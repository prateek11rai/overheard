from datetime import date

from overheard.pipeline import ledger_key, search_once
from overheard.sources import Post
from tests.conftest import add_product


def found(n: int) -> list[Post]:
    return [
        Post("reddit", f"https://www.reddit.com/r/x/comments/{i}/t/", f"t{i}", "b", "r/x", None)
        for i in range(n)
    ]


async def test_search_once_spends_one_search_per_run_and_phrase(db):
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
    assert await db.reddit_used(date(2026, 10, 1)) == 1

    _, spent3 = await search_once(db, **{**args, "run_key": "morning-2"})
    assert spent3 and calls == 2
    assert await db.reddit_used(date(2026, 10, 1)) == 2


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


def test_ledger_key_is_stable():
    assert ledger_key("r", 1, "reddit", "Meal Plan") == ledger_key("r", 1, "reddit", "meal plan")
    assert ledger_key("r", 1, "reddit", "x") != ledger_key("r", 1, "hn", "x")
