from temporalio.testing import ActivityEnvironment

from overheard.sources import Post as Found
from overheard.workflows import Activities, ModelArgs, ScanArgs
from tests.conftest import add_product
from tests.fakes import fake_ai, ollama_handler

OLD, NEW = "embeddinggemma:300m", "nomic-embed-text"


async def themed_product(db):
    product = await add_product(db)
    found = [
        Found(
            "reddit",
            "https://www.reddit.com/r/a/comments/1/",
            "Spinach",
            "I throw out spinach every week.",
            "r/a",
            None,
        ),
    ]
    await db.upsert_posts(product.id, found, None)
    [post] = await db.unlabelled_posts(product.id)
    await db.set_label(post.id, "pain", "I throw out spinach every week", None, "m", 1)
    await db.ensure_embedding_index(OLD, 3)
    await db.save_embedding(post.id, OLD, [1.0, 0.0, 0.0])
    theme = await db.create_theme(product.id, "Food goes bad", OLD, [1.0, 0.0, 0.0])
    await db.add_to_theme(post.id, theme, [1.0, 0.0, 0.0])
    return product, theme


def acts_with(db, settings, monkeypatch, vector):
    handler = ollama_handler(
        chat=lambda body: {"name": "Never used"},
        embed=lambda texts: [list(vector) for _ in texts],
    )
    acts = Activities(settings, db)

    async def ai():
        return fake_ai(handler)

    monkeypatch.setattr(acts, "_ai", ai)
    return acts


async def test_new_posts_join_existing_themes_during_a_reembed(db, test_settings, monkeypatch):
    product, theme = await themed_product(db)
    await db.set_setting("models", {"embed": OLD})
    await db.set_setting("pending_embed", NEW)
    found = [
        Found(
            "reddit",
            "https://www.reddit.com/r/a/comments/2/",
            "Waste",
            "Half my vegetables rot every week.",
            "r/a",
            None,
        )
    ]
    await db.upsert_posts(product.id, found, None)
    post = (await db.unlabelled_posts(product.id))[0]
    await db.set_label(post.id, "pain", "Half my vegetables rot every week", None, "m", 1)
    run_id = await db.upsert_run("check-x", "check", product.id)
    acts = acts_with(db, test_settings, monkeypatch, [0.95, 0.05, 0.0])
    created = await ActivityEnvironment().run(
        acts.update_themes, ScanArgs(product.id, run_id, "check-x")
    )
    assert created == 0
    assert (await db.post(post.id)).theme_id == theme
    assert len(await db.themes_for(product.id, OLD)) == 1


async def test_reembed_switches_the_model_as_its_last_step(db, test_settings, monkeypatch):
    product, theme = await themed_product(db)
    await db.set_setting("models", {"embed": OLD, "chat": "gemma4:e4b"})
    await db.set_setting("pending_embed", NEW)
    acts = acts_with(db, test_settings, monkeypatch, [0.0, 1.0, 0.0, 0.0])
    done = await ActivityEnvironment().run(acts.reembed, ModelArgs(NEW))
    assert done == 1
    models = await db.get_setting("models")
    assert models["embed"] == NEW and models["chat"] == "gemma4:e4b"
    assert await db.get_setting("pending_embed") is None
    [moved] = await db.themes_for(product.id, NEW)
    assert moved["id"] == theme and moved["centroid"] == [0.0, 1.0, 0.0, 0.0]
