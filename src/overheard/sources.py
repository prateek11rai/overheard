import html
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

SERPAPI_SEARCH = "https://serpapi.com/search.json"
SERPAPI_ACCOUNT = "https://serpapi.com/account.json"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://news.ycombinator.com/item?id="

_REDDIT_THREAD = re.compile(r"^/r/([A-Za-z0-9_]+)/comments/[A-Za-z0-9]+")
_TITLE_SUFFIX = re.compile(r"\s*(?::\s*r/[A-Za-z0-9_]+|-\s*Reddit|\|\s*Reddit)\s*$")
_RELATIVE = re.compile(r"^(\d+)\s+(minute|hour|day|week)s?\s+ago$", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_EXHAUSTED_HINTS = ("run out of searches", "out of searches", "plan searches", "searches left")


class PlanExhaustedError(Exception):
    """The SerpApi plan has no searches left (never retried)."""


class SourceError(Exception):
    """A source failed in a way that a retry may fix."""


@dataclass
class Post:
    """A post as a source returns it: the same shape for Reddit and Hacker News."""

    source: Literal["reddit", "hn"]
    url: str
    title: str
    body: str
    community: str
    posted_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        """A JSON-ready dict (for the search ledger).

        Returns:
            The post as plain values.
        """
        d = asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Post":
        """Rebuild a post saved with `to_json`.

        Args:
            d: The saved dict.

        Returns:
            The post.
        """
        posted = d.get("posted_at")
        return cls(
            source=d["source"],
            url=d["url"],
            title=d["title"],
            body=d.get("body", ""),
            community=d.get("community", ""),
            posted_at=datetime.fromisoformat(posted) if posted else None,
        )


@dataclass
class SerpApiAccount:
    """The parts of SerpApi's free `account.json` that the search plan needs."""

    plan_searches: int
    searches_left: int
    this_month_usage: int


def reddit_query(phrase: str, subreddits: list[str] | None = None) -> str:
    """Build the Google query for a phrase on Reddit.

    Args:
        phrase: The phrase to look for (searched as an exact phrase).
        subreddits: Limit to these subreddits; empty means all of Reddit.

    Returns:
        A query such as `site:reddit.com "meal plan from fridge"`.
    """
    clean = phrase.replace('"', " ").strip()
    names = [s.strip().removeprefix("r/") for s in subreddits or []]
    subs = [s for s in names if _SUBREDDIT.match(s)]
    if not subs:
        site = "site:reddit.com"
    elif len(subs) == 1:
        site = f"site:reddit.com/r/{subs[0]}"
    else:
        site = "(" + " OR ".join(f"site:reddit.com/r/{s}" for s in subs) + ")"
    return f'{site} "{clean}"'


def _parse_date(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    m = _RELATIVE.match(value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now - timedelta(**{f"{unit}s": n})
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_serpapi(payload: dict[str, Any], now: datetime | None = None) -> list[Post]:
    """Turn a SerpApi Google reply into Reddit posts (thread links only).

    Args:
        payload: The JSON reply.
        now: Reference time for "9 hours ago" dates.

    Returns:
        Posts in result order, duplicates removed.
    """
    now = now or datetime.now(UTC)
    posts: list[Post] = []
    seen: set[str] = set()
    for item in payload.get("organic_results", []) or []:
        link = item.get("link") or ""
        parts = urlsplit(link)
        host = parts.netloc.lower()
        if not (host == "reddit.com" or host.endswith(".reddit.com")):
            continue
        m = _REDDIT_THREAD.match(parts.path)
        if not m:
            continue
        url = f"https://www.reddit.com{parts.path.rstrip('/')}/"
        if url in seen:
            continue
        seen.add(url)
        title = _TITLE_SUFFIX.sub("", (item.get("title") or "").strip())
        posts.append(
            Post(
                source="reddit",
                url=url,
                title=title,
                body=(item.get("snippet") or "").strip(),
                community=f"r/{m.group(1)}",
                posted_at=_parse_date(item.get("date"), now),
            )
        )
    return posts


def _raise_for_serpapi(r: httpx.Response) -> dict[str, Any]:
    try:
        payload = r.json()
    except ValueError:
        payload = {}
    error = str(payload.get("error") or "")
    if r.status_code == 429 or any(h in error.lower() for h in _EXHAUSTED_HINTS):
        raise PlanExhaustedError("SerpApi plan has no searches left")
    if r.status_code in (401, 403):
        raise PlanExhaustedError("SerpApi rejected the key")
    if r.status_code >= 400 and "hasn't returned any results" not in error:
        raise SourceError(f"SerpApi answered {r.status_code}")
    return payload


async def search_reddit(
    client: httpx.AsyncClient,
    api_key: str,
    phrase: str,
    subreddits: list[str] | None = None,
    window: Literal["d", "w"] = "d",
) -> list[Post]:
    """Search Reddit through Google with SerpApi (one search credit).

    Args:
        client: HTTP client.
        api_key: SerpApi key.
        phrase: The phrase to look for.
        subreddits: Limit to these subreddits.
        window: Past day (`d`) or past week (`w`).

    Returns:
        Reddit posts: Google's title and snippet, URL, community, date.

    Raises:
        PlanExhaustedError: If the plan is used up (don't retry).
        SourceError: If SerpApi failed in a way a retry may fix.
    """
    params = {
        "engine": "google",
        "q": reddit_query(phrase, subreddits),
        "tbs": f"qdr:{window}",
        "num": "20",
        "hl": "en",
        "api_key": api_key,
    }
    try:
        r = await client.get(SERPAPI_SEARCH, params=params, timeout=30)
    except httpx.HTTPError as exc:
        raise SourceError(f"SerpApi unreachable ({type(exc).__name__})") from exc
    return parse_serpapi(_raise_for_serpapi(r))


def parse_account(payload: dict[str, Any]) -> SerpApiAccount:
    """Read plan size and searches left from `account.json`.

    Args:
        payload: The JSON reply.

    Returns:
        The account numbers the plan needs.
    """
    plan = int(payload.get("searches_per_month") or 0)
    left = payload.get("total_searches_left")
    if left is None:
        left = payload.get("plan_searches_left", 0)
    return SerpApiAccount(
        plan_searches=plan,
        searches_left=int(left or 0),
        this_month_usage=int(payload.get("this_month_usage") or 0),
    )


async def serpapi_account(client: httpx.AsyncClient, api_key: str) -> SerpApiAccount:
    """Fetch the SerpApi account (free, doesn't use a search).

    Args:
        client: HTTP client.
        api_key: SerpApi key.

    Returns:
        Plan size, searches left and this month's usage.

    Raises:
        SourceError: If SerpApi can't be reached or rejects the request.
    """
    try:
        r = await client.get(SERPAPI_ACCOUNT, params={"api_key": api_key}, timeout=20)
    except httpx.HTTPError as exc:
        raise SourceError(f"SerpApi unreachable ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise SourceError(f"SerpApi account answered {r.status_code}")
    return parse_account(r.json())


def _plain(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<p>", "\n\n", text)
    return html.unescape(_TAGS.sub("", text)).strip()


def parse_hn(payload: dict[str, Any]) -> list[Post]:
    """Turn a Hacker News Algolia reply into posts (stories and comments).

    Args:
        payload: The JSON reply.

    Returns:
        Posts in result order.
    """
    posts = []
    for hit in payload.get("hits", []) or []:
        oid = str(hit.get("objectID") or "")
        if not oid.isdigit():
            continue
        is_comment = bool(hit.get("comment_text"))
        title = (hit.get("story_title") if is_comment else hit.get("title")) or ""
        body = _plain(hit.get("comment_text") if is_comment else hit.get("story_text"))
        created = hit.get("created_at_i")
        posts.append(
            Post(
                source="hn",
                url=HN_ITEM + oid,
                title=html.unescape(title).strip() or "Hacker News comment",
                body=body,
                community="Hacker News",
                posted_at=datetime.fromtimestamp(created, UTC) if created else None,
            )
        )
    return posts


async def search_hn(client: httpx.AsyncClient, phrase: str, since: datetime) -> list[Post]:
    """Search Hacker News stories and comments newer than `since` (free).

    Args:
        client: HTTP client.
        phrase: The phrase to look for (exact phrase).
        since: Only items created after this time.

    Returns:
        Posts with full text.

    Raises:
        SourceError: If the API fails.
    """
    params = {
        "query": f'"{phrase.replace(chr(34), " ").strip()}"',
        "advancedSyntax": "true",
        "tags": "(story,comment)",
        "numericFilters": f"created_at_i>{int(since.timestamp())}",
        "hitsPerPage": "50",
    }
    try:
        r = await client.get(HN_SEARCH, params=params, timeout=30)
    except httpx.HTTPError as exc:
        raise SourceError(f"Hacker News unreachable ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise SourceError(f"Hacker News answered {r.status_code}")
    return parse_hn(r.json())
