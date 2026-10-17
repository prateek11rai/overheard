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
