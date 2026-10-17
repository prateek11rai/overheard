from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from overheard.pipeline import ProductPlan


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
