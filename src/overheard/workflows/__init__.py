import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

from overheard.workflows.types import (
    DEFAULT_TIME,
    PLAN_EXHAUSTED,
    SCHEDULE_ID,
    SEARCH_RETRY,
    STEP_RETRY,
    TASK_QUEUE,
    FinishArgs,
    ModelArgs,
    MorningInput,
    PlanArgs,
    PlanResult,
    ScanArgs,
    ScanResult,
    SearchArgs,
    StepArgs,
)


def _used_up(err: BaseException) -> bool:
    cause = err.cause if isinstance(err, ActivityError) else err
    return isinstance(cause, ApplicationError) and cause.type == PLAN_EXHAUSTED


def _reason(err: BaseException) -> str:
    cause: BaseException = err
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause  # type: ignore[assignment]
    text = str(cause) or type(cause).__name__
    return text[:160]


async def _finish(kind: str, status: str, note: str, product_id: int | None = None) -> None:
    """Close this workflow's run row; never hides the error that ended the run."""
    try:
        await workflow.execute_activity(
            "finish_run",
            FinishArgs(workflow.info().workflow_id, kind, status, note, product_id),
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=STEP_RETRY,
        )
    except Exception as exc:
        workflow.logger.warning("could not finish the run row: %s", _reason(exc))


def _note(used_up: bool) -> str:
    return "search plan used up" if used_up else ""


@workflow.defn
class ProductScan:
    """Search, save, label, theme and email for one product."""

    @workflow.run
    async def run(self, args: ScanArgs) -> ScanResult:
        """Scan one product; plans and closes its own run when started by Check now.

        Args:
            args: The planned product, or only its id.

        Returns:
            What happened to the product's searches.
        """
        standalone = args.plan is None
        status, note = "failed", "stopped before planning"
        try:
            if standalone:
                planned = await workflow.execute_activity(
                    "plan_check",
                    PlanArgs(workflow.info().workflow_id, product_id=args.product_id),
                    result_type=PlanResult,
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=STEP_RETRY,
                )
                if not planned.products:
                    status, note = "done", planned.reason
                    return ScanResult(args.product_id)
                args = ScanArgs(
                    args.product_id, planned.run_id, planned.run_key, planned.products[0]
                )
            result = await self._scan(args)
            status = "hn_only" if result.reddit_used_up else "done"
            note = _note(result.reddit_used_up)
            return result
        except asyncio.CancelledError:
            note = "cancelled"
            raise
        except Exception as exc:
            note = _reason(exc)
            raise
        finally:
            if standalone:
                await _finish("check", status, note, args.product_id)

    async def _scan(self, args: ScanArgs) -> ScanResult:
        plan = args.plan
        if plan is None:
            raise ApplicationError("ProductScan needs a plan", non_retryable=True)
        result = ScanResult(plan.id)
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
        return result


@workflow.defn
class MorningBrief:
    """The morning job: plan, then one ProductScan child per active product."""

    @workflow.run
    async def run(self, inp: MorningInput) -> dict[str, Any]:
        """Run every active product's scan; the run row is closed however it ends.

        Args:
            inp: Whether Reddit is on and what started the run.

        Returns:
            A summary with the run id, status and products scanned.
        """
        info = workflow.info()
        status, note = "failed", "stopped before planning"
        try:
            plan = await workflow.execute_activity(
                "plan_morning",
                PlanArgs(info.workflow_id, reddit=inp.reddit, trigger=inp.trigger),
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
            return {"run_id": plan.run_id, "status": status, "products": len(plan.products)}
        except asyncio.CancelledError:
            note = "cancelled"
            raise
        except Exception as exc:
            note = _reason(exc)
            raise
        finally:
            await _finish("morning", status, note)


@workflow.defn
class Reembed:
    """Embed stored posts with a new embedding model, then rebuild theme centroids."""

    @workflow.run
    async def run(self, args: ModelArgs) -> int:
        """Re-embed every post for one model; the run row is closed however it ends.

        Args:
            args: The embedding model.

        Returns:
            Number of posts embedded.
        """
        status, note = "failed", "stopped"
        try:
            done = await workflow.execute_activity(
                "reembed",
                args,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(seconds=120),
                retry_policy=STEP_RETRY,
            )
            status, note = "done", args.model
            return done
        except asyncio.CancelledError:
            note = "cancelled"
            raise
        except Exception as exc:
            note = _reason(exc)
            raise
        finally:
            await _finish("reembed", status, note)


@workflow.defn
class CompareRecent:
    """Label the last posts with another model without changing any label."""

    @workflow.run
    async def run(self, args: ModelArgs) -> dict[str, Any]:
        """Compare a model on recent real posts; the run row is closed however it ends.

        Args:
            args: The model and how many posts.

        Returns:
            Agreement numbers.
        """
        status, note = "failed", "stopped"
        try:
            result = await workflow.execute_activity(
                "compare_recent",
                args,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(seconds=120),
                retry_policy=STEP_RETRY,
            )
            status, note = "done", args.model
            return result
        except asyncio.CancelledError:
            note = "cancelled"
            raise
        except Exception as exc:
            note = _reason(exc)
            raise
        finally:
            await _finish("compare", status, note)


WORKFLOWS = [MorningBrief, ProductScan, Reembed, CompareRecent]


# Activities and the client helpers run outside the workflow sandbox.
with workflow.unsafe.imports_passed_through():
    from overheard.workflows.activities import Activities
    from overheard.workflows.runtime import (
        AlreadyRunningError,
        build_schedule,
        connect,
        ensure_schedule,
        live_runs,
        parse_time,
        run_brief_now,
        run_worker,
        schedule_settings,
        start_check,
        start_compare,
        start_reembed,
        start_run_now,
        sync_schedule,
        sync_schedule_from_db,
        temporal_status,
    )

__all__ = [
    "AlreadyRunningError",
    "live_runs",
    "Activities",
    "CompareRecent",
    "DEFAULT_TIME",
    "FinishArgs",
    "ModelArgs",
    "MorningBrief",
    "MorningInput",
    "PLAN_EXHAUSTED",
    "PlanArgs",
    "PlanResult",
    "ProductScan",
    "Reembed",
    "SCHEDULE_ID",
    "SEARCH_RETRY",
    "STEP_RETRY",
    "ScanArgs",
    "ScanResult",
    "SearchArgs",
    "StepArgs",
    "TASK_QUEUE",
    "WORKFLOWS",
    "build_schedule",
    "connect",
    "ensure_schedule",
    "parse_time",
    "run_brief_now",
    "run_worker",
    "schedule_settings",
    "start_check",
    "start_compare",
    "start_reembed",
    "start_run_now",
    "sync_schedule",
    "sync_schedule_from_db",
    "temporal_status",
]
