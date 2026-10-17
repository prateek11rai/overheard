from overheard.pipeline import cosine, moved_centroid, nearest_theme
from tests.conftest import add_product


def test_cosine():
    assert cosine([1, 0], [1, 0]) == 1
    assert cosine([1, 0], [0, 1]) == 0
    assert cosine([1, 0], [0, 0]) == 0
    assert cosine([1, 0], [1, 0, 0]) == 0


def test_nearest_theme_joins_when_similar_enough():
    themes = [{"id": 1, "centroid": [1.0, 0.0]}, {"id": 2, "centroid": [0.0, 1.0]}]
    hit = nearest_theme([0.9, 0.1], themes, threshold=0.8)
    assert hit is not None and hit[0]["id"] == 1


def test_nearest_theme_starts_a_new_one_when_far():
    themes = [{"id": 1, "centroid": [1.0, 0.0]}]
    assert nearest_theme([0.0, 1.0], themes, threshold=0.5) is None
    assert nearest_theme([1.0, 0.0], [], threshold=0.5) is None


def test_centroid_is_a_running_mean():
    assert moved_centroid([1.0, 0.0], 1, [0.0, 1.0]) == [0.5, 0.5]
    assert moved_centroid([0.5, 0.5], 2, [1.0, 1.0]) == [2 / 3, 2 / 3]


async def test_themes_in_the_database(db):
    product = await add_product(db)
    await db.ensure_embedding_index("embeddinggemma:300m", 3)
    await db.ensure_embedding_index("embeddinggemma:300m", 3)
    tid = await db.create_theme(product.id, "Food goes bad", "embeddinggemma:300m", [1.0, 0, 0])
    themes = await db.themes_for(product.id, "embeddinggemma:300m")
    assert themes[0]["id"] == tid and themes[0]["centroid"] == [1.0, 0.0, 0.0]
    assert await db.themes_for(product.id, "other-model") == []
