import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import httpx

    from overheard import pipeline, sources
    from overheard.ai import AI, role_models
    from overheard.db import Database
    from overheard.pipeline import PlanInput
    from overheard.settings import Settings

from overheard.workflows.types import (
    FinishArgs,
    StepArgs,
)

HEARTBEAT_EVERY = 10.0


@contextlib.asynccontextmanager
async def heartbeating(every: float = HEARTBEAT_EVERY) -> AsyncIterator[None]:
    """Heartbeat from a background task while the block runs.

    A single chat call to a slow local model can take longer than the heartbeat timeout;
    this keeps Temporal from treating the activity as dead while it waits.

    Args:
        every: Seconds between heartbeats.

    Yields:
        Nothing; the block runs with heartbeats in the background.
    """

    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(every)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _plan_input(p: Any, reddit: bool) -> PlanInput:
    return PlanInput(p.id, p.slug, p.name, list(p.phrases), reddit and p.reddit_on, p.subreddits)


class ActivitiesBase:
    """Shared state and helpers of the activities: settings, the database, models, plans."""

    def __init__(self, settings: Settings, db: Database) -> None:
        """Create the activities.

        Args:
            settings: App settings.
            db: Open database.
        """
        self.settings = settings
        self.db = db

    async def _today(self) -> date:
        tz = await self.db.get_setting("timezone") or self.settings.default_timezone()
        return datetime.now(ZoneInfo(tz)).date()

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

    async def _plan(
        self, products: list, reddit: bool, scheduled: bool = True
    ) -> pipeline.SearchPlan:
        s = self.settings
        today = await self._today()
        async with httpx.AsyncClient() as client:
            try:
                account = await self._account(client) if reddit else None
            except sources.SourceError:
                account = None
        plan = pipeline.make_plan(
            [_plan_input(p, reddit) for p in products],
            account,
            today,
            used_today=await self.db.reddit_used(today),
            used_month=await self.db.reddit_used(today.replace(day=1)),
            last_searched=await self.db.last_searched(),
            daily_cap=s.serpapi_daily_cap,
            monthly_cap=s.serpapi_monthly_cap,
            scheduled=scheduled,
        )
        for p in plan.products:
            if await self.db.last_product_run(p.id) is None:
                p.window = "w"
        return plan

    async def _record_plan(self, run_id: int, plan: pipeline.SearchPlan) -> None:
        today = await self._today()
        last = await self.db.last_searched()
        for p in plan.products:
            n = len(p.reddit_phrases)
            reused = sum(1 for ph in p.reddit_phrases if last.get((p.id, ph)) == today)
            if plan.mode == "off":
                text = (
                    "Reddit off · no SerpApi key"
                    if not self.settings.overheard_offline
                    else ("Offline · no searches")
                )
            elif plan.mode == "used_up":
                text = "Hacker News only · search plan used up"
            elif n and reused == n:
                text = f"{n} Reddit phrase{'s' if n != 1 else ''} · already searched today, reused"
            else:
                new = n - reused
                text = f"{new} Reddit search{'es' if new != 1 else ''} planned"
                if reused:
                    text += f" · {reused} reused from today"
                if p.reddit_note:
                    text += f" · {p.reddit_note}"
            status = "warn" if plan.mode == "used_up" else "ok"
            await self.db.add_step(run_id, f"plan:{p.id}", text, status, 0.1, 1, p.id)

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
        """Mark the run finished (making its row if the run failed before it had one).

        Args:
            args: Workflow id, kind, status and note.
        """
        run_id = await self.db.upsert_run(args.workflow_id, args.kind, args.product_id)
        await self.db.finish_run(run_id, args.status, args.note)
