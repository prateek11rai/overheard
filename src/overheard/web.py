import asyncio
import base64
import csv
import hashlib
import io
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from overheard import pipeline, workflows
from overheard.ai import (
    AI,
    CHAT_ROLES,
    EMBED_ROLE,
    ModelUnavailableError,
    model_status,
    role_models,
)
from overheard.db import Database, Product
from overheard.settings import Settings, get_settings, local_timezone

HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"
TIME_PRESETS = ["06:00", "06:30", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30"]
DONE_MESSAGES = {
    "deleted": "Product deleted",
    "saved": "Saved",
    "demo-cleared": "Demo data removed",
    "data-deleted": "Everything deleted",
    "added": "Listening. First check started",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A URL slug from a product name.

    Args:
        name: Product name.

    Returns:
        Lowercase words joined by dashes (or `product`).
    """
    return SLUG_RE.sub("-", name.lower()).strip("-")[:40] or "product"


def safe_url(url: str | None) -> str:
    """Only http(s) links reach the page; anything else becomes `#`.

    Args:
        url: A link from a post.

    Returns:
        The link, or `#`.
    """
    if not url:
        return "#"
    scheme = urlsplit(url).scheme.lower()
    return url if scheme in ("http", "https") else "#"


def parse_emails(raw: str) -> list[str]:
    """Split and check email addresses typed in a form.

    Args:
        raw: Addresses separated by commas, semicolons or spaces.

    Returns:
        The valid addresses, in order, without duplicates.
    """
    out: list[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        if part and EMAIL_RE.match(part) and part not in out:
            out.append(part)
    return out[:10]


class SecurityMiddleware(BaseHTTPMiddleware):
    """Password (when set), same-origin POSTs and security headers."""

    def __init__(self, app: Any, password: str | None) -> None:
        """Wrap the app.

        Args:
            app: The ASGI app.
            password: `OVERHEARD_PASSWORD`, or None on localhost.
        """
        super().__init__(app)
        self.password = password

    def _authorized(self, request: Request) -> bool:
        if not self.password or request.url.path == "/healthz":
            return True
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
        except ValueError:
            return False
        _, _, given = decoded.partition(":")
        return secrets.compare_digest(given.encode(), self.password.encode())

    @staticmethod
    def _same_origin(request: Request) -> bool:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            if not referer:
                return False
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc == host

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Check the request, then add security headers to the response.

        Args:
            request: The request.
            call_next: The next handler.

        Returns:
            The response.
        """
        if not self._authorized(request):
            return Response(
                "Password required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="overheard"'},
            )
        if request.method not in ("GET", "HEAD", "OPTIONS") and not self._same_origin(request):
            return Response("Cross-origin request refused", status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response


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
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.dsn, settings.overheard_db_schema)
        await db.open()
        state["db"] = db
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
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.filters["safe_url"] = safe_url
    digest = hashlib.sha256()
    for name in ("app.css", "app.js", "htmx.min.js"):
        digest.update((STATIC_DIR / name).read_bytes())
    templates.env.globals["static_version"] = digest.hexdigest()[:10]

    def db() -> Database:
        return state["db"]

    async def temporal():
        if "client" not in state:
            state["client"] = await asyncio.wait_for(workflows.connect(settings), timeout=5)
        return state["client"]

    async def ai() -> AI:
        models = role_models(
            await db().get_setting("models"), settings.chat_model, settings.embed_model
        )
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        return AI(settings.llm_base_url, models, key, timeout=120)

    async def tz() -> str:
        value = await db().get_setting("timezone")
        if not value:
            value = local_timezone()
            await db().set_setting("timezone", value)
        return value

    def current_path(request: Request) -> str:
        url = request.headers.get("hx-current-url") or str(request.url)
        return urlsplit(url).path

    def active_slug(request: Request) -> str | None:
        m = re.match(r"^/p/([^/]+)", current_path(request))
        return m.group(1) if m else None

    def reddit_cost(product: Product) -> int:
        if settings.serpapi_api_key is None or not product.reddit_on or product.demo:
            return 0
        return len(product.phrases)

    async def chrome(request: Request, active: str | None = None) -> dict[str, Any]:
        products = await db().products()
        counts = await db().open_counts()
        active = active if active is not None else active_slug(request)
        tabs = [
            {
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "paused": p.paused,
                "count": counts.get(p.id, 0),
                "active": p.slug == active,
                "cost": reddit_cost(p),
            }
            for p in products
        ]
        morning = await db().get_setting("morning_time") or workflows.DEFAULT_TIME
        paused_all = bool(await db().get_setting("paused_all", False))
        last = await db().last_run("morning")
        zone = ZoneInfo(await tz())
        last_text = None
        if last is not None:
            local = last.started_at.astimezone(zone)
            day = "today" if local.date() == datetime.now(zone).date() else f"{local:%a %d %b}"
            last_text = (f"{day} {local:%H:%M}", last.status)
        active_tab = next((t for t in tabs if t["active"]), None)
        count = active_tab["count"] if active_tab else sum(t["count"] for t in tabs)
        return {
            "tabs": tabs,
            "morning_time": morning,
            "paused_all": paused_all,
            "chip": "Paused" if paused_all else f"Daily at {morning}",
            "presets": TIME_PRESETS,
            "last_run": last_text,
            "run_cost": sum(reddit_cost(p) for p in products if not p.paused),
            "has_demo": any(p.demo for p in products),
            "title_count": count,
            "temporal_ui": settings.temporal_ui_url,
        }

    def page(request: Request, name: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
        done = request.query_params.get("done")
        ctx = {**ctx, "done_message": DONE_MESSAGES.get(done or "")}
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def fragment(
        request: Request,
        name: str | None,
        ctx: dict[str, Any],
        toast: str | None = None,
        undo: str | None = None,
        oob: list[str] | None = None,
    ) -> HTMLResponse:
        parts = []
        if name:
            parts.append(templates.get_template(name).render({**ctx, "request": request}))
        for o in oob or []:
            parts.append(templates.get_template(o).render({**ctx, "request": request, "oob": True}))
        response = HTMLResponse("".join(parts))
        trigger: dict[str, Any] = {}
        if toast:
            trigger["toast"] = {"text": toast, "undo": undo}
        if "title_count" in ctx:
            n = ctx["title_count"]
            trigger["title"] = {"value": f"({n}) overheard" if n else "overheard"}
        trigger["closePopovers"] = True
        response.headers["HX-Trigger"] = json.dumps(trigger)
        if name is None:
            response.headers["HX-Reswap"] = "none"
        return response

    def toast_only(message: str) -> Response:
        response = Response("", status_code=200)
        response.headers["HX-Trigger"] = json.dumps({"toast": {"text": message}})
        response.headers["HX-Reswap"] = "none"
        return response

    # pages

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        try:
            health = await db().health()
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True, "tables": health["tables"]})

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        products = await db().products()
        done = request.query_params.get("done")
        suffix = f"?done={done}" if done in DONE_MESSAGES else ""
        if not products:
            return RedirectResponse(f"/setup{suffix}", status_code=303)
        return RedirectResponse(f"/p/{products[0].slug}{suffix}", status_code=303)

    async def brief_ctx(request: Request, product: Product) -> dict[str, Any]:
        brief = await pipeline.brief_for(db(), product, await tz())
        ctx = await chrome(request, product.slug)
        themes = []
        if brief.total == 0:
            themes = (await themes_ctx(product))["themes"][:3]
        return {
            **ctx,
            "product": product,
            "brief": brief,
            "show_hidden": request.query_params.get("show") == "hidden"
            or "show=hidden" in (request.headers.get("hx-current-url") or ""),
            "hidden_posts": await db().hidden_posts(product.id),
            "quiet_themes": themes,
        }

    @app.get("/p/{slug}", response_class=HTMLResponse)
    async def brief_page(request: Request, slug: str) -> Response:
        product = await db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        return page(request, "brief.html", await brief_ctx(request, product))

    async def review_ctx(request: Request, product: Product, post_id: int | None) -> dict:
        brief = await pipeline.brief_for(db(), product, await tz())
        cards = brief.cards
        idx = 0
        if post_id is not None:
            for i, c in enumerate(cards):
                if c.post.id == post_id:
                    idx = i
                    break
        card = cards[idx] if cards else None
        ctx = await chrome(request, product.slug)
        return {
            **ctx,
            "product": product,
            "brief": brief,
            "card": card,
            "idx": idx,
            "order": brief.order,
        }

    @app.get("/p/{slug}/review", response_class=HTMLResponse)
    async def review_page(request: Request, slug: str, post: int | None = None) -> Response:
        product = await db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        return page(request, "review.html", await review_ctx(request, product, post))

    async def themes_ctx(product: Product) -> dict[str, Any]:
        now = datetime.now(UTC)
        stats = await db().theme_stats(product.id)
        out = []
        rank = {"up": 0, "new": 1, "": 2}
        trends = {s.id: pipeline.theme_trend(s, now)[0] for s in stats}
        ordered = sorted(
            stats, key=lambda s: (rank[trends[s.id]], -pipeline.growth(s), -s.posts_4w, s.id)
        )
        for s in ordered:
            if s.posts_4w == 0:
                continue
            trend = trends[s.id]
            if trend == "up":
                text = f"↑ {s.posts_4w} posts · {s.weekly[0]} this week"
            elif trend == "new":
                text = f"new · {s.total} post{'s' if s.total != 1 else ''}"
            else:
                text = f"{s.posts_4w} post{'s' if s.posts_4w != 1 else ''}"
            posts = await db().theme_posts(s.id, 2)
            out.append({"stat": s, "trend": trend, "trend_text": text, "posts": posts})
        return {"themes": out, "posts_4w": await db().useful_count(product.id, 28)}

    @app.get("/p/{slug}/themes", response_class=HTMLResponse)
    async def themes_page(request: Request, slug: str) -> Response:
        product = await db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        ctx = await chrome(request, slug)
        return page(
            request, "themes.html", {**ctx, "product": product, **await themes_ctx(product)}
        )

    @app.get("/p/{slug}/themes/{theme_id}", response_class=HTMLResponse)
    async def theme_page(request: Request, slug: str, theme_id: int) -> Response:
        product = await db().product_by_slug(slug)
        theme = await db().theme(theme_id)
        if product is None or theme is None or theme["product_id"] != product.id:
            return RedirectResponse(f"/p/{slug}/themes", status_code=303)
        posts = await db().theme_posts(theme_id)
        ctx = await chrome(request, slug)
        return page(
            request, "theme.html", {**ctx, "product": product, "theme": theme, "posts": posts}
        )

    async def setup_ctx(request: Request) -> dict[str, Any]:
        products = await db().products()
        async with await ai() as client:
            found = await client.discover()
            chat_in_use = client.model_for("label")
        checks = await system_checks(found)
        recipient = await db().get_setting("default_recipient") or ""
        email_done = bool(recipient) or bool(await db().get_setting("email_skipped", False))
        model_done = found.find(chat_in_use) is not None
        steps_done = [model_done, email_done, bool(products)]
        now_step = next((i for i, d in enumerate(steps_done) if not d), 3)
        return {
            **await chrome(request),
            "found": found,
            "chat_in_use": chat_in_use,
            "checks": checks,
            "failing": [c for c in checks if not c["ok"]],
            "recipient": recipient,
            "steps_done": steps_done,
            "now_step": now_step,
            "has_products": bool(products),
        }

    async def system_checks(found: Any) -> list[dict[str, Any]]:
        checks = [{"name": "Postgres + pgvector", "ok": True, "fix": ""}]
        try:
            await db().health()
        except Exception:
            checks[0] = {"name": "Postgres + pgvector", "ok": False, "fix": "Check DATABASE_URL"}
        checks.append(
            {
                "name": "Model server",
                "ok": found.reachable,
                "fix": f"Start Ollama at {settings.llm_base_url} (ollama serve)",
            }
        )
        status = await workflows.temporal_status(settings)
        checks.append(
            {"name": "Temporal", "ok": status["temporal"], "fix": "Run uv run overheard start"}
        )
        checks.append(
            {"name": "Worker", "ok": status["worker"], "fix": "Run uv run overheard start"}
        )
        return checks

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> Response:
        return page(request, "setup.html", await setup_ctx(request))

    def product_form_ctx(product: Product | None, recipient: str) -> dict[str, Any]:
        if product is None:
            return {
                "product": None,
                "form": {
                    "name": "",
                    "description": "",
                    "phrases": [],
                    "competitors": [],
                    "subreddits": [],
                    "sources": "reddit+hn",
                    "recipients": recipient,
                    "quiet_day_email": False,
                },
            }
        return {
            "product": product,
            "form": {
                "name": product.name,
                "description": product.description,
                "phrases": product.phrases,
                "competitors": product.competitors,
                "subreddits": product.subreddits,
                "sources": product.sources,
                "recipients": ", ".join(product.recipients),
                "quiet_day_email": product.quiet_day_email,
            },
        }

    def phrase_badge(phrases: list[str], sources: str) -> str:
        n = len(phrases)
        per_day = n if sources == "reddit+hn" and settings.serpapi_api_key is not None else 0
        return (
            f"{n} phrase{'s' if n != 1 else ''} · {per_day} search{'es' if per_day != 1 else ''}"
            " a day"
        )

    @app.get("/products/new", response_class=HTMLResponse)
    async def new_product(request: Request) -> Response:
        recipient = await db().get_setting("default_recipient") or ""
        ctx = {**await chrome(request, ""), **product_form_ctx(None, recipient)}
        ctx["badge"] = phrase_badge([], "reddit+hn")
        ctx["first_cost"] = 0
        return page(request, "product_form.html", ctx)

    @app.get("/products/{product_id}/edit", response_class=HTMLResponse)
    async def edit_product(request: Request, product_id: int) -> Response:
        product = await db().product(product_id)
        if product is None:
            return RedirectResponse("/", status_code=303)
        ctx = {**await chrome(request, product.slug), **product_form_ctx(product, "")}
        ctx["badge"] = phrase_badge(product.phrases, product.sources)
        ctx["first_cost"] = reddit_cost(product)
        return page(request, "product_form.html", ctx)

    def form_fields(form: Any) -> dict[str, Any]:
        sources = form.get("sources") if form.get("sources") in ("reddit+hn", "hn") else "reddit+hn"
        subs_mode = form.get("subs_mode", "all")
        subreddits = pipeline.clean_phrases(form.getlist("subreddits"), limit=20)
        subreddits = [s.removeprefix("r/") for s in subreddits] if subs_mode == "only" else []
        return {
            "name": (form.get("name") or "").strip()[:60],
            "description": (form.get("description") or "").strip()[:500],
            "phrases": pipeline.clean_phrases(form.getlist("phrases"), limit=12),
            "competitors": [c.strip()[:40] for c in form.getlist("competitors") if c.strip()][:10],
            "subreddits": subreddits,
            "sources": sources,
            "recipients": parse_emails(form.get("recipients", "")),
            "quiet_day_email": form.get("quiet") == "send",
        }

    @app.post("/products")
    async def create_product(request: Request) -> Response:
        fields = form_fields(await request.form())
        if not fields["name"]:
            return RedirectResponse("/products/new", status_code=303)
        fields["slug"] = await db().free_slug(slugify(fields["name"]))
        product = await db().create_product(**fields)
        try:
            await workflows.start_check(await temporal(), db(), product.id, product.slug)
            done = "added"
        except Exception:
            done = "saved"
        return RedirectResponse(f"/p/{product.slug}?done={done}", status_code=303)

    @app.post("/products/{product_id}")
    async def save_product(request: Request, product_id: int) -> Response:
        product = await db().product(product_id)
        if product is None:
            return RedirectResponse("/", status_code=303)
        fields = form_fields(await request.form())
        if not fields["name"]:
            fields["name"] = product.name
        await db().update_product(product_id, **fields)
        return RedirectResponse(f"/products/{product_id}/edit?done=saved", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request, run: int | None = None) -> Response:
        return page(request, "runs.html", {**await chrome(request, ""), **await runs_ctx(run)})

    async def run_view(r: Any, zone: ZoneInfo) -> dict[str, Any]:
        local = r.started_at.astimezone(zone)
        if r.status == "running":
            result, css = "running…", "dim"
        elif r.status == "failed":
            result, css = "failed · " + (r.note or "see the steps"), "bad"
        elif r.status == "hn_only":
            result, css = "Hacker News only · " + (r.note or "search plan used up"), "warn"
        else:
            took = (r.finished_at - r.started_at) if r.finished_at else timedelta()
            secs = int(took.total_seconds())
            dur = f"{secs // 60}m {secs % 60:02d}s" if secs >= 60 else f"{secs}s"
            result, css = "done · " + (r.note or dur), "ok"
        kind = {
            "morning": "",
            "check": f"Check · {r.product_name or 'product'}",
            "reembed": "Re-embed",
            "compare": "Compare",
        }[r.kind]
        return {
            "run": r,
            "date": f"{local:%a %d %b}",
            "time": f"{local:%H:%M}",
            "kind": kind,
            "result": result,
            "css": css,
        }

    async def runs_ctx(selected: int | None) -> dict[str, Any]:
        zone = ZoneInfo(await tz())
        runs = await db().runs(20)
        views = [await run_view(r, zone) for r in runs]
        sel = next((v for v in views if v["run"].id == selected), views[0] if views else None)
        return {"runs": views, "selected": sel, "groups": await step_groups(sel)}

    async def step_groups(sel: dict | None) -> list[dict[str, Any]]:
        if sel is None:
            return []
        steps = await db().steps(sel["run"].id)
        names = {p.id: p.name for p in await db().products()}
        groups: dict[Any, dict] = {}
        for s in steps:
            key = s.product_id
            if key not in groups:
                name = names.get(key, "") if key is not None else ""
                groups[key] = {"name": name, "steps": []}
            groups[key]["steps"].append(s)
        return list(groups.values())

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        return page(request, "settings.html", {**await chrome(request, ""), **await settings_ctx()})

    async def models_ctx() -> dict[str, Any]:
        async with await ai() as client:
            found = await client.discover()
            chat_in_use = client.model_for("label")
            embed_in_use = client.model_for(EMBED_ROLE)
        chat = [m.name for m in found.chat]
        embed = [m.name for m in found.embedding]
        if chat_in_use not in chat:
            chat.insert(0, chat_in_use)
        if embed_in_use not in embed:
            embed.insert(0, embed_in_use)
        evals = {r["model"]: r for r in await db().latest_evals("checked")}
        recent = {r["model"]: r for r in await db().latest_evals("recent")}
        compare_rows = []
        for name in dict.fromkeys([*chat, *evals]):
            r = evals.get(name)
            compare_rows.append(
                {
                    "model": name,
                    "in_use": name == chat_in_use,
                    "installed": found.find(name) is not None,
                    "right": f"{r['right_label']}/{r['items']}" if r else "—",
                    "quote": f"{round(100 * r['quote_ok'] / r['items'])}%"
                    if r and r["items"]
                    else "—",
                    "per_post": f"{r['seconds_per_post']:.1f}s" if r else "—",
                    "recent": recent.get(name),
                }
            )
        others = [m for m in chat if m != chat_in_use and found.find(m) is not None]
        return {
            "found": found,
            "chat_models": [(m, model_status(m, chat_in_use, found)) for m in chat],
            "embed_models": [(m, model_status(m, embed_in_use, found)) for m in embed],
            "chat_in_use": chat_in_use,
            "embed_in_use": embed_in_use,
            "compare_rows": compare_rows,
            "compare_next": others[0] if others else None,
            "is_ollama": found.server == "ollama",
        }

    async def reddit_ctx() -> dict[str, Any]:
        products = [p for p in await db().products() if not p.paused and p.reddit_on]
        phrases = sum(len(p.phrases) for p in products)
        today = datetime.now(ZoneInfo(await tz())).date()
        import calendar

        days = calendar.monthrange(today.year, today.month)[1]
        account = await db().get_setting("serpapi_account")
        has_key = settings.serpapi_api_key is not None
        projected = phrases * days
        plan = account["plan_searches"] if account and has_key else None
        if plan:
            left = account["searches_left"] - round(plan * pipeline.RESERVE)
            days_left = pipeline.days_left_in_month(today)
            fits = phrases * days_left <= max(left, 0)
            meter = min(100, round(100 * projected / plan)) if plan else 0
        else:
            fits, meter = None, 0
        return {
            "phrases": phrases,
            "days": days,
            "projected": projected,
            "plan": plan,
            "fits": fits,
            "meter": meter,
            "has_key": has_key,
        }

    async def settings_ctx() -> dict[str, Any]:
        products = await db().products()
        mode = settings.email_mode
        return {
            "products": products,
            "tz_name": await tz(),
            "reddit": await reddit_ctx(),
            "email_mode": mode,
            "digests_dir": str(settings.digests_dir),
            **await models_ctx(),
        }

    # button routes

    async def post_view(
        request: Request, post_id: int, pos: int | None, show: int | None = None
    ) -> HTMLResponse:
        post = await db().post(post_id)
        product = await db().product(post.product_id) if post else None
        if product is None:
            return fragment(request, None, {}, toast="That post is gone")
        if current_path(request).endswith("/review"):
            next_id = show
            if next_id is None:
                brief = await pipeline.brief_for(db(), product, await tz())
                if brief.order:
                    next_id = brief.order[min(pos or 0, len(brief.order) - 1)]
            ctx = await review_ctx(request, product, next_id)
            return fragment(request, "_review_main.html", ctx, oob=["_bar.html"])
        ctx = await brief_ctx(request, product)
        return fragment(request, "_brief_main.html", ctx, oob=["_bar.html"])

    async def _post_action(request: Request, post_id: int, action: str) -> Response:
        form = await request.form()
        pos_raw = form.get("pos")
        pos = int(pos_raw) if isinstance(pos_raw, str) and pos_raw.isdigit() else None
        if action == "done":
            post = await db().set_post_status(post_id, "done")
            message = "Done · next post" if pos is not None else "Marked done"
        elif action == "skip":
            post = await db().set_post_status(post_id, "skipped", await next_morning())
            message = "Skipped · back tomorrow"
        else:
            post = await db().set_post_status(post_id, "new", None)
            message = "Back in the brief"
        if post is None:
            return toast_only("That post is gone")
        show = post_id if action == "undo" else None
        response = await post_view(request, post_id, pos, show)
        trigger = json.loads(response.headers["HX-Trigger"])
        trigger["toast"] = {
            "text": message,
            "undo": None if action == "undo" else f"/api/posts/{post_id}/undo",
        }
        response.headers["HX-Trigger"] = json.dumps(trigger)
        return response

    async def next_morning() -> datetime:
        zone = ZoneInfo(await tz())
        h, m = workflows.parse_time(
            await db().get_setting("morning_time") or workflows.DEFAULT_TIME
        )
        now = datetime.now(zone)
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    @app.post("/api/posts/{post_id}/done", response_class=HTMLResponse)
    async def post_done(request: Request, post_id: int) -> Response:
        return await _post_action(request, post_id, "done")

    @app.post("/api/posts/{post_id}/skip", response_class=HTMLResponse)
    async def post_skip(request: Request, post_id: int) -> Response:
        return await _post_action(request, post_id, "skip")

    @app.post("/api/posts/{post_id}/undo", response_class=HTMLResponse)
    async def post_undo(request: Request, post_id: int) -> Response:
        return await _post_action(request, post_id, "undo")

    @app.post("/api/posts/{post_id}/draft", response_class=HTMLResponse)
    async def post_draft(request: Request, post_id: int, style: str = "first") -> Response:
        post = await db().post(post_id)
        product = await db().product(post.product_id) if post else None
        if post is None or product is None:
            return toast_only("That post is gone")
        style = style if style in ("first", "shorter", "rewrite") else "first"
        form = await request.form()
        previous = form.get("reply") if isinstance(form.get("reply"), str) else post.reply_draft
        if style == "first" and post.reply_draft:
            draft = post.reply_draft
        else:
            try:
                async with await ai() as client:
                    draft = await pipeline.draft_reply(
                        client, product, post.title, post.body, style, previous
                    )
            except (ModelUnavailableError, Exception):
                return fragment(
                    request,
                    "_reply_box.html",
                    {"post": post, "draft": previous or "", "error": True},
                    toast="The model didn't answer. Is Ollama running?",
                )
            await db().set_reply_draft(post_id, draft)
        return fragment(request, "_reply_box.html", {"post": post, "draft": draft})

    async def product_changed(request: Request, message: str) -> Response:
        ctx = await chrome(request)
        ctx["products"] = await db().products()
        oob = ["_bar.html"]
        if current_path(request) == "/settings":
            oob.append("_settings_products.html")
        slug = active_slug(request)
        product = await db().product_by_slug(slug) if slug else None
        if product is not None and not current_path(request).endswith(("/review", "/themes")):
            ctx = {**ctx, **await brief_ctx(request, product)}
            ctx["products"] = await db().products()
            oob.append("_brief_main.html")
        return fragment(request, None, ctx, toast=message, oob=oob)

    @app.post("/api/products/{product_id}/pause", response_class=HTMLResponse)
    async def product_pause(request: Request, product_id: int) -> Response:
        product = await db().update_product(product_id, status="paused")
        if product is None:
            return toast_only("That product is gone")
        return await product_changed(request, f"{product.name} paused")

    @app.post("/api/products/{product_id}/resume", response_class=HTMLResponse)
    async def product_resume(request: Request, product_id: int) -> Response:
        product = await db().update_product(product_id, status="active")
        if product is None:
            return toast_only("That product is gone")
        return await product_changed(request, f"{product.name} resumed")

    @app.delete("/api/products/{product_id}")
    async def product_delete(product_id: int) -> Response:
        await db().delete_product(product_id)
        response = Response("")
        response.headers["HX-Redirect"] = "/?done=deleted"
        return response

    @app.post("/api/products/{product_id}/check", response_class=HTMLResponse)
    async def product_check(request: Request, product_id: int) -> Response:
        product = await db().product(product_id)
        if product is None:
            return toast_only("That product is gone")
        try:
            await workflows.start_check(await temporal(), db(), product.id, product.slug)
        except Exception:
            return toast_only("Temporal isn't running. Start it with uv run overheard start")
        return fragment(request, None, await chrome(request), toast=f"Checking {product.name} now")

    async def schedule_changed(request: Request, message: str) -> Response:
        t, zone, paused = await workflows.schedule_settings(db())
        try:
            await workflows.sync_schedule(await temporal(), t, zone, paused)
        except Exception:
            message += " · Temporal isn't running, the schedule updates when it starts"
        ctx = {**await chrome(request), "tz_name": zone}
        return fragment(request, None, ctx, toast=message, oob=["_bar.html", "_timepick.html"])

    @app.post("/api/schedule/time", response_class=HTMLResponse)
    async def schedule_time(request: Request, time: str = Form(...)) -> Response:
        try:
            h, m = workflows.parse_time(time)
        except ValueError:
            return toast_only("That isn't a time")
        value = f"{h:02d}:{m:02d}"
        await db().set_setting("morning_time", value)
        return await schedule_changed(request, f"Every morning at {value}")

    @app.post("/api/schedule/step", response_class=HTMLResponse)
    async def schedule_step(request: Request, minutes: int = Form(...)) -> Response:
        h, m = workflows.parse_time(
            await db().get_setting("morning_time") or workflows.DEFAULT_TIME
        )
        total = (h * 60 + m + max(-60, min(60, minutes))) % (24 * 60)
        value = f"{total // 60:02d}:{total % 60:02d}"
        await db().set_setting("morning_time", value)
        return await schedule_changed(request, f"Every morning at {value}")

    @app.post("/api/schedule/pause-all", response_class=HTMLResponse)
    async def schedule_pause_all(request: Request) -> Response:
        paused = not bool(await db().get_setting("paused_all", False))
        await db().set_setting("paused_all", paused)
        return await schedule_changed(
            request, "All listening paused" if paused else "Listening again"
        )

    @app.post("/api/runs/now", response_class=HTMLResponse)
    async def run_now(request: Request) -> Response:
        try:
            await workflows.start_run_now(await temporal(), db())
        except Exception:
            return toast_only("Temporal isn't running. Start it with uv run overheard start")
        ctx = {**await chrome(request), **await runs_ctx(None)}
        oob = ["_runs_main.html"] if current_path(request) == "/runs" else []
        return fragment(request, None, ctx, toast="Morning brief started", oob=["_bar.html", *oob])

    @app.get("/api/runs/{run_id}/row", response_class=HTMLResponse)
    async def run_row(request: Request, run_id: int, sel: int | None = None) -> Response:
        r = await db().run(run_id)
        if r is None:
            return HTMLResponse("")
        view = await run_view(r, ZoneInfo(await tz()))
        ctx: dict[str, Any] = {"v": view, "selected_id": sel}
        html = templates.get_template("_run_row.html").render({**ctx, "request": request})
        if sel == run_id:
            groups = await step_groups(view)
            html += templates.get_template("_run_steps.html").render(
                {"selected": view, "groups": groups, "request": request, "oob": True}
            )
        return HTMLResponse(html)

    @app.post("/api/models/use", response_class=HTMLResponse)
    async def models_use(request: Request) -> Response:
        form = await request.form()
        stored = dict(await db().get_setting("models") or {})
        chat, embed = form.get("chat"), form.get("embed")
        message = None
        if isinstance(chat, str) and chat:
            for role in (*CHAT_ROLES, "chat"):
                stored[role] = chat
            message = f"Now using {chat}"
        reembed = None
        if isinstance(embed, str) and embed and embed != stored.get("embed"):
            current = role_models(stored, settings.chat_model, settings.embed_model)["embed"]
            if embed != current:
                stored["embed"] = embed
                reembed = embed
                message = f"Embeddings now {embed} · re-embedding in the background"
        await db().set_setting("models", stored)
        if reembed:
            try:
                await workflows.start_reembed(await temporal(), reembed)
            except Exception:
                message = f"Embeddings now {reembed} · start Temporal to re-embed"
        ctx = {**await chrome(request), **await models_ctx()}
        target = form.get("target")
        if target == "setup":
            return fragment(request, None, ctx, toast=message or "Saved")
        return fragment(request, "_models.html", ctx, toast=message or "Saved")

    @app.get("/api/models")
    async def models_list(request: Request) -> Response:
        if request.headers.get("hx-request"):
            ctx = await models_ctx()
            html = templates.get_template("_model_cards.html").render({**ctx, "request": request})
            return HTMLResponse(html)
        ctx = await models_ctx()
        found = ctx["found"]
        return JSONResponse(
            {
                "reachable": found.reachable,
                "server": found.server,
                "chat": [{"name": m, "status": s} for m, s in ctx["chat_models"]],
                "embedding": [{"name": m, "status": s} for m, s in ctx["embed_models"]],
            }
        )

    @app.post("/api/models/compare", response_class=HTMLResponse)
    async def models_compare(request: Request) -> Response:
        ctx = await models_ctx()
        model = ctx["compare_next"]
        if not model:
            return toast_only("Install a second chat model to compare")
        try:
            await workflows.start_compare(await temporal(), model)
        except Exception:
            return toast_only("Temporal isn't running. Start it with uv run overheard start")
        return toast_only(f"Comparing {model} on your last 50 posts · see Run log")

    @app.post("/api/email/test", response_class=HTMLResponse)
    async def email_test(request: Request) -> Response:
        from overheard import mail

        form = await request.form()
        raw = form.get("email")
        if isinstance(raw, str) and raw.strip():
            emails = parse_emails(raw)
            if not emails:
                return toast_only("That doesn't look like an email address")
            await db().set_setting("default_recipient", emails[0])
        recipient = await db().get_setting("default_recipient") or settings.smtp_user
        try:
            message = await mail.send_test(settings, recipient)
        except Exception:
            message = "The test email failed. Check SMTP_* in .env"
        ctx = await chrome(request)
        if form.get("target") == "setup":
            ctx = {**ctx, **await setup_ctx(request)}
            return fragment(request, "_setup_main.html", ctx, toast=message)
        return toast_only(message)

    @app.post("/api/email/skip", response_class=HTMLResponse)
    async def email_skip(request: Request) -> Response:
        await db().set_setting("email_skipped", True)
        ctx = {**await chrome(request), **await setup_ctx(request)}
        return fragment(request, "_setup_main.html", ctx, toast="Skipped · digests stay in the app")

    @app.post("/api/chips/{field}", response_class=HTMLResponse)
    async def chips(request: Request, field: str, remove: int | None = None) -> Response:
        if field not in ("phrases", "competitors", "subreddits"):
            return toast_only("Unknown field")
        form = await request.form()
        items = [i for i in form.getlist(field) if isinstance(i, str)]
        if remove is not None and 0 <= remove < len(items):
            items.pop(remove)
        new = form.get(f"{field}_new")
        if isinstance(new, str) and new.strip():
            items.append(new)
        items = pipeline.clean_phrases(items, 12) if field != "competitors" else items
        if field == "competitors":
            items = list(dict.fromkeys(i.strip()[:40] for i in items if i.strip()))[:10]
        sources = form.get("sources") if form.get("sources") in ("reddit+hn", "hn") else "reddit+hn"
        ctx = {"field": field, "items": items, "badge": phrase_badge(items, str(sources))}
        oob = ["_phrase_badge.html"] if field == "phrases" else []
        return fragment(request, "_chips.html", ctx, oob=oob)

    @app.post("/api/phrases/suggest", response_class=HTMLResponse)
    async def phrases_suggest(request: Request) -> Response:
        form = await request.form()
        items = [i for i in form.getlist("phrases") if isinstance(i, str)]
        name = str(form.get("name") or "").strip()
        description = str(form.get("description") or "").strip()
        sources = form.get("sources") if form.get("sources") in ("reddit+hn", "hn") else "reddit+hn"
        toast = None
        if not items and len(description) >= 12:
            try:
                async with await ai() as client:
                    items = await pipeline.suggest_phrases(client, name, description)
                toast = "Suggested phrases · edit them freely"
            except Exception:
                toast = "Couldn't suggest phrases. Is Ollama running?"
        ctx = {"field": "phrases", "items": items, "badge": phrase_badge(items, str(sources))}
        return fragment(request, "_chips.html", ctx, toast=toast, oob=["_phrase_badge.html"])

    @app.get("/api/export.csv")
    async def export_csv() -> Response:
        rows = await db().export_rows()
        buf = io.StringIO()
        cols = ["product", "source", "url", "title", "community", "posted_at", "found_at"]
        cols += ["label", "quote", "competitor", "status", "theme"]
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v)
                    for k, v in r.items()
                }
            )
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="overheard.csv"'},
        )

    @app.post("/api/data/delete-all")
    async def delete_all() -> Response:
        await db().delete_everything()
        response = Response("")
        response.headers["HX-Redirect"] = "/setup?done=data-deleted"
        return response

    @app.post("/api/demo/clear")
    async def demo_clear() -> Response:
        await db().delete_demo()
        response = Response("")
        response.headers["HX-Redirect"] = "/?done=demo-cleared"
        return response

    return app
