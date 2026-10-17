import time
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    import httpx

    from overheard import pipeline, sources
    from overheard.ai import AI, ModelUnavailableError, role_models
    from overheard.db import Database
    from overheard.pipeline import PlanInput
    from overheard.settings import Settings, local_timezone

from overheard.workflows.types import (
    PLAN_EXHAUSTED,
    FinishArgs,
    ModelArgs,
    PlanArgs,
    PlanResult,
    ScanArgs,
    SearchArgs,
    StepArgs,
)


def _plan_input(p: Any, reddit: bool) -> PlanInput:
    return PlanInput(p.id, p.slug, p.name, list(p.phrases), reddit and p.reddit_on, p.subreddits)


class Activities:
    """The morning job's activities, sharing one database pool."""

    def __init__(self, settings: Settings, db: Database) -> None:
        """Create the activities.

        Args:
            settings: App settings.
            db: Open database.
        """
        self.settings = settings
        self.db = db

    def all(self) -> list:
        """Every activity, for the worker.

        Returns:
            Activity callables.
        """
        return [
            self.plan_morning,
            self.plan_check,
            self.search_reddit,
            self.search_hn,
            self.save_posts,
            self.label_posts,
            self.update_themes,
            self.send_digest,
            self.record_step,
            self.finish_run,
            self.reembed,
            self.compare_recent,
        ]

    async def _ai(self) -> AI:
        s = self.settings
        models = role_models(await self.db.get_setting("models"), s.chat_model, s.embed_model)
        key = s.llm_api_key.get_secret_value() if s.llm_api_key else None
        return AI(s.llm_base_url, models, key)

    async def _account(self, client: httpx.AsyncClient) -> sources.SerpApiAccount | None:
        s = self.settings
        if s.serpapi_api_key is None or s.overheard_offline:
            return None
        account = await sources.serpapi_account(client, s.serpapi_api_key.get_secret_value())
        await self.db.set_setting(
            "serpapi_account",
            {
                "plan_searches": account.plan_searches,
                "searches_left": account.searches_left,
                "this_month_usage": account.this_month_usage,
                "checked_at": datetime.now(UTC).isoformat(),
            },
        )
        return account

    async def _plan(self, products: list, reddit: bool) -> pipeline.SearchPlan:
        s = self.settings
        today = datetime.now(UTC).date()
        async with httpx.AsyncClient() as client:
            try:
                account = await self._account(client) if reddit else None
            except sources.SourceError:
                account = None
        return pipeline.make_plan(
            [_plan_input(p, reddit) for p in products],
            account,
            today,
            used_today=await self.db.reddit_used(today),
            used_month=await self.db.reddit_used(today.replace(day=1)),
            last_searched=await self.db.last_searched(),
            daily_cap=s.serpapi_daily_cap,
            monthly_cap=s.serpapi_monthly_cap,
        )

    async def _record_plan(self, run_id: int, plan: pipeline.SearchPlan) -> None:
        for p in plan.products:
            n = len(p.reddit_phrases)
            if plan.mode == "off":
                text = (
                    "Reddit off · no SerpApi key"
                    if not self.settings.overheard_offline
                    else ("Offline · no searches")
                )
            elif plan.mode == "used_up":
                text = "Hacker News only · search plan used up"
            else:
                text = f"{n} Reddit search{'es' if n != 1 else ''} planned"
                if p.reddit_note:
                    text += f" · {p.reddit_note}"
            status = "warn" if plan.mode == "used_up" else "ok"
            await self.db.add_step(run_id, f"plan:{p.id}", text, status, 0.1, 1, p.id)

    @activity.defn(name="plan_morning")
    async def plan_morning(self, args: PlanArgs) -> PlanResult:
        """Create the run row and plan every active real product's searches.

        Args:
            args: The workflow id and whether Reddit is on.

        Returns:
            The plan.
        """
        products = [p for p in await self.db.products() if not p.demo and not p.paused]
        if await self.db.get_setting("paused_all", False):
            products = []
        plan = await self._plan(products, args.reddit)
        run_id = await self.db.upsert_run(args.workflow_id, "morning", None, plan.reddit_searches)
        for p in plan.products:
            p.run_key, p.run_id = args.workflow_id, run_id
        await self._record_plan(run_id, plan)
        reason = plan.reason if plan.products else "nothing to check"
        return PlanResult(run_id, args.workflow_id, plan.products, plan.mode, reason)

    @activity.defn(name="plan_check")
    async def plan_check(self, args: PlanArgs) -> PlanResult:
        """Create the run row for Check now and plan that product's searches.

        Args:
            args: The workflow id and the product.

        Returns:
            The plan (no products for a demo or missing product).
        """
        product = await self.db.product(args.product_id or 0)
        run_id = await self.db.upsert_run(args.workflow_id, "check", args.product_id, 0)
        if product is None:
            return PlanResult(run_id, args.workflow_id, [], "off", "product not found")
        if product.demo:
            await self.db.add_step(
                run_id, "demo", "Demo product · not checked", "ok", 0, 1, product.id
            )
            return PlanResult(run_id, args.workflow_id, [], "off", "demo product · not checked")
        plan = await self._plan([product], True)
        await self.db.upsert_run(args.workflow_id, "check", product.id, plan.reddit_searches)
        for p in plan.products:
            p.run_key, p.run_id = args.workflow_id, run_id
        await self._record_plan(run_id, plan)
        return PlanResult(run_id, args.workflow_id, plan.products, plan.mode, plan.reason)

    @activity.defn(name="search_reddit")
    async def search_reddit(self, args: SearchArgs) -> int:
        """One Reddit search through SerpApi, at most once per run and phrase.

        Args:
            args: The run, product and phrase.

        Returns:
            Posts found.

        Raises:
            ApplicationError: `PlanExhausted` (not retried) when the plan is used up.
        """
        info = activity.info()
        started = time.monotonic()
        key = self.settings.serpapi_api_key
        if key is None or self.settings.overheard_offline:
            return 0
        async with httpx.AsyncClient() as client:

            async def fetch() -> list[sources.Post]:
                return await sources.search_reddit(
                    client, key.get_secret_value(), args.phrase, args.subreddits
                )

            try:
                posts, _ = await pipeline.search_once(
                    self.db,
                    run_key=args.run_key,
                    product_id=args.product_id,
                    phrase=args.phrase,
                    source="reddit",
                    day=datetime.now(UTC).date(),
                    fetch=fetch,
                )
            except sources.PlanExhaustedError as exc:
                raise ApplicationError(str(exc), type=PLAN_EXHAUSTED, non_retryable=True) from exc
        retried = info.attempt > 1
        text = (
            f"Reddit · {args.phrase}" + (" · retried" if retried else "") + f" · {len(posts)} posts"
        )
        await self.db.add_step(
            args.run_id,
            f"reddit:{args.product_id}:{args.phrase}",
            text,
            "retried" if retried else "ok",
            time.monotonic() - started,
            info.attempt,
            args.product_id,
        )
        return len(posts)

    @activity.defn(name="search_hn")
    async def search_hn(self, args: ScanArgs) -> int:
        """Search Hacker News for every phrase (free), once per run and phrase.

        Args:
            args: The planned product.

        Returns:
            Posts found.
        """
        plan = args.plan
        if plan is None:
            return 0
        info = activity.info()
        started = time.monotonic()
        step = f"hn:{plan.id}"
        if self.settings.overheard_offline:
            await self.db.add_step(
                args.run_id, step, "Hacker News · offline, skipped", "ok", 0, 1, plan.id
            )
            return 0
        last = await self.db.last_product_run(plan.id)
        since = datetime.now(UTC) - timedelta(days=1)
        if last and last["started_at"] < since:
            since = max(last["started_at"], datetime.now(UTC) - timedelta(days=7))
        total = 0
        async with httpx.AsyncClient() as client:
            for phrase in plan.hn_phrases:

                async def fetch(phrase: str = phrase) -> list[sources.Post]:
                    return await sources.search_hn(client, phrase, since)

                try:
                    posts, _ = await pipeline.search_once(
                        self.db,
                        run_key=args.run_key,
                        product_id=plan.id,
                        phrase=phrase,
                        source="hn",
                        day=datetime.now(UTC).date(),
                        fetch=fetch,
                    )
                except sources.SourceError as exc:
                    raise ApplicationError(str(exc)) from exc
                total += len(posts)
        n = len(plan.hn_phrases)
        retried = info.attempt > 1
        text = f"Hacker News · {n} phrase{'s' if n != 1 else ''}" + (
            " · retried" if retried else ""
        )
        await self.db.add_step(
            args.run_id,
            step,
            f"{text} · {total} posts",
            "retried" if retried else "ok",
            time.monotonic() - started,
            info.attempt,
            plan.id,
        )
        return total

    @activity.defn(name="save_posts")
    async def save_posts(self, args: ScanArgs) -> int:
        """Save this run's results by (product, URL); only new posts move on.

        Args:
            args: The planned product.

        Returns:
            New posts saved.
        """
        started = time.monotonic()
        results = await self.db.ledger_results(args.run_key, args.product_id)
        posts = [sources.Post.from_json(d) for d in results]
        new, seen = await self.db.upsert_posts(args.product_id, posts, args.run_id)
        await self.db.add_step(
            args.run_id,
            f"save:{args.product_id}",
            f"{new} new of {seen}",
            "ok",
            time.monotonic() - started,
            activity.info().attempt,
            args.product_id,
        )
        return new

    @activity.defn(name="label_posts")
    async def label_posts(self, args: ScanArgs) -> int:
        """Label new posts with the `label` model and check every quote.

        Args:
            args: The planned product.

        Returns:
            Posts labelled.

        Raises:
            ApplicationError: When the model server can't be reached (retried).
        """
        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return 0
        posts = await self.db.unlabelled_posts(product.id)
        rechecked = unverified = 0
        async with await self._ai() as ai:
            for i, post in enumerate(posts):
                activity.heartbeat(i)
                try:
                    out = await pipeline.label_post(
                        ai, product, post.title, post.body, post.community
                    )
                except ModelUnavailableError as exc:
                    raise ApplicationError(str(exc)) from exc
                rechecked += out.attempts > 1
                unverified += out.label == "unverified"
                await self.db.set_label(
                    post.id,
                    out.label,
                    out.quote,
                    out.competitor,
                    ai.model_for("label"),
                    out.attempts,
                )
        text = f"Labelled {len(posts)}"
        if rechecked:
            text += f" · {rechecked} quote{'s' if rechecked != 1 else ''} re-checked"
        if unverified:
            text += f" · {unverified} unverified"
        await self.db.add_step(
            args.run_id,
            f"label:{product.id}",
            text,
            "retried" if rechecked else "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return len(posts)

    @activity.defn(name="update_themes")
    async def update_themes(self, args: ScanArgs) -> int:
        """Embed new posts and put pain points into themes (new themes get a name).

        Args:
            args: The planned product.

        Returns:
            Themes started.
        """
        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return 0
        new_themes = 0
        async with await self._ai() as ai:
            model = ai.model_for("embed")
            try:
                while batch := await self.db.posts_without_embedding(model, product.id, 32):
                    activity.heartbeat(len(batch))
                    vectors = await ai.embed([pipeline.post_text(p.title, p.body) for p in batch])
                    if vectors:
                        await self.db.ensure_embedding_index(model, len(vectors[0]))
                    for p, v in zip(batch, vectors, strict=True):
                        await self.db.save_embedding(p.id, model, v)
                themes = await self.db.themes_for(product.id, model)
                for post in await self.db.unthemed_pain_posts(product.id, model):
                    activity.heartbeat(post.id)
                    vector = await self.db.embedding(post.id, model)
                    if vector is None:
                        continue
                    hit = pipeline.nearest_theme(vector, themes)
                    if hit is None:
                        name = await pipeline.name_theme(ai, product, [post.quote or post.title])
                        tid = await self.db.create_theme(product.id, name, model, vector)
                        await self.db.add_to_theme(post.id, tid, vector)
                        themes.append(
                            {"id": tid, "name": name, "centroid": vector, "post_count": 1}
                        )
                        new_themes += 1
                    else:
                        theme = hit[0]
                        centroid = pipeline.moved_centroid(
                            theme["centroid"], theme["post_count"], vector
                        )
                        await self.db.add_to_theme(post.id, theme["id"], centroid)
                        theme["centroid"], theme["post_count"] = centroid, theme["post_count"] + 1
            except ModelUnavailableError as exc:
                raise ApplicationError(str(exc)) from exc
        await self.db.add_step(
            args.run_id,
            f"themes:{product.id}",
            f"Themes · {new_themes} new",
            "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return new_themes

    @activity.defn(name="send_digest")
    async def send_digest(self, args: ScanArgs) -> str:
        """Email the product's digest (or write it to a file, or keep it in the app).

        Args:
            args: The planned product.

        Returns:
            What happened, in a few words.
        """
        from overheard import mail

        started = time.monotonic()
        product = await self.db.product(args.product_id)
        if product is None:
            return "product gone"
        tz = await self.db.get_setting("timezone") or local_timezone()
        brief = await pipeline.brief_for(self.db, product, tz)
        text = await mail.deliver(self.settings, product, brief, run_key=args.run_key)
        await self.db.add_step(
            args.run_id,
            f"email:{product.id}",
            text,
            "ok",
            time.monotonic() - started,
            activity.info().attempt,
            product.id,
        )
        return text

    @activity.defn(name="record_step")
    async def record_step(self, args: StepArgs) -> None:
        """Record a step decided in workflow code.

        Args:
            args: The step.
        """
        await self.db.add_step(
            args.run_id, args.step_key, args.text, args.status, 0, 1, args.product_id
        )

    @activity.defn(name="finish_run")
    async def finish_run(self, args: FinishArgs) -> None:
        """Mark the run finished.

        Args:
            args: Run id, status and note.
        """
        await self.db.finish_run(args.run_id, args.status, args.note)

    @activity.defn(name="reembed")
    async def reembed(self, args: ModelArgs) -> int:
        """Embed every stored post with `args.model` and rebuild theme centroids.

        Args:
            args: The embedding model.

        Returns:
            Posts embedded.

        Raises:
            ApplicationError: When the model server can't be reached (retried).
        """
        info = activity.info()
        started = time.monotonic()
        run_id = await self.db.upsert_run(info.workflow_id, "reembed")
        done = 0
        try:
            async with await self._ai() as ai:
                while batch := await self.db.posts_without_embedding(args.model, None, 64):
                    activity.heartbeat(done)
                    vectors = await ai.embed(
                        [pipeline.post_text(p.title, p.body) for p in batch], model=args.model
                    )
                    await self.db.ensure_embedding_index(args.model, len(vectors[0]))
                    for p, v in zip(batch, vectors, strict=True):
                        await self.db.save_embedding(p.id, args.model, v)
                    done += len(batch)
        except ModelUnavailableError as exc:
            raise ApplicationError(str(exc)) from exc
        themes = await self.db.recompute_centroids(args.model)
        await self.db.add_step(
            run_id,
            "reembed",
            f"Re-embedded {done} posts with {args.model} · {themes} themes updated",
            "ok",
            time.monotonic() - started,
            info.attempt,
        )
        await self.db.finish_run(run_id, "done", args.model)
        return done

    @activity.defn(name="compare_recent")
    async def compare_recent(self, args: ModelArgs) -> dict[str, Any]:
        """Label recent real posts with another model, without changing any label.

        Args:
            args: The model and how many posts.

        Returns:
            Agreement numbers.
        """
        from overheard import evals

        info = activity.info()
        run_id = await self.db.upsert_run(info.workflow_id, "compare")
        async with await self._ai() as ai:
            result = await evals.compare_recent(
                self.db, ai, args.model, args.limit, heartbeat=activity.heartbeat
            )
        await self.db.add_step(
            run_id,
            "compare",
            f"{args.model} agreed on {result['agree']} of {result['items']} recent posts",
            "ok",
            result["seconds"],
            info.attempt,
        )
        await self.db.finish_run(run_id, "done", args.model)
        return result
