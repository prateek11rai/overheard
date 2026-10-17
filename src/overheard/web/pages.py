from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from overheard.web.context import DONE_MESSAGES
from overheard.web.updates import Web


def register(app: FastAPI, web: Web) -> None:
    """Add the page routes.

    Args:
        app: The FastAPI app.
        web: Shared settings, templates and helpers.
    """

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        try:
            health = await web._db().health()
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True, "tables": health["tables"]})

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        products = await web._db().products()
        done = request.query_params.get("done")
        suffix = f"?done={done}" if done in DONE_MESSAGES else ""
        if not products:
            return RedirectResponse(f"/setup{suffix}", status_code=303)
        return RedirectResponse(f"/p/{products[0].slug}{suffix}", status_code=303)

    @app.get("/p/{slug}", response_class=HTMLResponse)
    async def brief_page(request: Request, slug: str) -> Response:
        product = await web._db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        return web._page(request, "brief.html", await web._brief_ctx(request, product))

    @app.get("/p/{slug}/review", response_class=HTMLResponse)
    async def review_page(request: Request, slug: str, post: int | None = None) -> Response:
        product = await web._db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        return web._page(request, "review.html", await web._review_ctx(request, product, post))

    @app.get("/p/{slug}/themes", response_class=HTMLResponse)
    async def themes_page(request: Request, slug: str) -> Response:
        product = await web._db().product_by_slug(slug)
        if product is None:
            return RedirectResponse("/", status_code=303)
        ctx = await web._chrome(request, slug)
        return web._page(
            request, "themes.html", {**ctx, "product": product, **await web._themes_ctx(product)}
        )

    @app.get("/p/{slug}/themes/{theme_id}", response_class=HTMLResponse)
    async def theme_page(request: Request, slug: str, theme_id: int) -> Response:
        product = await web._db().product_by_slug(slug)
        theme = await web._db().theme(theme_id)
        if product is None or theme is None or theme["product_id"] != product.id:
            return RedirectResponse(f"/p/{slug}/themes", status_code=303)
        posts = await web._db().theme_posts(theme_id)
        ctx = await web._chrome(request, slug)
        return web._page(
            request, "theme.html", {**ctx, "product": product, "theme": theme, "posts": posts}
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> Response:
        return web._page(request, "setup.html", await web._setup_ctx(request))

    @app.get("/products/new", response_class=HTMLResponse)
    async def new_product(request: Request) -> Response:
        recipient = await web._db().get_setting("default_recipient") or ""
        ctx = {**await web._chrome(request, ""), **web._product_form_ctx(None, recipient)}
        ctx["badge"] = web._phrase_badge([], "reddit+hn")
        ctx["first_cost"] = (
            "Hacker News only" if web.settings.serpapi_api_key is None else "0 searches"
        )
        return web._page(request, "product_form.html", ctx)

    @app.get("/products/{product_id}/edit", response_class=HTMLResponse)
    async def edit_product(request: Request, product_id: int) -> Response:
        product = await web._db().product(product_id)
        if product is None:
            return RedirectResponse("/", status_code=303)
        ctx = {**await web._chrome(request, product.slug), **web._product_form_ctx(product, "")}
        ctx["badge"] = web._phrase_badge(product.phrases, product.sources)
        ctx["first_cost"] = web._cost_text(product)
        return web._page(request, "product_form.html", ctx)

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_page(request: Request, run: int | None = None) -> Response:
        return web._page(
            request, "runs.html", {**await web._chrome(request, ""), **await web._runs_ctx(run)}
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        return web._page(
            request,
            "settings.html",
            {**await web._chrome(request, ""), **await web._settings_ctx()},
        )
