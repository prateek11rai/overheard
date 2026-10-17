import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from overheard import pipeline, workflows
from overheard.ai import (
    AI,
    role_models,
)
from overheard.db import Database, Product
from overheard.settings import Settings, timezone_choices

HERE = Path(__file__).parent.parent
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


OOB_PAGES = {
    "_bar.html": lambda path: path != "/setup",
    "_runs_main.html": lambda path: path == "/runs",
    "_runs_runnow.html": lambda path: path == "/runs",
    "_run_steps.html": lambda path: path == "/runs",
    "_timepick.html": lambda path: path == "/settings",
    "_settings_products.html": lambda path: path == "/settings",
    "_reddit_plan.html": lambda path: path == "/settings",
    "_brief_main.html": lambda path: path.startswith("/p/") and path.count("/") == 2,
    "_phrase_badge.html": lambda path: path.startswith("/products/"),
}


def oob_target_on(template: str, path: str) -> bool:
    """Whether an out-of-band fragment's target exists on the page at `path`.

    Args:
        template: The fragment's template name.
        path: The requesting page's path (from HX-Current-URL).

    Returns:
        True when the page has the target (unknown fragments are always sent).
    """
    check = OOB_PAGES.get(template)
    return True if check is None else check(path)


class WebCore:
    """Shared plumbing: settings, templates, the database pool, Temporal and responses."""

    def __init__(self, settings: Settings, templates: Jinja2Templates) -> None:
        """Hold the shared pieces.

        Args:
            settings: App settings.
            templates: The Jinja2 templates.
        """
        self.settings = settings
        self.templates = templates
        self.state: dict[str, Any] = {}

    def _db(self) -> Database:
        return self.state["db"]

    async def _temporal(self):
        if "client" not in self.state:
            self.state["client"] = await asyncio.wait_for(
                workflows.connect(self.settings), timeout=5
            )
        return self.state["client"]

    async def _ai(self) -> AI:
        models = role_models(
            await self._db().get_setting("models"),
            self.settings.chat_model,
            self.settings.embed_model,
        )
        key = self.settings.llm_api_key.get_secret_value() if self.settings.llm_api_key else None
        return AI(self.settings.llm_base_url, models, key, timeout=120)

    async def _tz(self) -> str:
        value = await self._db().get_setting("timezone")
        if not value:
            value = self.settings.default_timezone()
            await self._db().set_setting("timezone", value)
        return value

    def _current_path(self, request: Request) -> str:
        url = request.headers.get("hx-current-url") or str(request.url)
        return urlsplit(url).path

    def _active_slug(self, request: Request) -> str | None:
        m = re.match(r"^/p/([^/]+)", self._current_path(request))
        return m.group(1) if m else None

    def _cost_text(self, product: Product) -> str:
        if product.demo:
            return "demo"
        n = self._reddit_cost(product)
        if self.settings.serpapi_api_key is None or not product.reddit_on:
            return "Hacker News only"
        return f"{n} search{'' if n == 1 else 'es'}"

    def _reddit_cost(self, product: Product) -> int:
        has_key = self.settings.serpapi_api_key is not None
        return pipeline.phrase_cost(
            product.phrases, product.reddit_on and not product.demo, has_key
        )

    async def _temporal_or_none(self) -> Any:
        if self.state.get("temporal_down_until", 0.0) > time.monotonic():
            return None
        try:
            return await self._temporal()
        except Exception:
            self.state["temporal_down_until"] = time.monotonic() + 30
            return None

    async def _reconcile(self) -> None:
        """Mark runs Temporal no longer runs as failed (only when some row says running)."""
        if not await self._db().running_runs():
            return
        client = await self._temporal_or_none()
        if client is None:
            return
        try:
            await workflows.live_runs(client, self._db())
        except Exception:
            self.state["temporal_down_until"] = time.monotonic() + 30

    async def _chrome(self, request: Request, active: str | None = None) -> dict[str, Any]:
        await self._reconcile()
        products = await self._db().products()
        counts = await self._db().open_counts()
        active = active if active is not None else self._active_slug(request)
        tabs = [
            {
                "id": p.id,
                "slug": p.slug,
                "name": p.name,
                "paused": p.paused,
                "count": counts.get(p.id, 0),
                "active": p.slug == active,
                "cost": self._reddit_cost(p),
                "cost_text": self._cost_text(p),
            }
            for p in products
        ]
        morning = await self._db().get_setting("morning_time") or workflows.DEFAULT_TIME
        paused_all = bool(await self._db().get_setting("paused_all", False))
        last = await self._db().last_run("morning")
        zone = ZoneInfo(await self._tz())
        last_text = None
        if last is not None:
            local = last.started_at.astimezone(zone)
            day = "today" if local.date() == datetime.now(zone).date() else f"{local:%a %d %b}"
            last_text = (f"{day} {local:%H:%M}", last.status)
        running = await self._db().running_runs()
        busy = any(r.kind in ("morning", "check") for r in running)
        morning_running = any(r.kind == "morning" for r in running)
        for tab in tabs:
            tab["checking"] = morning_running or any(
                r.kind == "check" and r.product_id == tab["id"] for r in running
            )
        run_cost = sum(self._reddit_cost(p) for p in products if not p.paused)
        active_tab = next((t for t in tabs if t["active"]), None)
        count = active_tab["count"] if active_tab else sum(t["count"] for t in tabs)
        return {
            "tabs": tabs,
            "morning_time": morning,
            "paused_all": paused_all,
            "chip": "Paused" if paused_all else f"Daily at {morning}",
            "presets": TIME_PRESETS,
            "last_run": last_text,
            "run_cost": run_cost,
            "run_cost_text": (
                "Hacker News only"
                if self.settings.serpapi_api_key is None
                else f"{run_cost} search{'' if run_cost == 1 else 'es'}"
            ),
            "has_demo": any(p.demo for p in products),
            "title_count": count,
            "temporal_ui": self.settings.temporal_ui_url,
            "busy": busy,
            "tz_name": zone.key,
            "tz_choices": timezone_choices(self.settings.default_timezone(), zone.key),
        }

    def _page(
        self, request: Request, name: str, ctx: dict[str, Any], status: int = 200
    ) -> HTMLResponse:
        done = request.query_params.get("done")
        ctx = {**ctx, "done_message": DONE_MESSAGES.get(done or "")}
        return self.templates.TemplateResponse(request, name, ctx, status_code=status)

    def _fragment(
        self,
        request: Request,
        name: str | None,
        ctx: dict[str, Any],
        toast: str | None = None,
        undo: str | None = None,
        oob: list[str] | None = None,
        close_popovers: bool = True,
    ) -> HTMLResponse:
        parts = []
        if name:
            parts.append(self.templates.get_template(name).render({**ctx, "request": request}))
        path = self._current_path(request)
        for o in oob or []:
            if not oob_target_on(o, path):
                continue
            parts.append(
                self.templates.get_template(o).render({**ctx, "request": request, "oob": True})
            )
        response = HTMLResponse("".join(parts))
        trigger: dict[str, Any] = {}
        if toast:
            trigger["toast"] = {"text": toast, **({"undo": undo} if undo else {})}
        if "title_count" in ctx:
            n = ctx["title_count"]
            trigger["title"] = {"value": f"({n}) overheard" if n else "overheard"}
        if close_popovers:
            trigger["closePopovers"] = True
        response.headers["HX-Trigger"] = json.dumps(trigger)
        if name is None:
            response.headers["HX-Reswap"] = "none"
        return response

    def _toast_only(self, message: str) -> Response:
        response = Response("", status_code=200)
        response.headers["HX-Trigger"] = json.dumps({"toast": {"text": message}})
        response.headers["HX-Reswap"] = "none"
        return response
