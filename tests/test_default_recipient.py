import asyncio
import re
import smtplib
import traceback

import pytest
from fastapi.testclient import TestClient
from temporalio.testing import ActivityEnvironment

from overheard import mail
from overheard.pipeline import brief_for
from overheard.sources import Post as Found
from overheard.web import create_app
from overheard.workflows import Activities, ScanArgs
from tests.conftest import _reset_schema, add_product
from tests.test_web import _prepare, settings_for

OWNER = "owner@example.com"
QUOTE = "Is there an app that plans the week around what I already have"


class FakeSMTP:
    """An SMTP server that records recipients, or refuses them like a real one does."""

    sent: list[str] = []
    refuse = False

    def __init__(self, host, port, timeout=None) -> None:
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def starttls(self, context=None) -> None:
        pass

    def login(self, user, password) -> None:
        pass

    def send_message(self, msg) -> None:
        if FakeSMTP.refuse:
            to = msg["To"]
            raise smtplib.SMTPRecipientsRefused({to: (550, f"5.1.1 <{to}>: unknown".encode())})
        FakeSMTP.sent.append(msg["To"])


@pytest.fixture
def smtp(test_settings, monkeypatch):
    FakeSMTP.sent, FakeSMTP.refuse = [], False
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    return test_settings.model_copy(
        update={"email_mode": "smtp", "smtp_host": "smtp.example.com", "smtp_user": OWNER}
    )


async def product_with_a_post(db, run_key: str):
    product = await add_product(db, recipients=[])
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


async def test_a_product_without_recipients_mails_the_owner(db, smtp):
    product, _ = await product_with_a_post(db, "wf-owner")
    brief = await brief_for(db, product, "UTC")
    assert await mail.deliver(smtp, product, brief) == "Email sent"
    assert FakeSMTP.sent == [OWNER]


def _no_address(exc: BaseException) -> None:
    text = "".join(traceback.format_exception(exc))
    assert OWNER not in text and "@" not in str(exc)


async def test_a_refused_email_never_names_the_address(db, smtp):
    FakeSMTP.refuse = True
    product, run_id = await product_with_a_post(db, "wf-refused")
    brief = await brief_for(db, product, "UTC")
    with pytest.raises(mail.MailError) as caught:
        await mail.deliver(smtp, product, brief)
    _no_address(caught.value)
    assert "SMTPRecipientsRefused" in str(caught.value)

    acts = Activities(smtp, db)
    with pytest.raises(mail.MailError) as caught:
        await ActivityEnvironment().run(
            acts.send_digest, ScanArgs(product.id, run_id, "wf-refused", None)
        )
    _no_address(caught.value)
    assert all(OWNER not in s.text for s in await db.steps(run_id))


async def test_the_cli_test_email_never_prints_the_address(smtp, capsys):
    FakeSMTP.refuse = True
    assert await mail.email_test(smtp) == 1
    assert OWNER not in capsys.readouterr().out


def test_forms_and_settings_name_the_mailbox_not_the_address(tmp_path):
    s = settings_for(tmp_path, email_mode="smtp", smtp_host="smtp.example.com", smtp_user=OWNER)
    _reset_schema()
    asyncio.run(_prepare(s))
    with TestClient(create_app(s), base_url="http://127.0.0.1:8000") as c:
        new = c.get("/products/new").text
        assert 'placeholder="your own mailbox (SMTP_USER)"' in new
        settings_page = c.get("/settings").text
        assert "your own mailbox (SMTP_USER)" in settings_page
        assert "connected" not in settings_page.split(">Email<")[1][:400]
        setup = c.get("/setup").text
        assert 'placeholder="your own mailbox (SMTP_USER)"' in setup
        edit = re.search(r'href="(/products/\d+/edit)"', settings_page).group(1)
        for page in (new, settings_page, setup, c.get(edit).text, c.get("/runs").text):
            assert OWNER not in page
