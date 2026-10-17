from dataclasses import dataclass
from datetime import UTC, datetime

from overheard.db import Database, Post, Product, ThemeStat
from overheard.pipeline.quotes import CONTEXT_CHARS, locate_quote, post_text, verify_quote

LABEL_TEXT = {
    "ask": "asking for a tool",
    "pain": "pain point",
    "you": "you",
    "rival": "competitor",
    "noise": "off-topic",
    "unverified": "unverified",
}


@dataclass
class Card:
    """A post as the brief and review mode show it."""

    post: Post
    label_css: str
    label_text: str
    age: str
    before: str
    mark: str
    after: str
    checked: bool


@dataclass
class QuoteLine:
    """One quote under a theme or in Mentions."""

    post_id: int
    quote: str
    url: str
    community: str
    label_css: str = ""
    label_text: str = ""


@dataclass
class ThemeBlock:
    """A pain-point theme in the brief."""

    theme_id: int | None
    name: str
    linkable: bool
    trend: str
    trend_text: str
    lines: list[QuoteLine]


@dataclass
class Brief:
    """Everything the brief page and the email show for one product."""

    product: Product
    headline: str
    subline: str
    total: int
    cards: list[Card]
    worth_a_reply: list[Card]
    pain_count: int
    pain_points: list[ThemeBlock]
    mentions: list[QuoteLine]
    hidden: int
    status: str
    order: list[int]


def age_text(then: datetime | None, now: datetime) -> str:
    """Short age such as `9h`, `2d` or `35m`.

    Args:
        then: When it was posted (or found).
        now: Now.

    Returns:
        The age.
    """
    if then is None:
        return ""
    secs = max(0, (now - then).total_seconds())
    if secs < 3600:
        return f"{max(1, int(secs // 60))}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def label_text(post: Post) -> str:
    """The label as the UI shows it (a competitor shows its name).

    Args:
        post: The post.

    Returns:
        The label text.
    """
    if post.label == "rival" and post.competitor:
        return post.competitor
    return LABEL_TEXT.get(post.label or "", "")


def card_for(post: Post, now: datetime) -> Card:
    """Build the card for one post, with the checked quote highlighted.

    Args:
        post: The post.
        now: Now (for its age).

    Returns:
        The card.
    """
    text = post.body or post.title
    span = locate_quote(post.quote or "", text) if post.quote else None
    if span:
        before, mark, after = text[: span[0]], text[span[0] : span[1]], text[span[1] :]
    else:
        before, mark, after = text, "", ""
    if len(before) > CONTEXT_CHARS:
        before = "…" + before[-CONTEXT_CHARS:].lstrip()
    if len(after) > CONTEXT_CHARS:
        after = after[:CONTEXT_CHARS].rstrip() + "…"
    checked = bool(post.quote) and verify_quote(
        post.quote, post_text(post.title, post.body, post.is_comment)
    )
    return Card(
        post=post,
        label_css=post.label or "",
        label_text=label_text(post),
        age=age_text(post.posted_at or post.found_at, now),
        before=before,
        mark=mark,
        after=after,
        checked=checked,
    )


def theme_trend(stat: ThemeStat | None, now: datetime) -> tuple[str, str]:
    """Whether a theme is new, growing or steady.

    `new` means first seen in the last 7 days; `up` means this week is above the average of
    the four weeks before.

    Args:
        stat: The theme's counts.
        now: Now.

    Returns:
        (trend, short text) such as ("up", "↑ 14 posts"), ("new", "new") or ("", "").
    """
    if stat is None:
        return "", ""
    if (now - stat.first_seen).days < 7:
        return "new", "new"
    this_week = stat.weekly[0] if stat.weekly else 0
    before = stat.weekly[1:5] if len(stat.weekly) > 1 else []
    avg = sum(before) / len(before) if before else 0
    if this_week >= 2 and this_week > avg:
        return "up", f"↑ {stat.posts_4w} posts"
    return "", ""


def growth(stat: ThemeStat) -> float:
    """How fast a theme grows: this week over the four weeks before.

    Args:
        stat: The theme's counts.

    Returns:
        A ratio for sorting (higher grows faster).
    """
    this_week = stat.weekly[0] if stat.weekly else 0
    before = stat.weekly[1:5]
    avg = sum(before) / len(before) if before else 0
    return this_week / (avg or 0.5)


def headline(product: Product, asks: int, pains: int, mentions: int) -> str:
    """The brief's headline: the most useful count for this product.

    Args:
        product: The product.
        asks: Posts asking for a tool.
        pains: Pain points.
        mentions: Mentions of the product or a competitor.

    Returns:
        One sentence.
    """
    name = product.name
    if asks:
        who = "1 person" if asks == 1 else f"{asks} people"
        return f"{who} asked for something like {name}."
    if pains:
        who = "1 person" if pains == 1 else f"{pains} people"
        verb = "describes" if pains == 1 else "describe"
        return f"{who} {verb} the problem {name} solves."
    if mentions:
        return f"{mentions} new mention{'s' if mentions != 1 else ''} of {name} or its rivals."
    return f"Nothing new about {name} since yesterday."


def part_of_day(when: datetime) -> str:
    """Morning, afternoon or evening.

    Args:
        when: A local time.

    Returns:
        The word.
    """
    return "morning" if when.hour < 12 else "afternoon" if when.hour < 18 else "evening"


async def brief_for(
    db: Database,
    product: Product,
    tz: str = "UTC",
    now: datetime | None = None,
    since: datetime | None = None,
) -> Brief:
    """Gather the brief for one product from what the database says now.

    Args:
        db: Database.
        product: The product.
        tz: Timezone for the times shown.
        now: Now (for tests).
        since: The current run's start: only posts found since then are shown, and the
            subline and status name this run (the morning email passes it).

    Returns:
        The brief.
    """
    from zoneinfo import ZoneInfo

    now = now or datetime.now(UTC)
    zone = ZoneInfo(tz)
    posts = await db.open_posts(product.id)
    if since is not None:
        posts = [p for p in posts if p.found_at >= since]
    cards = [card_for(p, now) for p in posts]
    asks = [c for c in cards if c.post.label == "ask"]
    pains = [c for c in cards if c.post.label == "pain"]
    mentions = [c for c in cards if c.post.label in ("you", "rival")]

    stats = {s.id: s for s in await db.theme_stats(product.id)}
    names = {s.id: s.name for s in stats.values()}
    blocks: dict[int | None, ThemeBlock] = {}
    for c in pains:
        tid = c.post.theme_id
        key = tid if tid is not None else -c.post.id
        if key not in blocks:
            stat = stats.get(tid) if tid is not None else None
            trend, trend_text = theme_trend(stat, now)
            blocks[key] = ThemeBlock(
                theme_id=tid,
                name=names.get(tid, "Not grouped yet") if tid is not None else "Not grouped yet",
                linkable=bool(stat and stat.total >= 2),
                trend=trend,
                trend_text=trend_text,
                lines=[],
            )
        blocks[key].lines.append(
            QuoteLine(c.post.id, c.post.quote or "", c.post.url, c.post.community)
        )
    mention_lines = [
        QuoteLine(
            c.post.id, c.post.quote or "", c.post.url, c.post.community, c.label_css, c.label_text
        )
        for c in mentions
    ]
    hidden = len(await db.hidden_posts(product.id))
    last = {"started_at": since} if since is not None else await db.last_product_run(product.id)
    if last:
        local = last["started_at"].astimezone(zone)
        n = await db.new_since(product.id, 24)
        posts_word = "post" if n == 1 else "posts"
        sub = f"{local:%A} {part_of_day(local)} · {n} new {posts_word} since yesterday"
        checked = len(product.phrases)
        status = f"Checked {checked} phrase{'s' if checked != 1 else ''} at {local:%H:%M}"
    else:
        sub = "Not checked yet" if not posts else f"{len(posts)} posts in the last 7 days"
        status = "Not checked yet"
    if product.paused:
        sub = "Paused · " + sub
    ordered = asks + pains + mentions
    return Brief(
        product=product,
        headline=headline(product, len(asks), len(pains), len(mentions)),
        subline=sub,
        total=len(cards),
        cards=ordered,
        worth_a_reply=asks,
        pain_count=len(pains),
        pain_points=list(blocks.values()),
        mentions=mention_lines,
        hidden=hidden,
        status=status,
        order=[c.post.id for c in ordered],
    )
