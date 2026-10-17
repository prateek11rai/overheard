import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from overheard.db import Database
    from overheard.settings import Settings, local_timezone

from overheard.workflows import WORKFLOWS, CompareRecent, MorningBrief, ProductScan, Reembed
from overheard.workflows.activities import Activities
from overheard.workflows.types import (
    DEFAULT_TIME,
    SCHEDULE_ID,
    TASK_QUEUE,
    ModelArgs,
    MorningInput,
    ScanArgs,
)


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


def _schedule_shape(schedule: Any) -> tuple:
    cal = schedule.spec.calendars[0] if schedule.spec.calendars else None
    action = schedule.action
    return (
        cal.hour[0].start if cal and cal.hour else None,
        cal.minute[0].start if cal and cal.minute else None,
        schedule.spec.time_zone_name or "UTC",
        bool(schedule.state.paused),
        int(schedule.policy.catchup_window.total_seconds()),
        schedule.policy.overlap,
        getattr(action, "workflow", None),
        getattr(action, "task_queue", None),
    )


async def _describe_new(handle: Any, seconds: float = 5) -> Any:
    from temporalio.service import RPCError, RPCStatusCode

    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while True:
        try:
            return await handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND or loop.time() > end:
                raise
            await asyncio.sleep(0.2)


async def sync_schedule(client: Any, time_of_day: str, tz: str, paused: bool) -> str:
    """Create the `morning-brief` schedule, or update it only if it differs.

    Safe when two processes sync at once (`start` and the worker on a fresh Temporal): the
    one whose create loses the race updates the schedule instead.

    Args:
        client: Temporal client.
        time_of_day: `HH:MM`.
        tz: IANA timezone.
        paused: Whether listening is paused.

    Returns:
        "created", "updated" or "unchanged".
    """
    from temporalio.client import ScheduleAlreadyRunningError, ScheduleUpdate
    from temporalio.service import RPCError, RPCStatusCode

    wanted = build_schedule(time_of_day, tz, paused)
    handle = client.get_schedule_handle(SCHEDULE_ID)
    try:
        desc = await handle.describe()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        try:
            await client.create_schedule(SCHEDULE_ID, wanted)
            return "created"
        except ScheduleAlreadyRunningError:
            desc = await _describe_new(handle)
    current = _schedule_shape(desc.schedule)
    target = _schedule_shape(wanted)
    if current == target:
        return "unchanged"
    if current[:3] + current[4:] != target[:3] + target[4:]:
        await handle.update(lambda _: ScheduleUpdate(schedule=wanted))
    desc = await handle.describe()
    if desc.schedule.state.paused != paused:
        if paused:
            await handle.pause(note="paused in Overheard")
        else:
            await handle.unpause(note="resumed in Overheard")
    return "updated"


async def ensure_schedule(client: Any, db: Database, default_tz: str | None = None) -> str:
    """Make the schedule match the time, timezone and pause switch stored in the app.

    Safe to call on every start: an unchanged schedule is left alone.

    Args:
        client: Temporal client.
        db: Database.
        default_tz: Timezone when none is stored yet.

    Returns:
        "created", "updated" or "unchanged".
    """
    t, tz, paused = await schedule_settings(db, default_tz)
    if await db.get_setting("timezone") is None:
        await db.set_setting("timezone", tz)
    return await sync_schedule(client, t, tz, paused)


async def schedule_settings(db: Database, default_tz: str | None = None) -> tuple[str, str, bool]:
    """The morning time, timezone and pause switch stored in the app.

    Args:
        db: Database.
        default_tz: Timezone when none is stored (`OVERHEARD_TIMEZONE` or the computer's).

    Returns:
        (time, timezone, paused).
    """
    t = await db.get_setting("morning_time") or DEFAULT_TIME
    tz = await db.get_setting("timezone") or default_tz or local_timezone()
    paused = bool(await db.get_setting("paused_all", False))
    return t, tz, paused


async def sync_schedule_from_db(settings: Settings) -> None:
    """Make the Temporal schedule match what the app stores.

    Args:
        settings: App settings.
    """
    client = await connect(settings)
    async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
        await ensure_schedule(client, db, settings.default_timezone())


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


class AlreadyRunningError(RuntimeError):
    """A run that would overlap is still going."""


STALE_GRACE = timedelta(seconds=60)


async def live_runs(client: Any, db: Database) -> list[Any]:
    """Runs marked running that Temporal is really still running.

    A row whose workflow Temporal no longer runs (finished without updating the row,
    terminated, or unknown) is marked failed on the way. Rows younger than a minute are
    trusted, since their workflow may be starting right now.

    Args:
        client: Temporal client.
        db: Database.

    Returns:
        The live runs.
    """
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    live = []
    now = datetime.now(UTC)
    for run in await db.running_runs():
        try:
            desc = await client.get_workflow_handle(run.workflow_id).describe()
            status = desc.status
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
            status = None
        if (
            status == WorkflowExecutionStatus.RUNNING
            or status is None
            and now - run.started_at < STALE_GRACE
        ):
            live.append(run)
        else:
            name = status.name.lower() if status is not None else "unknown to Temporal"
            await db.finish_run(run.id, "failed", f"stopped: {name}")
    return live


async def start_run_now(client: Any, db: Database, reddit: bool = True) -> str:
    """Start the morning job by hand and show it in the Run log at once.

    Args:
        client: Temporal client.
        db: Database.
        reddit: Whether Reddit searches are on.

    Returns:
        The workflow id.

    Raises:
        AlreadyRunningError: If a morning run or a check is still going.
    """
    if any(r.kind in ("morning", "check") for r in await live_runs(client, db)):
        raise AlreadyRunningError("a run is still going")
    wid = f"run-now-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    run_id = await db.upsert_run(wid, "morning")
    try:
        await client.start_workflow(
            MorningBrief.run,
            MorningInput(reddit=reddit, trigger="manual"),
            id=wid,
            task_queue=TASK_QUEUE,
        )
    except Exception as exc:
        await db.finish_run(run_id, "failed", f"couldn't start: {type(exc).__name__}")
        raise
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

    Raises:
        AlreadyRunningError: If a morning run or a check of this product is still going.
    """
    for r in await live_runs(client, db):
        if r.kind == "morning" or (r.kind == "check" and r.product_id == product_id):
            raise AlreadyRunningError("a run is still going")
    wid = f"check-{slug}-{datetime.now(UTC):%Y%m%dT%H%M%S%f}"
    run_id = await db.upsert_run(wid, "check", product_id)
    try:
        await client.start_workflow(
            ProductScan.run, ScanArgs(product_id), id=wid, task_queue=TASK_QUEUE
        )
    except Exception as exc:
        await db.finish_run(run_id, "failed", f"couldn't start: {type(exc).__name__}")
        raise
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
        result = await ensure_schedule(client, db, settings.default_timezone())
        print(f"schedule {SCHEDULE_ID}: {result}", flush=True)
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
