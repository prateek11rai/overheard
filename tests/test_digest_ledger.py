from datetime import UTC, datetime

import pytest
from temporalio.testing import ActivityEnvironment

from overheard import mail
from overheard.pipeline import brief_for, today_in
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
            lambda s, slug, subject, html, run="run", **kw: (
                sent.append(subject) or real_write(s, slug, subject, html, run, **kw)
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


async def test_two_overlapping_attempts_send_once(db, test_settings, monkeypatch):
    import asyncio
    import time

    test_settings.email_mode = "smtp"
    sent: list[str] = []

    def slow_send(s, to, subject, html, text):
        time.sleep(0.3)
        sent.append(subject)

    monkeypatch.setattr(mail, "_send_smtp", slow_send)
    product, _ = await prepared(db, "wf-overlap")
    brief = await brief_for(db, product, "UTC")
    results = await asyncio.gather(
        mail.deliver_once(db, test_settings, product, brief, "wf-overlap"),
        mail.deliver_once(db, test_settings, product, brief, "wf-overlap"),
    )
    assert len(sent) == 1
    assert "Email sent" in results
    assert any("not sent again" in r for r in results)
    row = await db.digest_get(mail.digest_key("wf-overlap", product.id))
    assert row["status"] == "sent"


async def test_a_failed_send_can_be_retried(db, test_settings, monkeypatch):
    import pytest as _pytest

    test_settings.email_mode = "smtp"
    calls = []

    def flaky(s, to, subject, html, text):
        calls.append(subject)
        if len(calls) == 1:
            raise OSError("connection reset")

    monkeypatch.setattr(mail, "_send_smtp", flaky)
    product, _ = await prepared(db, "wf-flaky")
    brief = await brief_for(db, product, "UTC")
    with _pytest.raises(OSError):
        await mail.deliver_once(db, test_settings, product, brief, "wf-flaky")
    assert await mail.deliver_once(db, test_settings, product, brief, "wf-flaky") == "Email sent"
    assert len(calls) == 2


def test_digest_file_names_include_the_run(test_settings):
    path = mail.write_file(test_settings, "mealwise", "s", "<p>x</p>", run="run-now-20261018T0700")
    assert path.name.endswith("-runnow20261018t0700-mealwise.html")
    assert path.name[:10].count("-") == 2


def test_digest_file_dates_follow_the_schedule_timezone(test_settings):
    evening_utc = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
    tokyo = mail.write_file(
        test_settings, "mealwise", "s", "<p>x</p>", run="r", tz="Asia/Tokyo", now=evening_utc
    )
    la = mail.write_file(
        test_settings,
        "mealwise",
        "s",
        "<p>x</p>",
        run="r",
        tz="America/Los_Angeles",
        now=evening_utc,
    )
    assert tokyo.name.startswith("2026-10-18-")
    assert la.name.startswith("2026-10-17-")


@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Etc/GMT+12"])
async def test_send_digest_names_the_file_by_the_schedules_day(db, test_settings, zone):
    await db.set_setting("timezone", zone)
    product, run_id = await prepared(db, "wf-day")
    acts = Activities(test_settings, db)
    await ActivityEnvironment().run(acts.send_digest, ScanArgs(product.id, run_id, "wf-day", None))
    [path] = test_settings.digests_dir.glob("*.html")
    assert path.name.startswith(today_in(zone).isoformat() + "-")
