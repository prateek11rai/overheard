import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
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
    EMBED_ROLE,
    model_status,
    role_models,
)
from overheard.db import Database, Product
from overheard.settings import Settings, local_timezone
from overheard.web.security import parse_emails

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


class Web:
    """What every route shares: settings, templates, the database pool and page helpers."""

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
            value = local_timezone()
            await self._db().set_setting("timezone", value)
        return value

    def _current_path(self, request: Request) -> str:
        url = request.headers.get("hx-current-url") or str(request.url)
        return urlsplit(url).path

    def _active_slug(self, request: Request) -> str | None:
        m = re.match(r"^/p/([^/]+)", self._current_path(request))
        return m.group(1) if m else None

    def _reddit_cost(self, product: Product) -> int:
        if self.settings.serpapi_api_key is None or not product.reddit_on or product.demo:
            return 0
        return len(product.phrases)

    async def _chrome(self, request: Request, active: str | None = None) -> dict[str, Any]:
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
        active_tab = next((t for t in tabs if t["active"]), None)
        count = active_tab["count"] if active_tab else sum(t["count"] for t in tabs)
        return {
            "tabs": tabs,
            "morning_time": morning,
            "paused_all": paused_all,
            "chip": "Paused" if paused_all else f"Daily at {morning}",
            "presets": TIME_PRESETS,
            "last_run": last_text,
            "run_cost": sum(self._reddit_cost(p) for p in products if not p.paused),
            "has_demo": any(p.demo for p in products),
            "title_count": count,
            "temporal_ui": self.settings.temporal_ui_url,
            "busy": busy,
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
            trigger["toast"] = {"text": toast, "undo": undo}
        if "title_count" in ctx:
            n = ctx["title_count"]
            trigger["title"] = {"value": f"({n}) overheard" if n else "overheard"}
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

    async def _brief_ctx(self, request: Request, product: Product) -> dict[str, Any]:
        brief = await pipeline.brief_for(self._db(), product, await self._tz())
        ctx = await self._chrome(request, product.slug)
        themes = []
        if brief.total == 0:
            themes = (await self._themes_ctx(product))["themes"][:3]
        return {
            **ctx,
            "product": product,
            "brief": brief,
            "show_hidden": request.query_params.get("show") == "hidden"
            or "show=hidden" in (request.headers.get("hx-current-url") or ""),
            "hidden_posts": await self._db().hidden_posts(product.id),
            "quiet_themes": themes,
        }

    async def _review_ctx(self, request: Request, product: Product, post_id: int | None) -> dict:
        brief = await pipeline.brief_for(self._db(), product, await self._tz())
        cards = brief.cards
        idx = 0
        if post_id is not None:
            for i, c in enumerate(cards):
                if c.post.id == post_id:
                    idx = i
                    break
        card = cards[idx] if cards else None
        ctx = await self._chrome(request, product.slug)
        return {
            **ctx,
            "product": product,
            "brief": brief,
            "card": card,
            "idx": idx,
            "order": brief.order,
        }

    async def _themes_ctx(self, product: Product) -> dict[str, Any]:
        now = datetime.now(UTC)
        stats = await self._db().theme_stats(product.id)
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
            posts = await self._db().theme_posts(s.id, 2)
            out.append({"stat": s, "trend": trend, "trend_text": text, "posts": posts})
        return {"themes": out, "posts_4w": await self._db().useful_count(product.id, 28)}

    async def _setup_ctx(self, request: Request) -> dict[str, Any]:
        products = await self._db().products()
        async with await self._ai() as client:
            found = await client.discover()
            chat_in_use = client.model_for("label")
        checks = await self._system_checks(found)
        recipient = await self._db().get_setting("default_recipient") or ""
        email_done = bool(recipient) or bool(await self._db().get_setting("email_skipped", False))
        model_done = found.find(chat_in_use) is not None
        steps_done = [model_done, email_done, bool(products)]
        now_step = next((i for i, d in enumerate(steps_done) if not d), 3)
        return {
            **await self._chrome(request),
            "found": found,
            "chat_in_use": chat_in_use,
            "checks": checks,
            "failing": [c for c in checks if not c["ok"]],
            "recipient": recipient,
            "steps_done": steps_done,
            "now_step": now_step,
            "has_products": bool(products),
        }

    async def _system_checks(self, found: Any) -> list[dict[str, Any]]:
        checks = [{"name": "Postgres + pgvector", "ok": True, "fix": ""}]
        try:
            await self._db().health()
        except Exception:
            checks[0] = {"name": "Postgres + pgvector", "ok": False, "fix": "Check DATABASE_URL"}
        checks.append(
            {
                "name": "Model server",
                "ok": found.reachable,
                "fix": f"Start Ollama at {self.settings.llm_base_url} (ollama serve)",
            }
        )
        status = await workflows.temporal_status(self.settings)
        checks.append(
            {"name": "Temporal", "ok": status["temporal"], "fix": "Run uv run overheard start"}
        )
        checks.append(
            {"name": "Worker", "ok": status["worker"], "fix": "Run uv run overheard start"}
        )
        return checks

    def _product_form_ctx(self, product: Product | None, recipient: str) -> dict[str, Any]:
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

    def _phrase_badge(self, phrases: list[str], sources: str) -> str:
        n = len(phrases)
        per_day = n if sources == "reddit+hn" and self.settings.serpapi_api_key is not None else 0
        return (
            f"{n} phrase{'s' if n != 1 else ''} · {per_day} search{'es' if per_day != 1 else ''}"
            " a day"
        )

    def _form_fields(self, form: Any) -> dict[str, Any]:
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

    async def _run_view(self, r: Any, zone: ZoneInfo) -> dict[str, Any]:
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

    async def _runs_ctx(self, selected: int | None) -> dict[str, Any]:
        zone = ZoneInfo(await self._tz())
        runs = await self._db().runs(20)
        views = [await self._run_view(r, zone) for r in runs]
        sel = next((v for v in views if v["run"].id == selected), views[0] if views else None)
        return {"runs": views, "selected": sel, "groups": await self._step_groups(sel)}

    async def _step_groups(self, sel: dict | None) -> list[dict[str, Any]]:
        if sel is None:
            return []
        steps = await self._db().steps(sel["run"].id)
        names = {p.id: p.name for p in await self._db().products()}
        groups: dict[Any, dict] = {}
        for s in steps:
            key = s.product_id
            if key not in groups:
                name = names.get(key, "") if key is not None else ""
                groups[key] = {"name": name, "steps": []}
            groups[key]["steps"].append(s)
        return list(groups.values())

    async def _models_ctx(self) -> dict[str, Any]:
        async with await self._ai() as client:
            found = await client.discover()
            chat_in_use = client.model_for("label")
            embed_in_use = client.model_for(EMBED_ROLE)
        chat = [m.name for m in found.chat]
        embed = [m.name for m in found.embedding]
        if chat_in_use not in chat:
            chat.insert(0, chat_in_use)
        if embed_in_use not in embed:
            embed.insert(0, embed_in_use)
        evals = {r["model"]: r for r in await self._db().latest_evals("checked")}
        recent = {r["model"]: r for r in await self._db().latest_evals("recent")}
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

    async def _reddit_ctx(self) -> dict[str, Any]:
        products = [p for p in await self._db().products() if not p.paused and p.reddit_on]
        phrases = sum(len(p.phrases) for p in products)
        today = datetime.now(ZoneInfo(await self._tz())).date()
        import calendar

        days = calendar.monthrange(today.year, today.month)[1]
        account = await self._db().get_setting("serpapi_account")
        has_key = self.settings.serpapi_api_key is not None
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

    async def _settings_ctx(self) -> dict[str, Any]:
        products = await self._db().products()
        mode = self.settings.email_mode
        return {
            "products": products,
            "tz_name": await self._tz(),
            "reddit": await self._reddit_ctx(),
            "email_mode": mode,
            "digests_dir": str(self.settings.digests_dir),
            **await self._models_ctx(),
        }

    async def _post_view(
        self, request: Request, post_id: int, pos: int | None, show: int | None = None
    ) -> HTMLResponse:
        post = await self._db().post(post_id)
        product = await self._db().product(post.product_id) if post else None
        if product is None:
            return self._fragment(request, None, {}, toast="That post is gone")
        if self._current_path(request).endswith("/review"):
            next_id = show
            if next_id is None:
                brief = await pipeline.brief_for(self._db(), product, await self._tz())
                if brief.order:
                    next_id = brief.order[min(pos or 0, len(brief.order) - 1)]
            ctx = await self._review_ctx(request, product, next_id)
            return self._fragment(request, "_review_main.html", ctx, oob=["_bar.html"])
        ctx = await self._brief_ctx(request, product)
        return self._fragment(request, "_brief_main.html", ctx, oob=["_bar.html"])

    async def _post_action(self, request: Request, post_id: int, action: str) -> Response:
        form = await request.form()
        pos_raw = form.get("pos")
        pos = int(pos_raw) if isinstance(pos_raw, str) and pos_raw.isdigit() else None
        if action == "done":
            post = await self._db().set_post_status(post_id, "done")
            message = "Done · next post" if pos is not None else "Marked done"
        elif action == "skip":
            post = await self._db().set_post_status(post_id, "skipped", await self._next_morning())
            message = "Skipped · back tomorrow"
        else:
            post = await self._db().set_post_status(post_id, "new", None)
            message = "Back in the brief"
        if post is None:
            return self._toast_only("That post is gone")
        show = post_id if action == "undo" else None
        response = await self._post_view(request, post_id, pos, show)
        trigger = json.loads(response.headers["HX-Trigger"])
        trigger["toast"] = {
            "text": message,
            "undo": None if action == "undo" else f"/api/posts/{post_id}/undo",
        }
        response.headers["HX-Trigger"] = json.dumps(trigger)
        return response

    async def _next_morning(self) -> datetime:
        zone = ZoneInfo(await self._tz())
        h, m = workflows.parse_time(
            await self._db().get_setting("morning_time") or workflows.DEFAULT_TIME
        )
        now = datetime.now(zone)
        nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    async def _product_changed(self, request: Request, message: str) -> Response:
        ctx = await self._chrome(request)
        ctx["products"] = await self._db().products()
        oob = ["_bar.html"]
        if self._current_path(request) == "/settings":
            oob.append("_settings_products.html")
        slug = self._active_slug(request)
        product = await self._db().product_by_slug(slug) if slug else None
        if product is not None and not self._current_path(request).endswith(("/review", "/themes")):
            ctx = {**ctx, **await self._brief_ctx(request, product)}
            ctx["products"] = await self._db().products()
            oob.append("_brief_main.html")
        return self._fragment(request, None, ctx, toast=message, oob=oob)

    async def _schedule_changed(self, request: Request, message: str) -> Response:
        t, zone, paused = await workflows.schedule_settings(self._db())
        try:
            await workflows.sync_schedule(await self._temporal(), t, zone, paused)
        except Exception:
            message += " · Temporal isn't running, the schedule updates when it starts"
        ctx = {**await self._chrome(request), "tz_name": zone}
        return self._fragment(
            request, None, ctx, toast=message, oob=["_bar.html", "_timepick.html"]
        )
