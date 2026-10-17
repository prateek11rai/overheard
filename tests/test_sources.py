from datetime import UTC, datetime

import httpx
import pytest

from overheard import sources
from overheard.sources import (
    PlanExhaustedError,
    Post,
    SourceError,
    parse_account,
    parse_hn,
    parse_serpapi,
    reddit_query,
)
from tests.conftest import load_fixture

NOW = datetime(2026, 10, 18, 7, 0, tzinfo=UTC)


def test_reddit_query_all_of_reddit_and_subreddits():
    assert reddit_query("meal plan from fridge") == 'site:reddit.com "meal plan from fridge"'
    assert reddit_query('say "hi"', ["r/Cooking"]) == 'site:reddit.com/r/Cooking "say  hi"'
    q = reddit_query("pantry recipes", ["MealPrepSunday", "Cooking", "bad name!"])
    assert q == '(site:reddit.com/r/MealPrepSunday OR site:reddit.com/r/Cooking) "pantry recipes"'


def test_parse_serpapi_keeps_threads_only_and_cleans_titles():
    posts = parse_serpapi(load_fixture("serpapi_google_reddit.json"), now=NOW)
    assert [p.community for p in posts] == [
        "r/MealPrepSunday",
        "r/EatCheapAndHealthy",
        "r/mealprep",
    ]
    first = posts[0]
    assert first.source == "reddit"
    assert first.title == "Is there an app that plans meals from what's already in my fridge?"
    assert first.url == (
        "https://www.reddit.com/r/MealPrepSunday/comments/abc123/is_there_an_app_that_plans_meals/"
    )
    assert "plans the week around what I already have" in first.body
    assert first.posted_at == datetime(2026, 10, 17, 22, 0, tzinfo=UTC)
    assert posts[1].title == "Food goes bad before I use it"
    assert posts[1].url.startswith("https://www.reddit.com/r/EatCheapAndHealthy/comments/def456/")
    assert "utm_source" not in posts[1].url
    assert posts[2].title == "Weekly planning thread"
    assert posts[2].posted_at == datetime(2026, 10, 15, tzinfo=UTC)


def test_parse_serpapi_no_results():
    assert parse_serpapi(load_fixture("serpapi_no_results.json")) == []


def test_parse_account():
    acc = parse_account(load_fixture("serpapi_account.json"))
    assert acc.plan_searches == 250
    assert acc.searches_left == 180
    assert acc.this_month_usage == 70


def test_parse_hn_stories_and_comments():
    posts = parse_hn(load_fixture("hn_search_by_date.json"))
    assert len(posts) == 2
    story, comment = posts
    assert story.source == "hn" and story.community == "Hacker News"
    assert story.url == "https://news.ycombinator.com/item?id=41000001"
    assert story.title == "Ask HN: How do you plan weekly meals?"
    assert story.body == (
        "Anything that works from my pantry instead of a recipe database? "
        "Every app assumes I'll buy everything fresh."
    )
    assert comment.title == "Ask HN: How do you plan weekly meals?"
    assert comment.body.startswith("Every inventory app wants me to scan every can.")
    assert "I won't." in comment.body and "<a" not in comment.body
    assert comment.posted_at == datetime.fromtimestamp(1792203600, UTC)


def test_post_json_round_trip():
    p = parse_hn(load_fixture("hn_search_by_date.json"))[0]
    assert Post.from_json(p.to_json()) == p


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_search_reddit_sends_one_google_search():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=load_fixture("serpapi_google_reddit.json"))

    async with _client(handler) as client:
        posts = await sources.search_reddit(client, "test-key", "meal plan from fridge")
    assert len(posts) == 3
    assert len(seen) == 1
    params = seen[0].url.params
    assert seen[0].url.host == "serpapi.com"
    assert params["engine"] == "google"
    assert params["q"] == 'site:reddit.com "meal plan from fridge"'
    assert params["tbs"] == "qdr:d"


async def test_search_reddit_plan_used_up_is_not_retryable():
    def handler(request):
        return httpx.Response(429, json=load_fixture("serpapi_out_of_searches.json"))

    async with _client(handler) as client:
        with pytest.raises(PlanExhaustedError):
            await sources.search_reddit(client, "k", "x y z")


async def test_search_reddit_server_error_is_retryable():
    def handler(request):
        return httpx.Response(503, json={"error": "upstream"})

    async with _client(handler) as client:
        with pytest.raises(SourceError):
            await sources.search_reddit(client, "k", "x y z")


async def test_search_reddit_no_results_is_empty():
    def handler(request):
        return httpx.Response(200, json=load_fixture("serpapi_no_results.json"))

    async with _client(handler) as client:
        assert await sources.search_reddit(client, "k", "x y z") == []


async def test_search_hn_asks_for_items_newer_than_since():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=load_fixture("hn_search_by_date.json"))

    since = datetime(2026, 10, 17, 7, 0, tzinfo=UTC)
    async with _client(handler) as client:
        posts = await sources.search_hn(client, "pantry recipes", since)
    assert len(posts) == 2
    params = seen[0].url.params
    assert seen[0].url.host == "hn.algolia.com"
    assert params["query"] == '"pantry recipes"'
    assert params["numericFilters"] == f"created_at_i>{int(since.timestamp())}"
    assert params["tags"] == "(story,comment)"


def test_comment_flag_survives_the_ledger_round_trip():
    _, comment = parse_hn(load_fixture("hn_search_by_date.json"))
    assert Post.from_json(comment.to_json()).is_comment
    assert Post.from_json({"source": "hn", "url": "u", "title": "t"}).is_comment is False


async def test_hacker_news_requests_are_spaced_out(monkeypatch):
    import asyncio
    import time

    monkeypatch.setattr(sources.HN_GATE, "gap", 0.2)
    monkeypatch.setattr(sources.HN_GATE, "next_at", 0.0)
    times: list[float] = []

    def answer(request: httpx.Request) -> httpx.Response:
        times.append(time.monotonic())
        return httpx.Response(200, json={"hits": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        await asyncio.gather(*(sources.search_hn(client, f"phrase {i}", NOW) for i in range(3)))
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert len(times) == 3 and all(g >= 0.18 for g in gaps), gaps
