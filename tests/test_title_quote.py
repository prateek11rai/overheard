import asyncio
import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from overheard import mail
from overheard.db import Database
from overheard.db.models import Post
from overheard.pipeline import brief_for
from overheard.pipeline.brief import card_for
from overheard.sources import Post as Found
from overheard.web import create_app
from tests.conftest import SCHEMA, TEST_DSN, _reset_schema, add_product
from tests.test_web import ORIGIN, _prepare, settings_for

NOW = datetime(2026, 10, 18, 7, 0, tzinfo=UTC)
TITLE = "What's the backend database you are using for your apps?"
QUOTE = "What's the backend database you are using for your apps"
BODY = "hi fellow developers, I have been developing apps for a while now and I code in Swift."


def post(**over) -> Post:
    base = dict(
        id=1,
        product_id=1,
        source="reddit",
        url="https://www.reddit.com/r/appledevelopers/comments/x1/db/",
        title=TITLE,
        body=BODY,
        community="r/appledevelopers",
        posted_at=None,
        found_at=NOW,
        label="ask",
        quote=QUOTE,
        competitor=None,
        status="new",
        snoozed_until=None,
        theme_id=None,
        reply_draft=None,
        label_attempts=1,
    )
    base.update(over)
    return Post(**base)


def test_a_quote_from_the_title_highlights_the_title():
    c = card_for(post(), NOW)
    assert (c.title_before, c.title_mark, c.title_after) == ("", QUOTE, "?")
    assert (c.before, c.mark, c.after) == (BODY, "", "")
    assert c.checked


def test_a_quote_in_the_body_still_highlights_the_body():
    c = card_for(post(quote="I have been developing apps for a while now"), NOW)
    assert c.title_mark == "" and c.title_before == TITLE
    assert c.mark == "I have been developing apps for a while now"


def test_a_comment_never_highlights_its_threads_title():
    c = card_for(post(source="hn", is_comment=True, body="Same here.", quote=QUOTE), NOW)
    assert c.title_mark == "" and c.title_before == TITLE
    assert not c.checked


def test_a_title_only_story_does_not_repeat_its_title():
    c = card_for(post(source="hn", body=""), NOW)
    assert c.title_mark == QUOTE
    assert (c.before, c.mark, c.after) == ("", "", "")


async def _ask_from_its_title(db) -> int:
    product = await add_product(db, slug="dbpicker", name="DBPicker", recipients=[])
    run_id = await db.upsert_run("check-dbpicker-1", "check", product.id)
    found = Found("reddit", post().url, TITLE, BODY, "r/appledevelopers", None)
    await db.upsert_posts(product.id, [found], run_id)
    [saved] = await db.unlabelled_posts(product.id)
    await db.set_label(saved.id, "ask", QUOTE, None, "gemma4:e4b", 1)
    return product.id


def test_the_brief_and_review_mode_highlight_the_title(tmp_path):
    settings = settings_for(tmp_path)
    _reset_schema()
    asyncio.run(_prepare(settings))

    async def prepare():
        async with Database(TEST_DSN, SCHEMA) as db:
            await _ask_from_its_title(db)

    asyncio.run(prepare())
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as client:
        brief = client.get("/p/dbpicker", headers=ORIGIN).text
        review = client.get("/p/dbpicker/review", headers=ORIGIN, follow_redirects=True).text
    title = re.search(r'<a class="title t"[^>]*>(.*?)</a>', brief, re.S).group(1)
    assert f"<mark>{QUOTE.replace(chr(39), '&#39;')}</mark>?" in title
    title = re.search(r'<a class="title t"[^>]*>(.*?)</a>', review, re.S).group(1)
    assert "<mark>" in title


async def test_the_email_highlights_the_title(db, test_settings):
    product_id = await _ask_from_its_title(db)
    product = await db.product(product_id)
    brief = await brief_for(db, product, "UTC")
    _, html, _ = mail.render_digest(test_settings, product, brief)
    link = re.search(
        r'<a href="https://www\.reddit\.com/r/appledevelopers[^>]*>(.*?)</a>', html, re.S
    )
    assert link and "<span style=" in link.group(1) and "backend database" in link.group(1)
