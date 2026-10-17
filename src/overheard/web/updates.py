import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from overheard import pipeline, workflows
from overheard.web.views import Views


class Web(Views):
    """What every route shares: settings, templates, the database pool and page helpers."""

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
            oob += ["_settings_products.html", "_reddit_plan.html"]
            ctx["reddit"] = await self._reddit_ctx()
        slug = self._active_slug(request)
        product = await self._db().product_by_slug(slug) if slug else None
        if product is not None and not self._current_path(request).endswith(("/review", "/themes")):
            ctx = {**ctx, **await self._brief_ctx(request, product)}
            ctx["products"] = await self._db().products()
            oob.append("_brief_main.html")
        return self._fragment(request, None, ctx, toast=message, oob=oob)

    async def _schedule_changed(self, request: Request, message: str) -> Response:
        t, zone, paused = await workflows.schedule_settings(
            self._db(), self.settings.default_timezone()
        )
        try:
            await workflows.sync_schedule(await self._temporal(), t, zone, paused)
        except Exception:
            message += " · Temporal isn't running, the schedule updates when it starts"
        ctx = {**await self._chrome(request), "tz_name": zone}
        return self._fragment(
            request, None, ctx, toast=message, oob=["_bar.html", "_timepick.html"]
        )
