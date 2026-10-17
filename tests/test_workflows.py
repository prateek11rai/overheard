import asyncio
import contextlib
import os
import uuid
from collections import defaultdict
from datetime import date

import pytest
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from overheard import pipeline
from overheard.pipeline import ProductPlan
from overheard.settings import TemporalNotFoundError, temporal_binary
from overheard.sources import Post
from overheard.workflows import (
    WORKFLOWS,
    Activities,
    FinishArgs,
    MorningBrief,
    MorningInput,
    PlanArgs,
    PlanResult,
    ProductScan,
    ScanArgs,
    SearchArgs,
    StepArgs,
)
from tests.conftest import add_product

QUEUE = "overheard-test"


@pytest.fixture
async def env():
    try:
        binary = temporal_binary(os.environ.get("OVERHEARD_TEMPORAL_BIN"))
    except TemporalNotFoundError as exc:
        pytest.fail(str(exc))
    e = await WorkflowEnvironment.start_local(
        dev_server_existing_path=str(binary), dev_server_log_level="error"
    )
    try:
        yield e
    finally:
        await e.shutdown()


class LedgerOnly:
    """Just the ledger part of the database, in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.uses: set[tuple[str, str]] = set()

    async def ledger_get(self, key):
        return self.rows.get(key)

    async def ledger_save(self, key, run_key, day, product_id, phrase, source, results):
        self.rows.setdefault(key, {"results": results})
        return True

    async def ledger_use(self, run_key, key):
        self.uses.add((run_key, key))


class FakeActivities:
    """Fake activities with the real names; search_reddit uses the real search ledger."""

    def __init__(self, phrases: list[str]) -> None:
        self.plan = ProductPlan(1, "mealwise", "Mealwise", phrases, phrases)
        self.db = LedgerOnly()
        self.paid: list[str] = []
        self.attempts: dict[str, int] = defaultdict(int)
        self.calls: list[str] = []
        self.steps: list[StepArgs] = []
        self.finished: FinishArgs | None = None
        self.on_search = {}
        self.fail: dict[str, str] = {}

    def all(self):
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
        ]

    @activity.defn(name="plan_morning")
    async def plan_morning(self, args: PlanArgs) -> PlanResult:
        if self.fail.get("plan_morning"):
            raise ApplicationError(self.fail["plan_morning"], non_retryable=True)
        p = ProductPlan(**{**self.plan.__dict__, "run_key": args.workflow_id, "run_id": 1})
        return PlanResult(1, args.workflow_id, [p], "every")

    @activity.defn(name="plan_check")
    async def plan_check(self, args: PlanArgs) -> PlanResult:
        p = ProductPlan(**{**self.plan.__dict__, "run_key": args.workflow_id, "run_id": 2})
        return PlanResult(2, args.workflow_id, [p], "every")

    @activity.defn(name="search_reddit")
    async def search_reddit(self, args: SearchArgs) -> int:
        attempt = activity.info().attempt
        self.attempts[args.phrase] = attempt

        async def serpapi() -> list[Post]:
            self.paid.append(args.phrase)
            return [
                Post(
                    "reddit",
                    f"https://www.reddit.com/r/x/comments/{args.phrase}/",
                    "t",
                    "b",
                    "r/x",
                    None,
                )
            ]

        hook = self.on_search.get(args.phrase)
        if hook and hook.get("before"):
            await hook["before"](attempt)
        posts, _ = await pipeline.search_once(
            self.db,
            run_key=args.run_key,
            product_id=args.product_id,
            phrase=args.phrase,
            source="reddit",
            day=date(2026, 10, 18),
            fetch=serpapi,
        )
        if hook and hook.get("after"):
            await hook["after"](attempt)
        return len(posts)

    async def _step(self, name: str) -> None:
        self.calls.append(name)

    @activity.defn(name="search_hn")
    async def search_hn(self, args: ScanArgs) -> int:
        await self._step("search_hn")
        if self.fail.get("search_hn"):
            raise ApplicationError(self.fail["search_hn"], non_retryable=True)
        return 0

    @activity.defn(name="save_posts")
    async def save_posts(self, args: ScanArgs) -> int:
        await self._step("save_posts")
        return 0

    @activity.defn(name="label_posts")
    async def label_posts(self, args: ScanArgs) -> int:
        await self._step("label_posts")
        return 0

    @activity.defn(name="update_themes")
    async def update_themes(self, args: ScanArgs) -> int:
        await self._step("update_themes")
        if self.fail.get("update_themes"):
            raise ApplicationError(self.fail["update_themes"], non_retryable=True)
        return 0

    @activity.defn(name="send_digest")
    async def send_digest(self, args: ScanArgs) -> str:
        await self._step("send_digest")
        return "sent"

    @activity.defn(name="record_step")
    async def record_step(self, args: StepArgs) -> None:
        self.steps.append(args)

    @activity.defn(name="finish_run")
    async def finish_run(self, args: FinishArgs) -> None:
        self.finished = args

    @activity.defn(name="reembed")
    async def reembed(self, args) -> int:
        raise ApplicationError("model server answered 500", non_retryable=True)


def worker(env, fake):
    return Worker(env.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=fake.all())


async def run_morning(env, fake):
    async with worker(env, fake):
        return await env.client.execute_workflow(
            MorningBrief.run, MorningInput(), id=f"morning-{uuid.uuid4()}", task_queue=QUEUE
        )


async def test_a_flaky_search_is_retried(env):
    fake = FakeActivities(["a", "b", "c"])

    async def flaky(attempt):
        if attempt < 3:
            raise RuntimeError("SerpApi timed out")

    fake.on_search["b"] = {"before": flaky}
    result = await run_morning(env, fake)
    assert result["status"] == "done"
    assert fake.attempts["b"] == 3
    assert fake.paid == ["a", "b", "c"]
    assert fake.calls == ["search_hn", "save_posts", "label_posts", "update_themes", "send_digest"]
    assert fake.finished.status == "done"


async def test_a_used_up_plan_falls_back_to_hacker_news_only(env):
    fake = FakeActivities(["a", "b", "c"])

    async def used_up(attempt):
        raise ApplicationError("no searches left", type="PlanExhausted", non_retryable=True)

    fake.on_search["a"] = {"before": used_up}
    result = await run_morning(env, fake)
    assert result["status"] == "hn_only"
    assert fake.attempts["a"] == 1
    assert "b" not in fake.attempts and "c" not in fake.attempts
    assert fake.paid == []
    assert "search_hn" in fake.calls and "send_digest" in fake.calls
    assert fake.steps[0].text == "Hacker News only · search plan used up"
    assert fake.finished.status == "hn_only"


async def test_check_now_runs_one_product_on_its_own(env):
    fake = FakeActivities(["a"])
    async with worker(env, fake):
        result = await env.client.execute_workflow(
            ProductScan.run, ScanArgs(1), id=f"check-{uuid.uuid4()}", task_queue=QUEUE
        )
    assert not result.reddit_used_up
    assert fake.paid == ["a"]
    assert fake.finished.kind == "check" and fake.finished.status == "done"


async def test_a_worker_restart_mid_run_spends_no_extra_search(env):
    fake = FakeActivities(["a", "b", "c"])
    crashed = asyncio.Event()

    async def die_after_paying(attempt):
        if attempt == 1:
            crashed.set()
            raise RuntimeError("worker died before reporting the result")

    fake.on_search["b"] = {"after": die_after_paying}
    wid = f"morning-{uuid.uuid4()}"
    async with worker(env, fake):
        handle = await env.client.start_workflow(
            MorningBrief.run, MorningInput(), id=wid, task_queue=QUEUE
        )
        await asyncio.wait_for(crashed.wait(), timeout=30)
    # Worker 1 is gone; a fresh worker picks the run up from history.
    async with worker(env, fake):
        result = await asyncio.wait_for(handle.result(), timeout=60)
    assert result["status"] == "done"
    assert fake.attempts["b"] >= 2
    assert sorted(fake.paid) == ["a", "b", "c"]
    assert fake.paid.count("b") == 1


async def test_demo_products_are_never_scanned(db, test_settings):
    await add_product(db, slug="real", name="Real")
    await add_product(db, slug="demo-mealwise", name="Mealwise", demo=True)
    await add_product(db, slug="paused", name="Paused", status="paused")
    acts = Activities(test_settings, db)
    plan = await acts.plan_morning(PlanArgs("wf-demo-test"))
    assert [p.slug for p in plan.products] == ["real"]
    assert plan.products[0].reddit_phrases == []  # no SerpApi key
    run = await db.run(plan.run_id)
    assert run.kind == "morning" and run.status == "running"


async def _wait_for_schedule(client, seconds: float = 20):
    from overheard.workflows import SCHEDULE_ID

    handle = client.get_schedule_handle(SCHEDULE_ID)
    end = asyncio.get_running_loop().time() + seconds
    while True:
        try:
            return await handle.describe()
        except Exception:
            if asyncio.get_running_loop().time() > end:
                raise
            await asyncio.sleep(0.2)


async def test_worker_start_creates_the_schedule_and_a_restart_leaves_it(env, db, test_settings):
    from overheard.workflows import ensure_schedule, run_worker

    target = env.client.service_client.config.target_host
    settings = test_settings.model_copy(update={"temporal_address": target})
    await db.set_setting("morning_time", "06:30")
    await db.set_setting("timezone", "Asia/Kolkata")
    task = asyncio.create_task(run_worker(settings))
    try:
        first = await _wait_for_schedule(env.client)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    cal = first.schedule.spec.calendars[0]
    assert (cal.hour[0].start, cal.minute[0].start) == (6, 30)
    assert first.schedule.spec.time_zone_name == "Asia/Kolkata"
    assert first.schedule.policy.catchup_window.total_seconds() == 12 * 3600
    assert await ensure_schedule(env.client, db) == "unchanged"
    again = await _wait_for_schedule(env.client)
    assert again.info.last_updated_at == first.info.last_updated_at
    await db.set_setting("morning_time", "07:15")
    assert await ensure_schedule(env.client, db) == "updated"


async def test_a_failing_check_closes_its_run_as_failed(env):
    fake = FakeActivities(["a"])
    fake.fail["search_hn"] = "Hacker News answered 503"
    async with worker(env, fake):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                ProductScan.run, ScanArgs(1), id=f"check-{uuid.uuid4()}", task_queue=QUEUE
            )
    assert fake.finished.kind == "check" and fake.finished.status == "failed"
    assert "Hacker News answered 503" in fake.finished.note


async def test_a_morning_run_that_fails_before_planning_is_closed(env):
    fake = FakeActivities(["a"])
    fake.fail["plan_morning"] = "database is down"
    async with worker(env, fake):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                MorningBrief.run, MorningInput(), id=f"morning-{uuid.uuid4()}", task_queue=QUEUE
            )
    assert fake.finished.kind == "morning" and fake.finished.status == "failed"
    assert "database is down" in fake.finished.note


async def test_a_failing_reembed_is_closed(env):
    from overheard.workflows import ModelArgs, Reembed

    fake = FakeActivities(["a"])
    async with worker(env, fake):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                Reembed.run,
                ModelArgs("nomic-embed-text"),
                id=f"reembed-{uuid.uuid4()}",
                task_queue=QUEUE,
            )
    assert fake.finished.kind == "reembed" and fake.finished.status == "failed"
    assert "500" in fake.finished.note


async def test_a_failed_start_leaves_no_running_row(db):
    from overheard.workflows import start_run_now

    class Down:
        async def start_workflow(self, *args, **kwargs):
            raise ConnectionError("temporal is gone")

        def get_workflow_handle(self, wid):
            raise AssertionError("no runs to check")

    with pytest.raises(ConnectionError):
        await start_run_now(Down(), db)
    assert await db.running_runs() == []
    [run] = await db.runs()
    assert run.status == "failed" and "couldn't start" in run.note


async def test_runs_temporal_no_longer_runs_are_marked_failed(db):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from temporalio.client import WorkflowExecutionStatus

    from overheard.workflows import live_runs

    old = await db.upsert_run("old-run", "morning")
    await db.upsert_run("young-run", "morning")
    live = await db.upsert_run("live-run", "morning")
    await db._exec(
        "UPDATE runs SET started_at = %s WHERE id IN (%s, %s)",
        (datetime.now(UTC) - timedelta(hours=1), old, live),
    )

    class Fake:
        def get_workflow_handle(self, wid):
            status = {
                "old-run": WorkflowExecutionStatus.TERMINATED,
                "young-run": None,
                "live-run": WorkflowExecutionStatus.RUNNING,
            }[wid]

            class H:
                async def describe(self):
                    if status is None:
                        from temporalio.service import RPCError, RPCStatusCode

                        raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
                    return SimpleNamespace(status=status)

            return H()

    alive = await live_runs(Fake(), db)
    assert sorted(r.workflow_id for r in alive) == ["live-run", "young-run"]
    stopped = await db.run(old)
    assert stopped.status == "failed" and stopped.note == "stopped: terminated"


@workflow.defn(sandboxed=False)
class SlowStep:
    @workflow.run
    async def run(self) -> str:
        from datetime import timedelta

        return await workflow.execute_activity(
            "slow_model_call",
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@activity.defn(name="slow_model_call")
async def slow_model_call() -> str:
    from overheard.workflows import heartbeating

    async with heartbeating(every=0.2):
        await asyncio.sleep(3)  # a chat call slower than the heartbeat timeout
    return "labelled"


async def test_a_slow_model_call_does_not_time_out_the_activity(env):
    async with Worker(
        env.client, task_queue=QUEUE, workflows=[SlowStep], activities=[slow_model_call]
    ):
        result = await env.client.execute_workflow(
            SlowStep.run, id=f"slow-{uuid.uuid4()}", task_queue=QUEUE
        )
    assert result == "labelled"


async def test_a_theming_failure_still_sends_the_digest(env):
    fake = FakeActivities(["a"])
    fake.fail["update_themes"] = "embedding model answered 500"
    result = await run_morning(env, fake)
    assert result["status"] == "done"
    assert fake.calls[-1] == "send_digest"
    failed = [s for s in fake.steps if s.status == "failed"]
    assert failed and failed[0].text.startswith("Themes · failed")
    assert "500" in failed[0].text
