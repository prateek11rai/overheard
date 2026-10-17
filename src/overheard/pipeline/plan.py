import calendar
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from overheard.db import Database
from overheard.sources import Post as FoundPost
from overheard.sources import SerpApiAccount

RESERVE = 0.10


@dataclass
class PlanInput:
    """A product as the search plan sees it."""

    id: int
    slug: str
    name: str
    phrases: list[str]
    reddit: bool
    subreddits: list[str] = field(default_factory=list)


@dataclass
class ProductPlan:
    """What one product searches this morning (also the ProductScan workflow input)."""

    id: int
    slug: str
    name: str
    reddit_phrases: list[str]
    hn_phrases: list[str]
    subreddits: list[str] = field(default_factory=list)
    every_days: int = 1
    run_key: str = ""
    run_id: int = 0
    reddit_note: str = ""


@dataclass
class SearchPlan:
    """The morning's searches for every product."""

    products: list[ProductPlan]
    mode: Literal["every", "rotate", "off", "used_up"]
    per_day: int
    total_phrases: int
    reason: str = ""

    @property
    def reddit_searches(self) -> int:
        """Reddit searches this plan spends."""
        return sum(len(p.reddit_phrases) for p in self.products)


def days_left_in_month(today: date) -> int:
    """Days from today to the end of the month, today included.

    Args:
        today: The day.

    Returns:
        The number of days.
    """
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


def make_plan(
    products: Sequence[PlanInput],
    account: SerpApiAccount | None,
    today: date,
    *,
    used_today: int = 0,
    used_month: int = 0,
    last_searched: dict[tuple[int, str], date] | None = None,
    daily_cap: int | None = None,
    monthly_cap: int | None = None,
    reserve: float = RESERVE,
) -> SearchPlan:
    """Decide which phrases get a Reddit search this morning.

    One Reddit search per phrase per morning if phrases × days left fits the searches left
    (minus a reserve for Run now and first checks); otherwise phrases take turns, least
    recently searched first. Hacker News is free and always searches every phrase.

    Args:
        products: Active products.
        account: SerpApi plan numbers, or None without a key.
        today: The day of the run.
        used_today: Reddit searches already spent today.
        used_month: Reddit searches already spent this month.
        last_searched: When each (product id, phrase) was last searched.
        daily_cap: Optional hard cap per day.
        monthly_cap: Optional hard cap per month.
        reserve: Share of the plan kept back.

    Returns:
        The plan.
    """
    last_searched = last_searched or {}
    reddit_products = [p for p in products if p.reddit]
    total = sum(len(p.phrases) for p in reddit_products)

    def hn_only(mode: Literal["off", "used_up"], reason: str) -> SearchPlan:
        return SearchPlan(
            products=[
                ProductPlan(p.id, p.slug, p.name, [], list(p.phrases), list(p.subreddits))
                for p in products
            ],
            mode=mode,
            per_day=0,
            total_phrases=total,
            reason=reason,
        )

    if account is None:
        return hn_only("off", "no SerpApi key")
    available = account.searches_left - math.ceil(account.plan_searches * reserve)
    if monthly_cap is not None:
        available = min(available, monthly_cap - used_month)
    per_day = max(0, available) // days_left_in_month(today)
    if daily_cap is not None:
        per_day = min(per_day, max(0, daily_cap - used_today))
    if total and per_day <= 0:
        return hn_only("used_up", "search plan used up")

    if total <= per_day:
        chosen = {(p.id, ph) for p in reddit_products for ph in p.phrases}
        mode: Literal["every", "rotate"] = "every"
        every = 1
    else:
        pairs = []
        for p in reddit_products:
            for turn, ph in enumerate(p.phrases):
                pairs.append((last_searched.get((p.id, ph), date.min), turn, p.id, ph))
        pairs.sort(key=lambda x: (x[0], x[1], x[2]))
        chosen = {(pid, ph) for _, _, pid, ph in pairs[:per_day]}
        mode = "rotate"
        every = math.ceil(total / per_day)

    plans = []
    for p in products:
        if p.reddit:
            reddit = [ph for ph in p.phrases if (p.id, ph) in chosen]
            note = f"phrases checked every {every} days" if mode == "rotate" else ""
        else:
            reddit, note = [], "Hacker News only"
        plans.append(
            ProductPlan(
                p.id,
                p.slug,
                p.name,
                reddit,
                list(p.phrases),
                list(p.subreddits),
                every if p.reddit else 1,
                reddit_note=note,
            )
        )
    return SearchPlan(products=plans, mode=mode, per_day=per_day, total_phrases=total)


# search ledger


def ledger_key(run_key: str, product_id: int, source: str, phrase: str) -> str:
    """The idempotency key of one search in one run.

    Args:
        run_key: The run (workflow id).
        product_id: Product id.
        source: reddit or hn.
        phrase: The phrase.

    Returns:
        The key.
    """
    return f"{run_key}|{product_id}|{source}|{phrase.casefold()}"


async def search_once(
    db: Database,
    *,
    run_key: str,
    product_id: int,
    phrase: str,
    source: Literal["reddit", "hn"],
    day: date,
    fetch: Callable[[], Awaitable[list[FoundPost]]],
) -> tuple[list[FoundPost], bool]:
    """Run a search at most once per run: a retry or restart reuses the saved result.

    Args:
        db: Database.
        run_key: The run (workflow id).
        product_id: Product id.
        phrase: The phrase.
        source: reddit or hn.
        day: The day the search counts against.
        fetch: Does the actual search.

    Returns:
        (posts, whether a search was spent now).
    """
    key = ledger_key(run_key, product_id, source, phrase)
    saved = await db.ledger_get(key)
    if saved is not None:
        return [FoundPost.from_json(d) for d in saved["results"]], False
    posts = await fetch()
    await db.ledger_save(
        key, run_key, day, product_id, phrase, source, [p.to_json() for p in posts]
    )
    return posts, True
