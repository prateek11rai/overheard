from overheard.sources import Post as Found
from tests.conftest import add_product

MODEL = "embeddinggemma:300m"


async def test_hybrid_search_merges_words_and_meaning(db):
    product = await add_product(db)
    found = [
        Found(
            "reddit",
            "https://www.reddit.com/r/a/comments/1/",
            "Spinach goes bad",
            "I throw out spinach every week.",
            "r/a",
            None,
        ),
        Found(
            "reddit",
            "https://www.reddit.com/r/a/comments/2/",
            "Fridge waste",
            "Half my vegetables rot in the drawer.",
            "r/a",
            None,
        ),
        Found(
            "reddit",
            "https://www.reddit.com/r/a/comments/3/",
            "Knives",
            "Which chef knife stays sharp?",
            "r/a",
            None,
        ),
    ]
    await db.upsert_posts(product.id, found, None)
    ids = {p.title: p.id for p in await db.unlabelled_posts(product.id)}
    await db.ensure_embedding_index(MODEL, 3)
    await db.save_embedding(ids["Spinach goes bad"], MODEL, [1.0, 0.0, 0.0])
    await db.save_embedding(ids["Fridge waste"], MODEL, [0.9, 0.1, 0.0])
    await db.save_embedding(ids["Knives"], MODEL, [0.0, 0.0, 1.0])

    words_only = await db.hybrid_search(product.id, "spinach", None, MODEL)
    assert [p.title for p, _ in words_only] == ["Spinach goes bad"]

    both = await db.hybrid_search(product.id, "spinach", [0.95, 0.05, 0.0], MODEL)
    titles = [p.title for p, _ in both]
    assert titles[0] == "Spinach goes bad"  # first in both lists
    assert titles.index("Fridge waste") < titles.index("Knives")  # close in meaning
    scores = [s for _, s in both]
    assert scores == sorted(scores, reverse=True)
