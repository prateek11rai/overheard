import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    import httpx

    from overheard import pipeline, sources
    from overheard.ai import AI, ModelUnavailableError, role_models
    from overheard.db import Database
    from overheard.pipeline import PlanInput, ProductPlan
    from overheard.settings import Settings, local_timezone

TASK_QUEUE = "overheard"
SCHEDULE_ID = "morning-brief"
DEFAULT_TIME = "07:00"
PLAN_EXHAUSTED = "PlanExhausted"
SEARCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=4,
    non_retryable_error_types=[PLAN_EXHAUSTED],
)
STEP_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)


@dataclass
class MorningInput:
    """Input of the morning job."""

    reddit: bool = True
    trigger: str = "schedule"


@dataclass
class PlanArgs:
    """Input of the planning activities."""

    workflow_id: str
    reddit: bool = True
    product_id: int | None = None


@dataclass
class PlanResult:
    """The run row and what each product searches."""

    run_id: int
    run_key: str
    products: list[ProductPlan] = field(default_factory=list)
    mode: str = "off"
    reason: str = ""


@dataclass
class ScanArgs:
    """Input of ProductScan: a planned product, or just a product id for Check now."""

    product_id: int
    run_id: int = 0
    run_key: str = ""
    plan: ProductPlan | None = None


@dataclass
class SearchArgs:
    """One Reddit search."""

    run_id: int
    run_key: str
    product_id: int
    phrase: str
    subreddits: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """What one product's scan did."""

    product_id: int
    reddit_used_up: bool = False
    failed_searches: int = 0


@dataclass
class StepArgs:
    """A step recorded from workflow code."""

    run_id: int
    step_key: str
    text: str
    status: str = "ok"
    product_id: int | None = None


@dataclass
class FinishArgs:
    """How a run ended."""

    run_id: int
    status: str
    note: str = ""


@dataclass
class ModelArgs:
    """Input of Reembed and CompareRecent."""

    model: str
    limit: int = 50


def _used_up(err: BaseException) -> bool:
    cause = err.cause if isinstance(err, ActivityError) else err
    return isinstance(cause, ApplicationError) and cause.type == PLAN_EXHAUSTED


@workflow.defn
class ProductScan:
    """Search, save, label, theme and email for one product."""

    @workflow.run
    async def run(self, args: ScanArgs) -> ScanResult:
        """Scan one product; plans its own run when started by Check now.

        Args:
            args: The planned product, or only its id.

        Returns:
            What happened to the product's searches.
        """
        standalone = args.plan is None
        if standalone:
            planned = await workflow.execute_activity(
                "plan_check",
                PlanArgs(workflow.info().workflow_id, product_id=args.product_id),
                result_type=PlanResult,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=STEP_RETRY,
            )
            if not planned.products:
                await self._finish(planned.run_id, "done", planned.reason)
                return ScanResult(args.product_id)
            args = ScanArgs(args.product_id, planned.run_id, planned.run_key, planned.products[0])
        plan = args.plan
        if plan is None:
            raise ApplicationError("ProductScan needs a plan", non_retryable=True)
        result = ScanResult(plan.id)
        try:
            for phrase in plan.reddit_phrases:
                if result.reddit_used_up:
                    break
                try:
                    await workflow.execute_activity(
                        "search_reddit",
                        SearchArgs(args.run_id, args.run_key, plan.id, phrase, plan.subreddits),
                        start_to_close_timeout=timedelta(seconds=40),
                        retry_policy=SEARCH_RETRY,
                    )
                except ActivityError as err:
                    if not _used_up(err):
                        result.failed_searches += 1
                        continue
                    result.reddit_used_up = True
                    await workflow.execute_activity(
                        "record_step",
                        StepArgs(
                            args.run_id,
                            f"used-up:{plan.id}",
                            "Hacker News only · search plan used up",
                            "warn",
                            plan.id,
                        ),
                        start_to_close_timeout=timedelta(seconds=20),
                        retry_policy=STEP_RETRY,
                    )
            for name, timeout, heartbeat in (
                ("search_hn", timedelta(seconds=120), None),
                ("save_posts", timedelta(seconds=60), None),
                ("label_posts", timedelta(minutes=30), timedelta(seconds=90)),
                ("update_themes", timedelta(minutes=15), timedelta(seconds=90)),
                ("send_digest", timedelta(seconds=90), None),
            ):
                await workflow.execute_activity(
                    name,
                    args,
                    start_to_close_timeout=timeout,
                    heartbeat_timeout=heartbeat,
                    retry_policy=STEP_RETRY,
                )
        except Exception:
            if standalone:
                await self._finish(args.run_id, "failed", "see the steps below")
            raise
        if standalone:
            status = "hn_only" if result.reddit_used_up else "done"
            await self._finish(args.run_id, status, _note(result.reddit_used_up))
        return result

    async def _finish(self, run_id: int, status: str, note: str) -> None:
        await workflow.execute_activity(
            "finish_run",
            FinishArgs(run_id, status, note),
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=STEP_RETRY,
        )


def _note(used_up: bool) -> str:
    return "search plan used up" if used_up else ""


@workflow.defn
class MorningBrief:
    """The morning job: plan, then one ProductScan child per active product."""

    @workflow.run
    async def run(self, inp: MorningInput) -> dict[str, Any]:
        """Run every active product's scan.

        Args:
            inp: Whether Reddit is on and what started the run.

        Returns:
            A summary with the run id, status and products scanned.
        """
        info = workflow.info()
        plan = await workflow.execute_activity(
            "plan_morning",
            PlanArgs(info.workflow_id, reddit=inp.reddit),
            result_type=PlanResult,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=STEP_RETRY,
        )
        used_up = plan.mode == "used_up"
        failed = 0
        for p in plan.products:
            try:
                res = await workflow.execute_child_workflow(
                    ProductScan.run,
                    ScanArgs(p.id, plan.run_id, plan.run_key, p),
                    id=f"{info.workflow_id}/{p.slug}",
                    task_queue=info.task_queue,
                )
                used_up = used_up or res.reddit_used_up
            except ChildWorkflowError:
                failed += 1
        if failed and failed == len(plan.products):
            status, note = "failed", "see the steps below"
        else:
            status = "hn_only" if used_up else "done"
            note = _note(used_up) or plan.reason
            if failed:
                note = f"{failed} product{'s' if failed != 1 else ''} failed"
        await workflow.execute_activity(
            "finish_run",
            FinishArgs(plan.run_id, status, note),
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=STEP_RETRY,
        )
        return {"run_id": plan.run_id, "status": status, "products": len(plan.products)}


@workflow.defn
class Reembed:
    """Embed stored posts with a new embedding model, then rebuild theme centroids."""

    @workflow.run
    async def run(self, args: ModelArgs) -> int:
        """Re-embed every post for one model.

        Args:
            args: The embedding model.

        Returns:
            Number of posts embedded.
        """
        return await workflow.execute_activity(
            "reembed",
            args,
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(seconds=120),
            retry_policy=STEP_RETRY,
        )


@workflow.defn
class CompareRecent:
    """Label the last posts with another model without changing any label."""

    @workflow.run
    async def run(self, args: ModelArgs) -> dict[str, Any]:
        """Compare a model on recent real posts.

        Args:
            args: The model and how many posts.

        Returns:
            Agreement numbers.
        """
        return await workflow.execute_activity(
            "compare_recent",
            args,
            start_to_close_timeout=timedelta(hours=1),
            heartbeat_timeout=timedelta(seconds=120),
            retry_policy=STEP_RETRY,
        )


WORKFLOWS = [MorningBrief, ProductScan, Reembed, CompareRecent]


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


# client, schedule, worker


async def connect(settings: Settings):
    """Connect to Temporal.

    Args:
        settings: App settings.

    Returns:
        A Temporal client.
    """
    from temporalio.client import Client

    key = settings.temporal_api_key.get_secret_value() if settings.temporal_api_key else None
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        api_key=key,
        tls=bool(key),
    )


def parse_time(value: str) -> tuple[int, int]:
    """Parse `HH:MM`.

    Args:
        value: The time.

    Returns:
        (hour, minute).

    Raises:
        ValueError: If it isn't a valid time.
    """
    hour, _, minute = value.partition(":")
    h, m = int(hour), int(minute)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"not a time: {value}")
    return h, m


def build_schedule(time_of_day: str, tz: str, paused: bool):
    """The `morning-brief` schedule: daily at a time, skip overlaps, catch up 12 h late.

    Args:
        time_of_day: `HH:MM`.
        tz: IANA timezone.
        paused: Whether listening is paused.

    Returns:
        A Temporal `Schedule`.
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleCalendarSpec,
        ScheduleOverlapPolicy,
        SchedulePolicy,
        ScheduleRange,
        ScheduleSpec,
        ScheduleState,
    )

    h, m = parse_time(time_of_day)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            MorningBrief.run, MorningInput(), id=SCHEDULE_ID, task_queue=TASK_QUEUE
        ),
        spec=ScheduleSpec(
            calendars=[
                ScheduleCalendarSpec(
                    hour=[ScheduleRange(h)],
                    minute=[ScheduleRange(m)],
                    comment=f"daily at {time_of_day}",
                )
            ],
            time_zone_name=tz,
        ),
        policy=SchedulePolicy(
            overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(hours=12)
        ),
        state=ScheduleState(paused=paused, note="paused in Overheard" if paused else None),
    )


async def sync_schedule(client: Any, time_of_day: str, tz: str, paused: bool) -> None:
    """Create or update the `morning-brief` schedule.

    Args:
        client: Temporal client.
        time_of_day: `HH:MM`.
        tz: IANA timezone.
        paused: Whether listening is paused.
    """
    from temporalio.client import ScheduleAlreadyRunningError, ScheduleUpdate

    schedule = build_schedule(time_of_day, tz, paused)
    try:
        await client.create_schedule(SCHEDULE_ID, schedule)
        return
    except ScheduleAlreadyRunningError:
        pass
    handle = client.get_schedule_handle(SCHEDULE_ID)
    await handle.update(lambda _: ScheduleUpdate(schedule=schedule))
    desc = await handle.describe()
    if desc.schedule.state.paused != paused:
        if paused:
            await handle.pause(note="paused in Overheard")
        else:
            await handle.unpause(note="resumed in Overheard")


async def schedule_settings(db: Database) -> tuple[str, str, bool]:
    """The morning time, timezone and pause switch stored in the app.

    Args:
        db: Database.

    Returns:
        (time, timezone, paused).
    """
    t = await db.get_setting("morning_time") or DEFAULT_TIME
    tz = await db.get_setting("timezone") or local_timezone()
    paused = bool(await db.get_setting("paused_all", False))
    return t, tz, paused


async def sync_schedule_from_db(settings: Settings) -> None:
    """Make the Temporal schedule match what the app stores.

    Args:
        settings: App settings.
    """
    async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
        t, tz, paused = await schedule_settings(db)
        if await db.get_setting("timezone") is None:
            await db.set_setting("timezone", tz)
    client = await connect(settings)
    await sync_schedule(client, t, tz, paused)


async def temporal_status(settings: Settings) -> dict[str, Any]:
    """Whether Temporal answers, a worker polls the queue, and the schedule.

    Args:
        settings: App settings.

    Returns:
        Keys `temporal`, `worker` and `schedule` (a short description or None).
    """
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    out: dict[str, Any] = {"temporal": False, "worker": False, "schedule": None}
    try:
        client = await asyncio.wait_for(connect(settings), timeout=5)
    except Exception:
        return out
    out["temporal"] = True
    try:
        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=settings.temporal_namespace,
                task_queue=TaskQueue(name=TASK_QUEUE),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            )
        )
        out["worker"] = len(resp.pollers) > 0
    except Exception:
        out["worker"] = False
    try:
        desc = await client.get_schedule_handle(SCHEDULE_ID).describe()
        cal = desc.schedule.spec.calendars[0]
        hh, mm = cal.hour[0].start, cal.minute[0].start
        tz = desc.schedule.spec.time_zone_name or "UTC"
        state = "paused" if desc.schedule.state.paused else "on"
        out["schedule"] = f"{SCHEDULE_ID} · daily {hh:02d}:{mm:02d} {tz} · {state}"
    except Exception:
        out["schedule"] = None
    return out


async def start_run_now(client: Any, db: Database, reddit: bool = True) -> str:
    """Start the morning job by hand and show it in the Run log at once.

    Args:
        client: Temporal client.
        db: Database.
        reddit: Whether Reddit searches are on.

    Returns:
        The workflow id.
    """
    wid = f"run-now-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    await db.upsert_run(wid, "morning")
    await client.start_workflow(
        MorningBrief.run,
        MorningInput(reddit=reddit, trigger="manual"),
        id=wid,
        task_queue=TASK_QUEUE,
    )
    return wid


async def start_check(client: Any, db: Database, product_id: int, slug: str) -> str:
    """Start Check now for one product.

    Args:
        client: Temporal client.
        db: Database.
        product_id: Product id.
        slug: Product slug (for the workflow id).

    Returns:
        The workflow id.
    """
    wid = f"check-{slug}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    await db.upsert_run(wid, "check", product_id)
    await client.start_workflow(
        ProductScan.run, ScanArgs(product_id), id=wid, task_queue=TASK_QUEUE
    )
    return wid


async def start_reembed(client: Any, model: str) -> str:
    """Start re-embedding with a new embedding model.

    Args:
        client: Temporal client.
        model: The embedding model.

    Returns:
        The workflow id.
    """
    wid = f"reembed-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    await client.start_workflow(Reembed.run, ModelArgs(model), id=wid, task_queue=TASK_QUEUE)
    return wid


async def start_compare(client: Any, model: str, limit: int = 50) -> str:
    """Start comparing a model on the last posts.

    Args:
        client: Temporal client.
        model: The chat model to compare.
        limit: How many posts.

    Returns:
        The workflow id.
    """
    wid = f"compare-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    await client.start_workflow(
        CompareRecent.run, ModelArgs(model, limit), id=wid, task_queue=TASK_QUEUE
    )
    return wid


async def run_worker(settings: Settings) -> None:
    """Run the Temporal worker until interrupted.

    Args:
        settings: App settings.
    """
    from temporalio.worker import Worker

    async with Database(settings.dsn, settings.overheard_db_schema) as db:
        client = await connect(settings)
        acts = Activities(settings, db)
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=WORKFLOWS,
            activities=acts.all(),
            max_concurrent_activities=4,
        )
        print(f"worker polling {TASK_QUEUE} on {settings.temporal_address}", flush=True)
        await worker.run()


async def run_brief_now(settings: Settings, reddit: bool = True) -> int:
    """Run the morning job now and wait for it (needs a running worker).

    Args:
        settings: App settings.
        reddit: Whether Reddit searches are on.

    Returns:
        Exit code.
    """
    client = await connect(settings)
    async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
        wid = await start_run_now(client, db, reddit)
        print(f"started {wid}; waiting (Temporal UI: {settings.temporal_ui_url})")
        result = await client.get_workflow_handle(wid).result()
    print(f"{result['status']} · {result['products']} products")
    return 0 if result["status"] != "failed" else 1
