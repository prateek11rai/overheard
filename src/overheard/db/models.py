import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pgvector import Vector

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
USEFUL_LABELS = ("ask", "pain", "you", "rival")
HIDDEN_LABELS = ("noise", "unverified")

PRODUCT_COLS = (
    "id, slug, name, description, phrases, competitors, subreddits, sources, recipients, "
    "quiet_day_email, status, demo, created_at"
)
POST_COLS = (
    "p.id, p.product_id, p.source, p.url, p.title, p.body, p.community, p.posted_at, p.found_at, "
    "p.label, p.quote, p.competitor, p.status, p.snoozed_until, p.theme_id, p.reply_draft, "
    "p.label_attempts, p.is_comment"
)
OPEN_FILTER = (
    "p.label IN ('ask', 'pain', 'you', 'rival') "
    "AND p.found_at >= now() - interval '7 days' "
    "AND (p.status = 'new' OR (p.status = 'skipped' AND p.snoozed_until <= now()))"
)
LABEL_ORDER = "CASE p.label WHEN 'ask' THEN 0 WHEN 'pain' THEN 1 WHEN 'you' THEN 2 ELSE 3 END"


@dataclass
class Product:
    """A product Overheard listens for."""

    id: int
    slug: str
    name: str
    description: str
    phrases: list[str]
    competitors: list[str]
    subreddits: list[str]
    sources: str
    recipients: list[str]
    quiet_day_email: bool
    status: str
    demo: bool
    created_at: datetime

    @property
    def paused(self) -> bool:
        """Whether the product is paused."""
        return self.status == "paused"

    @property
    def reddit_on(self) -> bool:
        """Whether Reddit is one of the product's sources."""
        return self.sources == "reddit+hn"


@dataclass
class Post:
    """A post found for a product."""

    id: int
    product_id: int
    source: str
    url: str
    title: str
    body: str
    community: str
    posted_at: datetime | None
    found_at: datetime
    label: str | None
    quote: str | None
    competitor: str | None
    status: str
    snoozed_until: datetime | None
    theme_id: int | None
    reply_draft: str | None
    label_attempts: int
    is_comment: bool = False


@dataclass
class Run:
    """One run of the morning job, a check, a re-embed or a comparison."""

    id: int
    workflow_id: str
    kind: str
    product_id: int | None
    status: str
    note: str
    reddit_planned: int
    searches_used: int
    new_posts: int
    demo: bool
    started_at: datetime
    finished_at: datetime | None
    product_name: str | None = None


@dataclass
class Step:
    """One step of a run, usually one Temporal activity."""

    id: int
    run_id: int
    product_id: int | None
    step_key: str
    text: str
    status: str
    seconds: float
    attempts: int
    created_at: datetime


@dataclass
class ThemeStat:
    """A theme with its post counts."""

    id: int
    name: str
    first_seen: datetime
    total: int
    posts_4w: int
    weekly: list[int] = field(default_factory=list)


def _vec(values: Sequence[float]) -> Vector:
    return Vector([float(x) for x in values])


def _floats(value: Any) -> list[float]:
    items = value.to_list() if hasattr(value, "to_list") else value
    return [float(x) for x in items]


def _id(row: dict | None) -> int:
    if row is None:
        raise RuntimeError("insert returned no row")
    return row["id"]


def index_name(model: str) -> str:
    """Name of the HNSW index for one embedding model.

    Args:
        model: The embedding model name.

    Returns:
        A stable, safe index name.
    """
    return "post_embeddings_" + hashlib.sha1(model.encode(), usedforsecurity=False).hexdigest()[:12]
