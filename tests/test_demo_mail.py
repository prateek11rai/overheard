from overheard import demo, mail, pipeline
from tests.conftest import make_product


def test_every_demo_quote_is_in_its_post():
    posts = demo.all_demo_posts()
    assert len(posts) > 50
    for slug, p in posts:
        assert pipeline.verify_quote(p.quote, pipeline.post_text(p.title, p.body)), (slug, p.quote)


async def test_load_clear_load(db, test_settings):
    products = await demo.load(db, test_settings)
    assert set(products) == {"demo-mealwise", "demo-shipnote", "demo-plantpal"}
    assert all(p.demo for p in products.values())
    counts = await db.open_counts()
    assert counts[products["demo-mealwise"].id] == 7
    assert counts[products["demo-shipnote"].id] == 3
    assert products["demo-plantpal"].paused
    files = sorted(f.name for f in test_settings.digests_dir.iterdir())
    assert len(files) == 3 and all("demo-" in f for f in files)
    brief = await pipeline.brief_for(db, products["demo-mealwise"], "UTC")
    assert brief.headline == "2 people asked for something like Mealwise."
    assert [b.name for b in brief.pain_points] == [
        "Food goes bad before it's used",
        "Recipes ignore what's at home",
        "Planning takes longer than cooking",
    ]
    assert brief.pain_points[0].trend == "up" and brief.pain_points[1].trend == "new"
    assert brief.hidden == 5
    assert len(await db.runs()) == 3

    assert await demo.clear(db, test_settings) == 3
    assert await db.products() == [] and await db.runs() == []
    assert list(test_settings.digests_dir.iterdir()) == []
    await demo.load(db, test_settings)
    assert len(await db.products()) == 3


async def test_demo_load_twice_replaces_it(db, test_settings):
    await demo.load(db, test_settings)
    await demo.load(db, test_settings)
    assert len(await db.products()) == 3


async def test_digest_email_is_escaped_dark_and_leads_with_the_count(db, test_settings):
    products = await demo.load(db, test_settings)
    product = products["demo-mealwise"]
    await db._exec(
        "UPDATE posts SET title = '<b>bold</b> fridge?' WHERE product_id = %s AND label = 'ask'",
        (product.id,),
    )
    brief = await pipeline.brief_for(db, product, "UTC")
    subject, html, text = mail.render_digest(test_settings, product, brief)
    assert subject == "Mealwise: 2 people asked for something like it"
    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html
    assert 'bgcolor="#181825"' in html and "Open the brief" in html
    assert "<table" in html and "font-family:-apple-system" in html
    assert "http://127.0.0.1:8000/p/demo-mealwise" in html
    assert "<b>bold</b> fridge?" in text


async def test_quiet_morning_email_only_when_asked(test_settings):
    product = make_product(quiet_day_email=False)
    empty = pipeline.Brief(product, "Nothing new", "", 0, [], [], 0, [], [], 0, "", [])
    assert await mail.deliver(test_settings, product, empty) == "Quiet morning · no email"
    product = make_product(quiet_day_email=True)
    assert await mail.deliver(test_settings, product, empty) == "Digest saved to .data/digests"
    subject, html, _ = mail.render_quiet(test_settings, product)
    assert subject == "Mealwise: quiet morning"
    assert "Nothing new about Mealwise since yesterday." in html


async def test_email_off_keeps_the_digest_in_the_app(test_settings):
    test_settings.email_mode = "off"
    product = make_product(quiet_day_email=True)
    empty = pipeline.Brief(product, "Nothing new", "", 0, [], [], 0, [], [], 0, "", [])
    assert await mail.deliver(test_settings, product, empty) == "Email off · digest in the app"
