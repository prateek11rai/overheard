from datetime import date

from overheard.pipeline import ledger_key, search_once
from overheard.sources import Post
from tests.conftest import add_product


def found(n: int) -> list[Post]:
    return [
        Post("reddit", f"https://www.reddit.com/r/x/comments/{i}/t/", f"t{i}", "b", "r/x", None)
        for i in range(n)
    ]


async def test_two_runs_on_the_same_day_spend_each_phrase_once(db):
    product = await add_product(db)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return found(3)

    args = dict(
        run_key="morning-1",
        product_id=product.id,
        phrase="Use Up Leftovers",
        source="reddit",
        day=date(2026, 10, 18),
        fetch=fetch,
    )
    first, spent1 = await search_once(db, **args)
    again, spent2 = await search_once(db, **{**args, "phrase": "use up leftovers"})
    assert calls == 1
    assert (spent1, spent2) == (True, False)
    assert again == first

    # Run now later the same day (or an overlapping run) reuses the paid result.
    _, spent3 = await search_once(db, **{**args, "run_key": "run-now-2"})
    assert not spent3 and calls == 1
    assert await db.reddit_used(date(2026, 10, 1)) == 1
    # Each run still sees the results it used.
    assert len(await db.ledger_results("morning-1", product.id)) == 3
    assert len(await db.ledger_results("run-now-2", product.id)) == 3

    # The next day pays again.
    _, spent4 = await search_once(db, **{**args, "run_key": "morning-3", "day": date(2026, 10, 19)})
    assert spent4 and calls == 2
    assert await db.reddit_used(date(2026, 10, 1)) == 2


async def test_hacker_news_stays_live_on_every_run(db):
    product = await add_product(db)
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return found(1)

    base = dict(product_id=product.id, phrase="p", source="hn", day=date(2026, 10, 18), fetch=fetch)
    await search_once(db, run_key="a", **base)
    await search_once(db, run_key="a", **base)
    await search_once(db, run_key="b", **base)
    assert calls == 2


async def test_hn_searches_are_not_counted_against_the_plan(db):
    product = await add_product(db)

    async def fetch():
        return found(1)

    await search_once(
        db,
        run_key="r",
        product_id=product.id,
        phrase="p",
        source="hn",
        day=date(2026, 10, 18),
        fetch=fetch,
    )
    assert await db.reddit_used(date(2026, 10, 1)) == 0


def test_ledger_key_is_per_day_for_reddit_and_per_run_for_hacker_news():
    day = date(2026, 10, 18)
    assert ledger_key("r1", 1, "reddit", "Meal Plan", day) == ledger_key(
        "r2", 1, "reddit", "meal plan", day
    )
    assert ledger_key("r1", 1, "hn", "x", day) != ledger_key("r2", 1, "hn", "x", day)
    assert ledger_key("r1", 1, "reddit", "x", day) != ledger_key(
        "r1", 1, "reddit", "x", date(2026, 10, 19)
    )


async def test_saved_posts_keep_the_comment_flag(db):
    product = await add_product(db)
    comment = Post(
        "hn",
        "https://news.ycombinator.com/item?id=1",
        "Thread",
        "My text here.",
        "Hacker News",
        None,
        True,
    )
    await db.upsert_posts(product.id, [comment], None)
    [saved] = await db.unlabelled_posts(product.id)
    assert saved.is_comment


async def test_deleting_a_product_keeps_its_paid_searches(db):
    product = await add_product(db)

    async def fetch():
        return found(2)

    await search_once(
        db,
        run_key="r",
        product_id=product.id,
        phrase="p",
        source="reddit",
        day=date(2026, 10, 18),
        fetch=fetch,
    )
    await db.delete_product(product.id)
    assert await db.reddit_used(date(2026, 10, 1)) == 1
    row = await db._one("SELECT product_id FROM search_ledger")
    assert row["product_id"] is None
    await db.delete_everything()
    assert await db.reddit_used(date(2026, 10, 1)) == 0
