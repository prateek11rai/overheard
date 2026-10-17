import time
from datetime import UTC, datetime, timedelta

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    import httpx

    from overheard import pipeline, sources

from overheard.workflows.activities_base import ActivitiesBase
from overheard.workflows.types import (
    PLAN_EXHAUSTED,
    PlanArgs,
    PlanResult,
    ScanArgs,
    SearchArgs,
)


class SearchActivities(ActivitiesBase):
    """Planning, searching and saving."""

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
        plan = await self._plan(products, args.reddit, scheduled=args.trigger == "schedule")
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
        plan = await self._plan([product], True, scheduled=False)
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
                    day=await self._today(),
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
                        day=await self._today(),
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
