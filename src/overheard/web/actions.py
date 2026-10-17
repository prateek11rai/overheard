import csv
import io
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from overheard import pipeline, workflows
from overheard.ai import (
    CHAT_ROLES,
    ModelUnavailableError,
    role_models,
)
from overheard.settings import is_timezone
from overheard.web.context import Web
from overheard.web.security import csv_safe, parse_emails, slugify


def register(app: FastAPI, web: Web) -> None:
    """Add the button and API routes.

    Args:
        app: The FastAPI app.
        web: Shared settings, templates and helpers.
    """

    @app.post("/products")
    async def create_product(request: Request) -> Response:
        fields = web._form_fields(await request.form())
        if not fields["name"]:
            return RedirectResponse("/products/new", status_code=303)
        fields["slug"] = await web._db().free_slug(slugify(fields["name"]))
        product = await web._db().create_product(**fields)
        try:
            await workflows.start_check(await web._temporal(), web._db(), product.id, product.slug)
            done = "added"
        except Exception:
            done = "saved"
        return RedirectResponse(f"/p/{product.slug}?done={done}", status_code=303)

    @app.post("/products/{product_id}")
    async def save_product(request: Request, product_id: int) -> Response:
        product = await web._db().product(product_id)
        if product is None:
            return RedirectResponse("/", status_code=303)
        fields = web._form_fields(await request.form())
        if not fields["name"]:
            fields["name"] = product.name
        await web._db().update_product(product_id, **fields)
        return RedirectResponse(f"/products/{product_id}/edit?done=saved", status_code=303)

    @app.post("/api/posts/{post_id}/done", response_class=HTMLResponse)
    async def post_done(request: Request, post_id: int) -> Response:
        return await web._post_action(request, post_id, "done")

    @app.post("/api/posts/{post_id}/skip", response_class=HTMLResponse)
    async def post_skip(request: Request, post_id: int) -> Response:
        return await web._post_action(request, post_id, "skip")

    @app.post("/api/posts/{post_id}/undo", response_class=HTMLResponse)
    async def post_undo(request: Request, post_id: int) -> Response:
        return await web._post_action(request, post_id, "undo")

    @app.post("/api/posts/{post_id}/draft", response_class=HTMLResponse)
    async def post_draft(request: Request, post_id: int, style: str = "first") -> Response:
        post = await web._db().post(post_id)
        product = await web._db().product(post.product_id) if post else None
        if post is None or product is None:
            return web._toast_only("That post is gone")
        style = style if style in ("first", "shorter", "rewrite") else "first"
        form = await request.form()
        previous = form.get("reply") if isinstance(form.get("reply"), str) else post.reply_draft
        if style == "first" and post.reply_draft:
            draft = post.reply_draft
        else:
            try:
                async with await web._ai() as client:
                    draft = await pipeline.draft_reply(
                        client, product, post.title, post.body, style, previous
                    )
            except (ModelUnavailableError, Exception):
                return web._fragment(
                    request,
                    "_reply_box.html",
                    {"post": post, "draft": previous or "", "error": True},
                    toast="The model didn't answer. Is Ollama running?",
                )
            await web._db().set_reply_draft(post_id, draft)
        return web._fragment(request, "_reply_box.html", {"post": post, "draft": draft})

    @app.post("/api/products/{product_id}/pause", response_class=HTMLResponse)
    async def product_pause(request: Request, product_id: int) -> Response:
        product = await web._db().update_product(product_id, status="paused")
        if product is None:
            return web._toast_only("That product is gone")
        return await web._product_changed(request, f"{product.name} paused")

    @app.post("/api/products/{product_id}/resume", response_class=HTMLResponse)
    async def product_resume(request: Request, product_id: int) -> Response:
        product = await web._db().update_product(product_id, status="active")
        if product is None:
            return web._toast_only("That product is gone")
        return await web._product_changed(request, f"{product.name} resumed")

    @app.delete("/api/products/{product_id}")
    async def product_delete(product_id: int) -> Response:
        await web._db().delete_product(product_id)
        response = Response("")
        response.headers["HX-Redirect"] = "/?done=deleted"
        return response

    @app.post("/api/products/{product_id}/check", response_class=HTMLResponse)
    async def product_check(request: Request, product_id: int) -> Response:
        product = await web._db().product(product_id)
        if product is None:
            return web._toast_only("That product is gone")
        try:
            await workflows.start_check(await web._temporal(), web._db(), product.id, product.slug)
        except workflows.AlreadyRunningError:
            return web._toast_only("Already running")
        except Exception:
            return web._toast_only("Temporal isn't running. Start it with uv run overheard start")
        ctx = {**await web._chrome(request), **await web._runs_ctx(None)}
        oob = ["_bar.html", "_runs_main.html"]
        return web._fragment(request, None, ctx, toast=f"Checking {product.name} now", oob=oob)

    @app.post("/api/schedule/time", response_class=HTMLResponse)
    async def schedule_time(request: Request, time: str = Form(...)) -> Response:
        try:
            h, m = workflows.parse_time(time)
        except ValueError:
            return web._toast_only("That isn't a time")
        value = f"{h:02d}:{m:02d}"
        await web._db().set_setting("morning_time", value)
        return await web._schedule_changed(request, f"Every morning at {value}")

    @app.post("/api/schedule/timezone", response_class=HTMLResponse)
    async def schedule_timezone(request: Request, tz: str = Form(...)) -> Response:
        tz = tz.strip()
        if not is_timezone(tz):
            return web._toast_only("That isn't a timezone")
        await web._db().set_setting("timezone", tz)
        morning = await web._db().get_setting("morning_time") or workflows.DEFAULT_TIME
        return await web._schedule_changed(request, f"Every morning at {morning} {tz}")

    @app.post("/api/schedule/step", response_class=HTMLResponse)
    async def schedule_step(request: Request, minutes: int = Form(...)) -> Response:
        h, m = workflows.parse_time(
            await web._db().get_setting("morning_time") or workflows.DEFAULT_TIME
        )
        total = (h * 60 + m + max(-60, min(60, minutes))) % (24 * 60)
        value = f"{total // 60:02d}:{total % 60:02d}"
        await web._db().set_setting("morning_time", value)
        return await web._schedule_changed(request, f"Every morning at {value}")

    @app.post("/api/schedule/pause-all", response_class=HTMLResponse)
    async def schedule_pause_all(request: Request) -> Response:
        paused = not bool(await web._db().get_setting("paused_all", False))
        await web._db().set_setting("paused_all", paused)
        return await web._schedule_changed(
            request, "All listening paused" if paused else "Listening again"
        )

    @app.post("/api/runs/now", response_class=HTMLResponse)
    async def run_now(request: Request) -> Response:
        try:
            await workflows.start_run_now(await web._temporal(), web._db())
        except workflows.AlreadyRunningError:
            return web._toast_only("Already running")
        except Exception:
            return web._toast_only("Temporal isn't running. Start it with uv run overheard start")
        ctx = {**await web._chrome(request), **await web._runs_ctx(None)}
        oob = ["_bar.html", "_runs_main.html"]
        return web._fragment(request, None, ctx, toast="Morning brief started", oob=oob)

    @app.get("/api/runs/{run_id}/row", response_class=HTMLResponse)
    async def run_row(request: Request, run_id: int, sel: int | None = None) -> Response:
        r = await web._db().run(run_id)
        if r is None:
            return HTMLResponse("")
        view = await web._run_view(r, ZoneInfo(await web._tz()))
        ctx: dict[str, Any] = {"v": view, "selected_id": sel}
        html = web.templates.get_template("_run_row.html").render({**ctx, "request": request})
        if sel == run_id:
            groups = await web._step_groups(view)
            html += web.templates.get_template("_run_steps.html").render(
                {"selected": view, "groups": groups, "request": request, "oob": True}
            )
        if r.status != "running":
            ctx = await web._chrome(request)
            for name in ("_bar.html", "_runs_runnow.html"):
                html += web.templates.get_template(name).render(
                    {**ctx, "request": request, "oob": True}
                )
        return HTMLResponse(html)

    @app.post("/api/models/use", response_class=HTMLResponse)
    async def models_use(request: Request) -> Response:
        form = await request.form()
        stored = dict(await web._db().get_setting("models") or {})
        chat, embed = form.get("chat"), form.get("embed")
        message = None
        if isinstance(chat, str) and chat:
            for role in (*CHAT_ROLES, "chat"):
                stored[role] = chat
            await web._db().set_setting("models", stored)
            message = f"Now using {chat}"
        current = role_models(stored, web.settings.chat_model, web.settings.embed_model)["embed"]
        if isinstance(embed, str) and embed and embed != current:
            try:
                await workflows.start_reembed(await web._temporal(), embed)
            except Exception:
                ctx = {**await web._chrome(request), **await web._models_ctx()}
                return web._fragment(
                    request,
                    "_models.html",
                    ctx,
                    toast="Temporal isn't running, so embeddings can't switch yet. "
                    "Start it with uv run overheard start",
                )
            await web._db().set_setting("pending_embed", embed)
            message = (
                f"Re-embedding with {embed} · it switches when every post is re-embedded "
                "(see Run log)"
            )
        ctx = {**await web._chrome(request), **await web._models_ctx()}
        target = form.get("target")
        if target == "setup":
            return web._fragment(request, None, ctx, toast=message or "Saved")
        return web._fragment(request, "_models.html", ctx, toast=message or "Saved")

    @app.get("/api/models")
    async def models_list(request: Request) -> Response:
        if request.headers.get("hx-request"):
            ctx = await web._models_ctx()
            html = web.templates.get_template("_model_cards.html").render(
                {**ctx, "request": request}
            )
            return HTMLResponse(html)
        ctx = await web._models_ctx()
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
        ctx = await web._models_ctx()
        model = ctx["compare_next"]
        if not model:
            return web._toast_only("Install a second chat model to compare")
        try:
            await workflows.start_compare(await web._temporal(), model)
        except Exception:
            return web._toast_only("Temporal isn't running. Start it with uv run overheard start")
        return web._toast_only(f"Comparing {model} on your last 50 posts · see Run log")

    @app.post("/api/email/test", response_class=HTMLResponse)
    async def email_test(request: Request) -> Response:
        from overheard import mail

        form = await request.form()
        raw = form.get("email")
        if isinstance(raw, str) and raw.strip():
            emails = parse_emails(raw)
            if not emails:
                return web._toast_only("That doesn't look like an email address")
            await web._db().set_setting("default_recipient", emails[0])
        recipient = await web._db().get_setting("default_recipient") or web.settings.smtp_user
        try:
            message = await mail.send_test(web.settings, recipient)
        except Exception:
            message = "The test email failed. Check SMTP_* in .env"
        ctx = await web._chrome(request)
        if form.get("target") == "setup":
            ctx = {**ctx, **await web._setup_ctx(request)}
            return web._fragment(request, "_setup_main.html", ctx, toast=message)
        return web._toast_only(message)

    @app.post("/api/email/skip", response_class=HTMLResponse)
    async def email_skip(request: Request) -> Response:
        await web._db().set_setting("email_skipped", True)
        ctx = {**await web._chrome(request), **await web._setup_ctx(request)}
        return web._fragment(
            request, "_setup_main.html", ctx, toast="Skipped · digests stay in the app"
        )

    @app.post("/api/chips/{field}", response_class=HTMLResponse)
    async def chips(request: Request, field: str, remove: int | None = None) -> Response:
        if field not in ("phrases", "competitors", "subreddits"):
            return web._toast_only("Unknown field")
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
        ctx = {"field": field, "items": items, "badge": web._phrase_badge(items, str(sources))}
        oob = ["_phrase_badge.html"] if field == "phrases" else []
        return web._fragment(request, "_chips.html", ctx, oob=oob)

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
                async with await web._ai() as client:
                    items = await pipeline.suggest_phrases(client, name, description)
                toast = "Suggested phrases · edit them freely"
            except Exception:
                toast = "Couldn't suggest phrases. Is Ollama running?"
        ctx = {"field": "phrases", "items": items, "badge": web._phrase_badge(items, str(sources))}
        return web._fragment(request, "_chips.html", ctx, toast=toast, oob=["_phrase_badge.html"])

    @app.get("/api/export.csv")
    async def export_csv() -> Response:
        rows = await web._db().export_rows()
        buf = io.StringIO()
        cols = ["product", "source", "url", "title", "community", "posted_at", "found_at"]
        cols += ["label", "quote", "competitor", "status", "theme"]
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: csv_safe(v) for k, v in r.items()})
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="overheard.csv"'},
        )

    @app.post("/api/data/delete-all")
    async def delete_all() -> Response:
        await web._db().delete_everything()
        response = Response("")
        response.headers["HX-Redirect"] = "/setup?done=data-deleted"
        return response

    @app.post("/api/demo/clear")
    async def demo_clear() -> Response:
        from overheard import demo

        await demo.clear(web._db(), web.settings)
        response = Response("")
        response.headers["HX-Redirect"] = "/?done=demo-cleared"
        return response
