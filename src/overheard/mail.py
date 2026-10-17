import asyncio
import re
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from overheard.db import Database, Product
from overheard.pipeline import Brief, safe_filename, today_in
from overheard.settings import Settings

TEMPLATES = Path(__file__).parent / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=select_autoescape(["html"], disabled_extensions=["txt"], default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["safe_url"] = lambda url: url if str(url).startswith(("https://", "http://")) else "#"


def subject_for(product: Product, brief: Brief | None) -> str:
    """The subject line, leading with the most useful count.

    Args:
        product: The product.
        brief: Its brief, or None for a quiet morning.

    Returns:
        The subject.
    """
    name = product.name
    if brief is None or brief.total == 0:
        return f"{name}: quiet morning"
    asks = len(brief.worth_a_reply)
    if asks:
        who = "1 person" if asks == 1 else f"{asks} people"
        return f"{name}: {who} asked for something like it"
    if brief.pain_count:
        n = brief.pain_count
        who = "1 person" if n == 1 else f"{n} people"
        return f"{name}: {who} {'describes' if n == 1 else 'describe'} the problem it solves"
    n = len(brief.mentions)
    return f"{name}: {n} new mention{'s' if n != 1 else ''}"


def render_digest(settings: Settings, product: Product, brief: Brief) -> tuple[str, str, str]:
    """Render one product's morning email.

    Args:
        settings: App settings (for the brief link).
        product: The product.
        brief: Its brief.

    Returns:
        (subject, HTML, plain text).
    """
    ctx = {
        "product": product,
        "brief": brief,
        "link": f"{settings.app_url}/p/{product.slug}",
        "subject": subject_for(product, brief),
    }
    html = _env.get_template("digest.html").render(ctx)
    text = _env.get_template("digest.txt").render(ctx)
    return ctx["subject"], html, text


def render_quiet(settings: Settings, product: Product) -> tuple[str, str, str]:
    """Render the one-line quiet-morning email.

    Args:
        settings: App settings (for the brief link).
        product: The product.

    Returns:
        (subject, HTML, plain text).
    """
    ctx = {
        "product": product,
        "link": f"{settings.app_url}/p/{product.slug}",
        "subject": subject_for(product, None),
    }
    html = _env.get_template("quiet.html").render(ctx)
    text = f"Nothing new about {product.name} since yesterday.\n\n{ctx['link']}\n"
    return ctx["subject"], html, text


def run_tag(run: str) -> str:
    """A short tag for a run in file names: lowercase letters and digits only.

    Args:
        run: The run (workflow id).

    Returns:
        The tag, without dashes so names can be split back into date, run and slug.
    """
    return re.sub(r"[^a-z0-9]", "", run.lower())[-28:] or "run"


def write_file(
    settings: Settings,
    slug: str,
    subject: str,
    html: str,
    run: str = "run",
    tz: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Write a digest to `.data/digests/<date>-<run>-<slug>.html` (`EMAIL_MODE=file`).

    Args:
        settings: App settings.
        slug: Product slug.
        subject: The subject (kept in the HTML title).
        html: The email HTML.
        run: The run it belongs to.
        tz: The schedule's timezone, which dates the morning (default: the first-run one).
        now: The current time (for tests).

    Returns:
        The file written.
    """
    folder = settings.digests_dir
    folder.mkdir(parents=True, exist_ok=True)
    day = today_in(tz or settings.default_timezone(), now or datetime.now(UTC))
    name = f"{day:%Y-%m-%d}-{run_tag(run)}-{safe_filename(slug)}.html"
    path = folder / name
    path.write_text(html, encoding="utf-8")
    return path


class MailError(RuntimeError):
    """Sending failed. The message names the error, never an address."""


def _failure(exc: BaseException) -> str:
    code = getattr(exc, "smtp_code", None)
    return f"Email failed: {type(exc).__name__}" + (f" {code}" if code else "")


def _send_smtp(settings: Settings, to: list[str], subject: str, html: str, text: str) -> None:
    if not settings.smtp_host:
        raise RuntimeError("SMTP_HOST is not set")
    try:
        _smtp_send(settings, to, subject, html, text)
    except (smtplib.SMTPException, OSError) as exc:
        # SMTP errors quote addresses back; keep them out of run steps, Temporal and logs.
        raise MailError(_failure(exc)) from None


def _smtp_send(settings: Settings, to: list[str], subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Overheard <{settings.email_from or settings.smtp_user}>"
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="overheard.local")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    password = settings.smtp_pass.get_secret_value() if settings.smtp_pass else None
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, 465, context=context, timeout=30) as s:
            if settings.smtp_user and password:
                s.login(settings.smtp_user, password)
            s.send_message(msg)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as s:
        s.starttls(context=context)
        if settings.smtp_user and password:
            s.login(settings.smtp_user, password)
        s.send_message(msg)


async def deliver(
    settings: Settings,
    product: Product,
    brief: Brief,
    run_key: str = "",
    recipients: list[str] | None = None,
    tz: str | None = None,
) -> str:
    """Send, save or skip a product's morning email, by `EMAIL_MODE`.

    A quiet morning sends a short email only when the product asks for it.

    Args:
        settings: App settings.
        product: The product.
        brief: Its brief.
        run_key: The run (unused in the message, kept for logs).
        recipients: Override the product's recipients.
        tz: The schedule's timezone, which dates a saved digest.

    Returns:
        What happened, for the Run log.
    """
    quiet = brief.total == 0
    if quiet and not product.quiet_day_email:
        return "Quiet morning · no email"
    if quiet:
        subject, html, text = render_quiet(settings, product)
    else:
        subject, html, text = render_digest(settings, product, brief)
    if settings.email_mode == "file":
        write_file(settings, product.slug, subject, html, run=run_key or "run", tz=tz)
        return "Digest saved to .data/digests"
    if settings.email_mode != "smtp":
        return "Email off · digest in the app"
    to = recipients or product.recipients or ([settings.smtp_user] if settings.smtp_user else [])
    if not to:
        return "No recipient · digest in the app"
    await asyncio.to_thread(_send_smtp, settings, to, subject, html, text)
    return "Email sent"


SENT_OUTCOMES = ("Email sent", "Digest saved to .data/digests")


def digest_key(run_key: str, product_id: int) -> str:
    """The idempotency key of one product's digest in one run.

    Args:
        run_key: The run (workflow id).
        product_id: Product id.

    Returns:
        The key.
    """
    return f"{run_key}|{product_id}"


async def deliver_once(
    db: Database,
    settings: Settings,
    product: Product,
    brief: Brief,
    run_key: str,
    recipients: list[str] | None = None,
    tz: str | None = None,
) -> str:
    """Send (or save) a product's digest at most once per run, like the search ledger.

    The ledger is checked first and written right after the mail server accepted the message
    (or the file was written), so a retried activity or a restarted worker doesn't send twice.

    Args:
        db: Database.
        settings: App settings.
        product: The product.
        brief: Its brief.
        run_key: The run (workflow id).
        recipients: Override the product's recipients.
        tz: The schedule's timezone, which dates a saved digest.

    Returns:
        What happened, for the Run log.
    """
    key = digest_key(run_key, product.id)
    if not await db.digest_claim(key, run_key, product.id):
        done = await db.digest_get(key)
        if done is not None and done["status"] == "sent":
            return f"{done['outcome']} · not sent again"
        return "Already being sent · not sent again"
    try:
        outcome = await deliver(settings, product, brief, run_key, recipients, tz)
    except BaseException:
        await db.digest_release(key)
        raise
    if outcome in SENT_OUTCOMES:
        await db.digest_mark_sent(key, outcome)
    else:
        await db.digest_release(key)
    return outcome


async def send_test(settings: Settings, recipient: str | None, tz: str | None = None) -> str:
    """Send (or save) a short test email.

    Args:
        settings: App settings.
        recipient: Where to send it.
        tz: The schedule's timezone, which dates a saved test.

    Returns:
        A short message for a toast.
    """
    html = _env.get_template("test.html").render({"link": settings.app_url})
    subject = "Overheard: test email"
    if settings.email_mode == "file":
        path = write_file(settings, "test", subject, html, run="test", tz=tz)
        return f"Test email saved to {path}"
    if settings.email_mode != "smtp" or not settings.smtp_host:
        return "Email is off, so nothing was sent. Add SMTP_* to .env to send email."
    to = [recipient] if recipient else ([settings.smtp_user] if settings.smtp_user else [])
    if not to:
        return "No address to send to"
    text = f"Overheard can send email. Open the app: {settings.app_url}\n"
    await asyncio.to_thread(_send_smtp, settings, to, subject, html, text)
    return "Test email sent"


async def email_test(settings: Settings) -> int:
    """`overheard email-test`: send a test digest to `SMTP_USER`.

    Args:
        settings: App settings.

    Returns:
        Exit code.
    """
    try:
        message = await send_test(settings, settings.smtp_user)
    except MailError as exc:
        print(f"The test email failed ({exc}). Check SMTP_* in .env")
        return 1
    except (OSError, smtplib.SMTPException) as exc:
        print(f"The test email failed: {type(exc).__name__}. Check SMTP_* in .env")
        return 1
    print(re.sub(r"\S+@\S+", "<you>", message))
    return 0
