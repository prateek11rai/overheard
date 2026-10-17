import hashlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from overheard import pipeline
from overheard.db import Database
from overheard.settings import Settings, get_settings
from overheard.web import actions, pages
from overheard.web.context import STATIC_DIR, TEMPLATES_DIR
from overheard.web.security import SecurityMiddleware, parse_emails, safe_url, slugify
from overheard.web.updates import Web

__all__ = ["SecurityMiddleware", "create_app", "parse_emails", "safe_url", "slugify"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the web app.

    Args:
        settings: App settings (defaults to the environment).

    Returns:
        The FastAPI app.

    Raises:
        RuntimeError: If it would listen beyond localhost without `OVERHEARD_PASSWORD`.
    """
    settings = settings or get_settings()
    if not settings.is_local_host and settings.overheard_password is None:
        raise RuntimeError("Set OVERHEARD_PASSWORD before listening beyond 127.0.0.1")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters["safe_url"] = safe_url
    templates.env.globals["label_name"] = pipeline.label_text
    digest = hashlib.sha256()
    for name in ("app.css", "app.js", "htmx.min.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    templates.env.globals["static_version"] = digest.hexdigest()[:10]
    web = Web(settings, templates)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.dsn, settings.overheard_db_schema)
        await db.open()
        web.state["db"] = db
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    password = (
        settings.overheard_password.get_secret_value() if settings.overheard_password else None
    )
    app.add_middleware(SecurityMiddleware, password=password)
    if settings.overheard_host in ("0.0.0.0", "::"):  # noqa: S104
        allowed = ["*"]
    else:
        allowed = ["127.0.0.1", "localhost", "[::1]", settings.overheard_host]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    pages.register(app, web)
    actions.register(app, web)
    return app
