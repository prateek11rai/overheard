import calendar
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request

from overheard import pipeline, workflows
from overheard.ai import (
    EMBED_ROLE,
    model_status,
)
from overheard.db import Product
from overheard.sources import SerpApiAccount
from overheard.web.context import WebCore
from overheard.web.security import parse_emails


class Views(WebCore):
    """The data each page renders from."""

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

    async def _review_ctx(
        self,
        request: Request,
        product: Product,
        post_id: int | None,
        session: list[int] | None = None,
    ) -> dict:
        brief = await pipeline.brief_for(self._db(), product, await self._tz())
        cards = {c.post.id: c for c in brief.cards}
        session = [i for i in (session or []) if isinstance(i, int)] or list(brief.order)
        remaining = [i for i in session if i in cards]
        current = post_id if post_id in remaining else (remaining[0] if remaining else None)
        handled = len(session) - len(remaining)
        bar = ["cur" if i == current else "" if i in cards else "done" for i in session]
        ctx = await self._chrome(request, product.slug)
        return {
            **ctx,
            "product": product,
            "brief": brief,
            "card": cards.get(current) if current is not None else None,
            "idx": remaining.index(current) if current in remaining else 0,
            "position": min(handled + 1, len(session)),
            "session_total": len(session),
            "bar": bar,
            "session": session,
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
                text = f"↑ {s.weekly[0]} this week · {s.posts_4w} in 4 weeks"
            elif trend == "new":
                text = f"new · {s.total} post{'s' if s.total != 1 else ''}"
            else:
                text = f"{s.posts_4w} in 4 weeks"
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
        has_key = self.settings.serpapi_api_key is not None
        words = f"{n} phrase{'s' if n != 1 else ''}"
        if not has_key or sources != "reddit+hn":
            return f"{words} · Hacker News only"
        per_day = pipeline.phrase_cost(phrases, True, has_key)
        return f"{words} · {per_day} search{'es' if per_day != 1 else ''} a day"

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
        pending = await self._db().get_setting("pending_embed")
        if pending and pending not in embed:
            embed.append(pending)
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
            "embed_models": [
                (m, "re-embedding…" if m == pending else model_status(m, embed_in_use, found))
                for m in embed
            ],
            "chat_in_use": chat_in_use,
            "embed_in_use": embed_in_use,
            "compare_rows": compare_rows,
            "compare_next": others[0] if others else None,
            "is_ollama": found.server == "ollama",
        }

    async def _reddit_ctx(self) -> dict[str, Any]:
        db = self._db()
        products = await db.products()
        real = [p for p in products if not p.demo]
        pool = real or [p for p in products if p.demo]
        listening = [p for p in pool if not p.paused and p.reddit_on]
        phrases = sum(len(p.phrases) for p in listening)
        today = pipeline.today_in(await self._tz())
        has_key = self.settings.serpapi_api_key is not None
        stored = await db.get_setting("serpapi_account")
        account = None
        if has_key and stored:
            account = SerpApiAccount(
                plan_searches=int(stored["plan_searches"]),
                searches_left=int(stored["searches_left"]),
                this_month_usage=int(stored.get("this_month_usage", 0)),
            )
        budget = pipeline.reddit_budget(
            account,
            today,
            used_today=await db.reddit_used(today),
            used_month=await db.reddit_used(today.replace(day=1)),
            daily_cap=self.settings.serpapi_daily_cap,
            monthly_cap=self.settings.serpapi_monthly_cap,
        )
        days = calendar.monthrange(today.year, today.month)[1]
        projected = phrases * days
        plan = budget.plan_searches if budget else None
        return {
            "phrases": phrases,
            "days": days,
            "projected": projected,
            "plan": plan,
            "fits": budget.fits(phrases) if budget else None,
            "meter": min(100, round(100 * projected / plan)) if plan else 0,
            "has_key": has_key,
            "demo_only": not real and bool(pool),
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
