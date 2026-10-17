from datetime import UTC, datetime

import pytest
from temporalio.testing import ActivityEnvironment

from overheard import mail
from overheard.pipeline import brief_for
from overheard.sources import Post as Found
from overheard.workflows import Activities, ScanArgs
from tests.conftest import add_product

QUOTE = "Is there an app that plans the week around what I already have"


async def prepared(db, run_key: str):
    product = await add_product(db, recipients=["me@example.com"])
    run_id = await db.upsert_run(run_key, "check", product.id)
    post = Found(
        "reddit",
        "https://www.reddit.com/r/MealPrepSunday/comments/x1/app/",
        "Fridge planner?",
        QUOTE + "? Leftovers first.",
        "r/MealPrepSunday",
        None,
    )
    await db.upsert_posts(product.id, [post], run_id)
    [saved] = await db.unlabelled_posts(product.id)
    await db.set_label(saved.id, "ask", QUOTE, None, "gemma4:e4b", 1)
    return product, run_id


@pytest.mark.parametrize("mode", ["smtp", "file"])
async def test_a_retried_send_digest_sends_one_email(db, test_settings, monkeypatch, mode):
    sent: list[str] = []
    if mode == "smtp":
        test_settings.email_mode = "smtp"
        monkeypatch.setattr(
            mail, "_send_smtp", lambda s, to, subject, html, text: sent.append(subject)
        )
    else:
        real_write = mail.write_file
        monkeypatch.setattr(
            mail,
            "write_file",
            lambda s, slug, subject, html: (
                sent.append(subject) or real_write(s, slug, subject, html)
            ),
        )
    product, run_id = await prepared(db, f"wf-digest-{mode}")
    acts = Activities(test_settings, db)
    args = ScanArgs(product.id, run_id, f"wf-digest-{mode}", None)
    env = ActivityEnvironment()
    first = await env.run(acts.send_digest, args)
    retry = await env.run(acts.send_digest, args)
    assert sent == ["Mealwise: 1 person asked for something like it"]
    assert first in mail.SENT_OUTCOMES
    assert retry == f"{first} · not sent again"
    steps = {s.step_key: s.text for s in await db.steps(run_id)}
    assert steps[f"email:{product.id}"] == retry


async def test_another_run_sends_again_and_quiet_mornings_leave_no_record(db, test_settings):
    product, _ = await prepared(db, "wf-a")
    brief = await brief_for(db, product, "UTC")
    assert await mail.deliver_once(db, test_settings, product, brief, "wf-a") in mail.SENT_OUTCOMES
    assert (await mail.deliver_once(db, test_settings, product, brief, "wf-a")).endswith(
        "not sent again"
    )
    assert await mail.deliver_once(db, test_settings, product, brief, "wf-b") in mail.SENT_OUTCOMES
    quiet = await brief_for(
        db,
        product,
        "UTC",
        since=datetime.max.replace(tzinfo=UTC),
    )
    assert (
        await mail.deliver_once(db, test_settings, product, quiet, "wf-c")
        == "Quiet morning · no email"
    )
    assert await db.digest_get(mail.digest_key("wf-c", product.id)) is None
