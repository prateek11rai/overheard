from dataclasses import dataclass, field


@dataclass
class DemoPost:
    """One made-up post. `quote` must be a sentence of `body` (checked on load)."""

    label: str
    community: str
    title: str
    body: str
    quote: str
    days: float = 0.0
    posted_hours: float = 6.0
    competitor: str | None = None
    open: bool = False
    reply: str | None = None


@dataclass
class DemoTheme:
    """A made-up theme: its posts are spread over weeks by `weekly` (week 0 first)."""

    name: str
    first_seen_days: int
    weekly: list[int]
    posts: list[DemoPost] = field(default_factory=list)


def _p(community: str, title: str, body: str, quote: str, **kw: object) -> DemoPost:
    return DemoPost("pain", community, title, body, quote, **kw)  # type: ignore[arg-type]
