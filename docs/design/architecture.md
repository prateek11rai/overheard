# Overheard: architecture

How it is built. `spec.md` says what it does; `mockups/` (v4) says how it looks; this file says how the code is laid out, runs, ships and grows.

## 1. One picture

```
                you ── http://127.0.0.1:8000 ──┐
                                               ▼
  ┌────────────────────── one Python project (uv) ───────────────────────┐
  │  web     FastAPI: renders the pages (Jinja2 templates made from the   │
  │          v4 mockups) and answers the buttons (done, skip, run now…)   │
  │  worker  Temporal worker: the morning job                             │
  └──────┬──────────────────────────┬──────────────────────────┬─────────┘
         │                          │                          │
   Postgres + pgvector        Temporal server            outside world
   products, posts, labels,   schedule + run history     SerpApi (Reddit via Google)
   embeddings, themes, runs   (dev server locally)       Hacker News API
                                                         an AI model server (Ollama locally)
                                                         SMTP (your own mailbox)
```

Everything is Python. No Node, no npm, no build step: pages are HTML rendered by Python and styled with the mockups' CSS. Buttons use **HTMX** (one vendored JavaScript file) so every action updates the page with HTML the server rendered from the database; one small `app.js` handles toasts, popovers and review keys.

## 2. One command

```bash
uv run overheard start
```

starts everything locally and stops everything on Ctrl-C:
1. checks the model server answers (starts `ollama serve` if it is the default local Ollama and not running),
2. starts Homebrew's Temporal dev server (`/opt/homebrew/bin/temporal`, state in `.data/temporal.db`) unless one already answers on `TEMPORAL_ADDRESS`,
3. runs `overheard worker` and `overheard web` as child processes with `[temporal] [worker] [web]` log prefixes,
4. creates or updates the morning schedule,
5. prints `Open http://127.0.0.1:8000` and the Temporal UI address.

All commands come from one `overheard` entry point declared in `pyproject.toml` (`[project.scripts] overheard = "overheard.cli:main"`), built with the standard library's `argparse`:

| Command | Does |
|---|---|
| `uv run overheard setup` | creates `.data/`, runs database migrations, checks tools |
| `uv run overheard doctor` | ✓/✗ for every piece, never a secret |
| `uv run overheard start` | everything (above) |
| `uv run overheard web` / `worker` | one piece; these are also what a deployment runs |
| `uv run overheard demo load` / `demo clear` | adds / removes demo data (§11), for trying the app without any accounts |
| `uv run overheard seed` | adds the two sample products, Mealwise and Shipnote, with no posts (for live runs) |
| `uv run overheard brief [--reddit off]` | runs the morning job now and waits |
| `uv run overheard eval --model A --model B` | compares models on the checked posts (§7) |
| `uv run overheard email-test` | sends a test digest to your own mailbox (`SMTP_USER`) |
| `uv run pytest` · `uv run ruff check` | tests · lint |

**Why no Makefile, Poetry or `main.py`:** uv installs and runs Python but has no task runner; Poetry is another package manager we won't mix in. A Makefile was only needed to glue Python and Node; with Python alone, the `overheard` command does it and `uv run overheard --help` lists everything. A `main.py` would be a second way to do the same thing.

## 3. Files

```
overheard/
├── README.md             value first, banner, screenshots, quick start, limits, credits (§12)
├── pyproject.toml        dependencies, the `overheard` command, uv settings
├── uv.lock               exact versions (committed)
├── .python-version       3.13 (committed; uv and CI read it)
├── .env.example          the 6 settings people actually fill (§9)
├── Dockerfile            one image for web and worker (§10)
├── compose.yaml          the app plus Postgres, Temporal and Ollama, for anyone deploying (§10)
├── .dockerignore · .gitignore
├── AGENTS.md             rules for any coding agent in this repo (§15)
├── .agents/skills/       repo skills for agents; DevRelay's devrelay-* ones are git-ignored (§15)
├── .claude/skills        symlink → ../.agents/skills, so Claude Code finds the same skills (§15)
├── LICENSE · NOTICE      MIT · credits (Catppuccin, fonts)
├── src/overheard/
│   ├── cli.py            the `overheard` command
│   ├── settings.py       reads environment variables (and .env when present)
│   ├── db.py             Postgres connection and every query
│   ├── migrations/       001_init.sql, 002_….sql
│   ├── ai.py             finds models; chat and embedding calls; model roles
│   ├── sources.py        Reddit via SerpApi, Hacker News (one function per source)
│   ├── pipeline.py       search plan, search ledger, labels + quote check, themes, brief data
│   ├── evals.py          model comparison
│   ├── workflows.py      Temporal: morning job, per-product scan, re-embed, schedule
│   ├── mail.py           the morning email
│   ├── web.py            FastAPI: pages + button actions
│   ├── templates/        base.html, one file per screen, _macros.html, email/
│   └── static/           app.css, app.js, logo.svg, fonts/
├── tests/                pytest, with fixtures/
├── data/eval/            checked posts for comparing models
└── docs/
    ├── design/           spec.md, architecture.md, mockups/ (v4)
    ├── img/              banner.png, devto-cover.png, screenshots
    ├── RUNBOOK.md        running and deploying, troubleshooting
    └── devto-post.md     the post draft
```

Plus `.github/workflows/test.yml` (lint + tests) and `.github/workflows/docker.yml` (checks the image still builds). One file per job inside `src/overheard/`; if one passes about 500 lines, it becomes a folder of the same name.

## 4. How the pages work (FastAPI + Jinja2)

FastAPI is a Python web framework: a function decorated with a route answers an HTTP request. Usually it returns JSON; with Jinja2 it returns an HTML page instead. `uvicorn` is the web server that runs the app. The browser asks `GET /p/mealwise`, Python fills a template with data from Postgres and sends back HTML, exactly like the mockup but with real posts. Buttons use HTMX: they post to a small route and swap in the HTML it returns.

**The app and a page:**

```python
# src/overheard/web.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(docs_url=None, redoc_url=None)               # no public API docs page
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)       # autoescape is on: post text can't inject HTML

@app.get("/p/{slug}", response_class=HTMLResponse)
async def brief_page(request: Request, slug: str):
    product = await db.product_by_slug(slug)
    brief = await pipeline.brief_for(product)              # headline, worth_a_reply, pain_points, mentions
    return templates.TemplateResponse(request, "brief.html", {"product": product, "brief": brief})
```

**The template** (same markup and classes as `mockups/02-brief.html`):

```jinja
{# src/overheard/templates/brief.html #}
{% extends "base.html" %}
{% from "_macros.html" import post_card %}
{% block main %}
  <h1>{{ brief.headline }}</h1>
  <p class="sub">{{ brief.subline }}</p>
  <a class="btn primary big" href="/p/{{ product.slug }}/review">Go through all {{ brief.total }}</a>
  <section class="section">
    <h2>Worth a reply <span class="count">{{ brief.worth_a_reply|length }}</span></h2>
    <div class="stack">{% for post in brief.worth_a_reply %}{{ post_card(post) }}{% endfor %}</div>
  </section>
{% endblock %}
```

```jinja
{# src/overheard/templates/_macros.html #}
{% macro post_card(post) %}
<article class="block {{ post.label_css }} post" data-post="{{ post.id }}">
  <div class="row between"><span class="lbl {{ post.label_css }}">{{ post.label_text }}</span>
    <span class="src">{{ post.community }} · {{ post.age }}</span></div>
  <a class="title t" href="{{ post.url }}" target="_blank" rel="noopener">{{ post.title }}<span class="ext">↗</span></a>
  <div class="quote">{{ post.text_before }}<mark>{{ post.quote }}</mark>{{ post.text_after }}</div>
  <div class="checked">✓ quote checked</div>
  <div class="acts"><a class="btn sm" href="/p/{{ post.product_slug }}/review?post={{ post.id }}">Draft a reply</a>
    <button class="btn sm" data-action="done">Done</button></div>
</article>
{% endmacro %}
```

**A button** (HTMX: the server answers with the updated piece of the page):

```jinja
{# in the post_card macro #}
<button class="btn sm" hx-post="/api/posts/{{ post.id }}/done" hx-target="closest [data-post]" hx-swap="outerHTML">Done</button>
```

```python
@app.post("/api/posts/{post_id}/done", response_class=HTMLResponse)
async def mark_done(request: Request, post_id: int):
    """Marks a post done and returns what replaces its card."""
    await db.set_post_status(post_id, "done")
    response = templates.TemplateResponse(request, "_removed.html", {"undo": f"/api/posts/{post_id}/undo"})
    response.headers["HX-Trigger"] = '{"toast": {"text": "Marked done", "undo": "/api/posts/%d/undo"}}' % post_id
    return response
```

The page never guesses: what it shows after a click is what the server says is now true. Pausing a product, for example, returns the re-rendered tab bar (the tab dims and shows "paused"), the Settings row and the ▾ menu ("Resume" instead of "Pause") through `hx-swap-oob`, and the change is still there after a reload. `app.js` listens for the `toast` event to show the toast with its Undo link.

**A form** (add product: plain HTML form, then a redirect):

```python
@app.post("/products")
async def create_product(name: str = Form(), description: str = Form(), phrases: list[str] = Form([])):
    product = await db.create_product(name, description, phrases)
    await workflows.check_now(product.id)                      # first scan
    return RedirectResponse(f"/p/{product.slug}", status_code=303)
```

**Behaviour:**

| Thing | Built with |
|---|---|
| Done, Skip, Undo, Pause, Resume, Delete, Use (model), Run now, Check now, schedule time, pause all | HTMX `hx-post` → the server saves, then returns the changed fragments (main target + `hx-swap-oob` for tabs, counts, chips) and an `HX-Trigger` toast |
| Option groups, model cards, time grid | real `<input type="radio">` styled like the mockups, `hx-post` on change |
| ◷ schedule popover, ▾ product menu | `<details>`/`<summary>`; `app.js` closes them on outside click |
| More options, compare models | `<details>` |
| Delete confirmation | `<dialog>`, then `hx-delete` |
| Run log while a run is going | `hx-trigger="every 3s"` on the running row until it finishes |
| Model status in Settings | `hx-trigger="every 10s"` on the model list |
| Review keys S D C O ? | `app.js`, review page only |

- **Routes:** `/setup`, `/` (first product's brief, or setup), `/p/<slug>`, `/p/<slug>/review`, `/p/<slug>/themes`, `/products/new`, `/products/<id>/edit`, `/runs`, `/settings`, `/healthz`; button routes under `/api/` return HTML fragments for HTMX (`GET /api/models` also answers JSON for `overheard doctor`).
- **CSS:** `static/app.css` = the mockups' `styles.css` minus `.mock` and `.agent-note`. **Fonts:** IBM Plex Sans and JetBrains Mono `woff2` with their OFL licences in `static/fonts/`; nothing loads from Google at runtime.
- **Safety:** Jinja2 autoescape for all post text; POST routes accept only same-origin requests (check the `Origin` header); the app refuses to listen on anything but 127.0.0.1 unless `OVERHEARD_PASSWORD` is set (§10).

## 5. The morning job (Temporal, Python SDK)

```python
@workflow.defn
class MorningBrief:                                   # started by the schedule or "Run now"
    @workflow.run
    async def run(self) -> None:
        plan = await workflow.execute_activity(plan_searches, start_to_close_timeout=timedelta(seconds=30))
        for product in plan.products:                 # one child workflow per active product
            await workflow.execute_child_workflow(ProductScan.run, product,
                                                  id=f"{workflow.info().workflow_id}/{product.slug}")

@workflow.defn
class ProductScan:
    @workflow.run
    async def run(self, p: ProductPlan) -> None:
        for phrase in p.reddit_phrases:               # each search is its own activity, retried on failure
            await workflow.execute_activity(search_reddit, SearchArgs(p.id, phrase, p.run_key),
                retry_policy=RetryPolicy(maximum_attempts=4, non_retryable_error_types=["PlanExhausted"]),
                start_to_close_timeout=timedelta(seconds=40))
        await workflow.execute_activity(search_hn, p, start_to_close_timeout=timedelta(seconds=60))
        await workflow.execute_activity(label_new_posts, p, heartbeat_timeout=timedelta(seconds=60),
                                        start_to_close_timeout=timedelta(minutes=20))
        await workflow.execute_activity(update_themes, p, start_to_close_timeout=timedelta(minutes=10))
        await workflow.execute_activity(send_digest, p, start_to_close_timeout=timedelta(seconds=60))
```

- **One Reddit search per phrase per morning.** `plan_searches` reads the SerpApi plan (`account.json`, free) and decides "every phrase" or "take turns" (10 % reserve).
- **Never pay twice:** `search_reddit` checks `search_ledger` for `(run, phrase)` first and saves the result before returning.
- **Schedule** `morning-brief`: chosen time and timezone, skip if one is still running, catch up to 12 hours late.
- **Run now** starts `MorningBrief`; **Check now** starts `ProductScan` for one product; changing the embedding model starts `Reembed`.
- Tests use Temporal's time-skipping environment: a flaky search retries; a used-up plan falls back to Hacker News only; a worker restart spends no extra search.

## 6. AI models, embeddings and roles

**Detection** (checked against Ollama 0.35.1 on this laptop):
- `GET /api/tags` → installed models.
- `POST /api/show` → `capabilities`: `completion` = chat model (gemma4:e4b), `embedding` = embedding model (embeddinggemma:300m), and an embedding model's size from `model_info["<arch>.embedding_length"]` (768).
- `GET /api/ps` → models loaded in memory right now.
- Any other OpenAI-compatible server (LM Studio, llama.cpp, vLLM, a hosted endpoint): `GET /v1/models`, and each model is tried once with a tiny embedding call to tell embedding models apart.

**Status in Settings:** each model is an option card marked **in use**, **loaded**, **installed** or **unreachable**, refreshed every 10 s.

**Roles.** `ai.py` asks for a model by role, never by name: `ai.chat(role="label" | "theme_name" | "reply" | "phrases")` and `ai.embed()`. Today every chat role uses the one chat model picked in Settings; the `settings` table already stores a model per role, so a later version can, say, label with a small tuned model and draft replies with a bigger one without touching the pipeline.

**Adding models:** `ollama pull qwen3:4b` (chat) or `ollama pull nomic-embed-text` (embeddings) → it appears in Settings within 10 s → pick it. A new embedding model starts the Re-embed job (progress in Run log). Embeddings live per model in `post_embeddings(post_id, model, dim, embedding)` with one HNSW index per model, so switching never needs a schema change.

## 7. Comparing models

**The checked set** (`data/eval/`): each item is a product context (one of the sample products' descriptions) plus a post, and the label a person agreed on. Hacker News items are stored **by id** and fetched live; Reddit-style items are written for the set and marked `synthetic: true`. Labels start in `labels.proposed.jsonl` and move to `labels.jsonl` only after a person checks them.

**From the terminal:**

```bash
uv run overheard eval --model gemma4:e4b --model qwen3:4b
```

prints, and saves to `data/eval/results/<date>-<model>.json`:

| | gemma4:e4b | qwen3:4b |
|---|---|---|
| right label | n/N | n/N |
| quote checks out | % | % |
| asking-for-a-tool recall | % | % |
| seconds per post | s | s |

plus the list of posts where the two disagree. `--markdown` writes the same table for the README and the post. Numbers only ever come from runs.

**From Settings → Compare models:**
- The same table from the latest results, with a **Use** button per row (one click switches the chat model).
- **Compare on my last 50 posts:** runs the second model on real posts in the background without changing any label and lists where the two disagree, so you can judge on your own data before switching.

## 8. Future scope (room is left for these)

- **A tuned labeller (Tinker).** Fine-tune a small open model (Tinker supports Qwen3.5-4B, not Gemma) on checked labels, merge the LoRA, convert to GGUF and `ollama create overheard-labeler -f Modelfile`. It then appears in Settings like any model, `overheard eval` shows its gain over the base model, and the `label` role can point at it alone.
- **Tuned embeddings.** Fine-tune an embedding model on (post, theme) pairs the user confirmed (sentence-transformers, contrastive loss) so themes group the way the user thinks. Serve it through Ollama or any OpenAI-compatible embeddings endpoint; picking it in Settings runs Re-embed.
- **More sources.** `sources.py` functions share one shape, `search(phrase, since) -> list[Post]`; adding Lobsters, GitHub Discussions or Stack Overflow is one function plus a toggle.
- **More channels.** `mail.py` renders the digest once; Slack or Discord webhooks would reuse it.
- **Teams.** A login and per-user products; until then the app is single-user and private (§10).

## 9. Settings

`settings.py` (pydantic-settings) reads **environment variables first** and a `.env` file only if present, so the same code runs on a laptop, in Docker or on a server. Everything has a sensible default except the database.

**`.env.example`** (all a new user needs):

```dotenv
# Required: Postgres with pgvector (Tiger Cloud's free tier works; so does a local Postgres)
DATABASE_URL=postgresql://user:password@host:5432/db?sslmode=require

# Optional: Reddit through Google search. Free plan: 250 searches a month. Without it, Hacker News only.
SERPAPI_API_KEY=

# Optional: send the morning email from your own mailbox (for Gmail use an app password)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
```

**Everything else has a default** and is documented in `docs/RUNBOOK.md`, not in `.env.example`:

| Variable | Default | Set it when |
|---|---|---|
| `LLM_BASE_URL` | `http://127.0.0.1:11434` | the model server is elsewhere or isn't Ollama |
| `LLM_API_KEY` | empty | that server needs a key |
| `CHAT_MODEL` · `EMBED_MODEL` | `gemma4:e4b` · `embeddinggemma:300m` | you want different first-run defaults (Settings wins after) |
| `EMAIL_FROM` | `SMTP_USER` | sending as another address |
| `EMAIL_MODE` | `smtp` if `SMTP_HOST` is set, else `off` | `file` writes digests to `.data/digests/` (used while building) |
| `TEMPORAL_ADDRESS` · `TEMPORAL_NAMESPACE` | `127.0.0.1:7233` · `default` | a real Temporal or Temporal Cloud |
| `TEMPORAL_API_KEY` | empty | Temporal Cloud |
| `TEMPORAL_UI_URL` | `http://127.0.0.1:8233` | for the Run log link |
| `OVERHEARD_HOST` · `OVERHEARD_PORT` | `127.0.0.1` · `8000` | deploying (needs `OVERHEARD_PASSWORD`) |
| `OVERHEARD_PASSWORD` | empty | any non-local host: the app asks for it (HTTP Basic, one user) |
| `OVERHEARD_DB_SCHEMA` | `overheard` | sharing a database |
| `OVERHEARD_DATA_DIR` | `.data` | moving local files |
| `SERPAPI_DAILY_CAP` · `SERPAPI_MONTHLY_CAP` | none | hard limits on searches |

The morning time and timezone, products, recipients and model choices are set in the app and stored in the database, not in the environment.

**For the demo** the planner fills `.env` by copying the matching values from Pagdandi's `.env` and the Tiger credentials file without printing them, adds `EMAIL_MODE=file` and safety caps for the build, and runs `uv run overheard doctor`.

## 10. Deploying (ready, not done for the hackathon)

The app is built so deployment is configuration, not code changes:
- **Two stateless processes** from one image: `overheard web` and `overheard worker`. All state is in Postgres and Temporal.
- **Configuration only through environment variables** (§9); logs go to stdout; `GET /healthz` for health checks; migrations run with `overheard setup`.
- **Private by default:** the web app binds to 127.0.0.1 and refuses any other host unless `OVERHEARD_PASSWORD` is set.

**`Dockerfile`** builds our app into one image:

```dockerfile
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:<tag> /uv /usr/local/bin/uv     # uv from its official image
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project               # exact versions from uv.lock
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --create-home app
USER app                                                         # never runs as root
ENV PATH="/app/.venv/bin:$PATH" OVERHEARD_HOST=0.0.0.0
CMD ["overheard", "web"]                                         # the worker runs `overheard worker`
```

**`compose.yaml` is not a test file.** It is Docker Compose's file (Docker looks for that name), and it exists because a Dockerfile only builds *our* image, while the app also needs Postgres + pgvector, a Temporal server and a model server. Each of those is its own container with its own ports, volumes and settings; `compose.yaml` lists them together so `docker compose up` starts the whole stack instead of five hand-typed `docker run` commands:

```yaml
services:
  db:       { image: pgvector/pgvector:<pg17 tag>, volumes: [db:/var/lib/postgresql/data] }
  temporal: { image: temporalio/temporal:<tag>, command: server start-dev --ip 0.0.0.0 --db-filename /data/temporal.db, volumes: [temporal:/data] }
  ollama:   { image: ollama/ollama:<tag>, volumes: [ollama:/root/.ollama] }
  worker:   { build: ., command: overheard worker, env_file: .env, depends_on: [db, temporal, ollama] }
  web:      { build: ., command: overheard web, env_file: .env, ports: ["127.0.0.1:8000:8000"] }
volumes: { db: {}, temporal: {}, ollama: {} }
```

The image sets `OVERHEARD_HOST=0.0.0.0` (inside a container that is required), so the app will only start with `OVERHEARD_PASSWORD` set in `.env`; compose still publishes the port on the host's 127.0.0.1 only. Image tags are pinned to releases at least 7 days old. In compose, `.env` points `DATABASE_URL`, `TEMPORAL_ADDRESS` and `LLM_BASE_URL` at the service names (`db`, `temporal:7233`, `http://ollama:11434`). For a real server, swap `temporal` for Temporal Cloud and `db` for managed Postgres (Tiger), keep the port bound to localhost and put a private tunnel or an authenticating proxy in front.

**Not run during the hackathon.** Neither file is built or started on this laptop. The `docker.yml` workflow builds the image on GitHub whenever `Dockerfile`, `pyproject.toml`, `uv.lock` or `src/` change, so the Dockerfile can't silently break; nothing is pushed to a registry.

## 11. Data

| What | Demo | Fully local | Compose |
|---|---|---|---|
| Products, posts, labels, embeddings, themes, runs, settings | Tiger Cloud (existing free service), schema `overheard`; Pagdandi's data untouched | Homebrew Postgres 17 + pgvector, database `overheard` | `db` service |
| Schedule and run history | Temporal dev server, `.data/temporal.db` | same | `temporal` service |
| Tests | Homebrew Postgres, database `overheard_test` | same | — |

Trends use TimescaleDB's `time_bucket` when present (Tiger) and `date_trunc('week', …)` otherwise.

**Demo data.** `uv run overheard demo load` adds the mockups' sample products (Mealwise, Shipnote, Plantpal paused) with posts, verified quotes, themes with eight weeks of counts, runs with steps, and a digest, all made up and flagged `demo = true` on the product (everything else hangs off the product and is deleted with it). The morning job never scans demo products. While any exist, the top bar shows a small `demo data` badge linking to Settings → Your data → **Remove demo data** (`overheard demo clear`). Real products and demo products can live side by side; demo slugs get a `demo-` prefix so they never collide with real ones.

## 12. README, banner and images

**README order:**
1. Banner (`docs/img/banner.png`, from `mockups/10-readme-banner.html`).
2. One line: "Every morning, what people said about the problem your product solves."
3. Screenshot of the brief (`docs/img/brief.png`).
4. **Why:** people pay for this today (spec §2 numbers with sources); GummySearch closes for good on 1 Dec 2026; this is the open, self-hosted version.
5. **How it works:** the diagram from §1 in plain words, the morning job, the quote check.
6. **Quick start:** `uv run overheard setup` → `doctor` → `start`, with prerequisites (Homebrew: uv, Python 3.13, temporal, ollama) and the 6-line `.env`.
7. **Screens:** a 2×2 grid of screenshots (brief, review, themes, settings) and the email.
8. **Models:** swapping, adding, the comparison table from a real `overheard eval --markdown` run.
9. **Deploy:** compose in four lines, link to RUNBOOK.
10. **Honest limits** (spec §11).
11. **Built for Hacktoberfest 2026:** link to the DEV post and the challenge; categories entered.
12. **After the deadline:** any commit after 2026-10-19 06:59 UTC listed here.
13. **Credits and licence:** Catppuccin (MIT), IBM Plex Sans and JetBrains Mono (OFL), Temporal, Tiger Data, SerpApi, Gemma; MIT licence.

**Images** (`docs/img/`, all PNG, made by screenshotting the running app or the mockup templates with real data):
- `banner.png` 1280×400 and `devto-cover.png` 1000×420 (key content inside the middle 800 px, because DEV crops link previews about 10 % per side). Both use one real, verified post from the demo run.
- `brief.png`, `review.png`, `themes.png`, `settings.png`, `runs.png`, `email.png`, all in the dark theme, no secrets or personal addresses visible.
- The demo video is recorded by the owner and committed under `docs/demo/`; the README links it (DEV can't play MP4s from GitHub).

## 13. Shipping to DEV

- **Challenge:** https://dev.to/challenges/hacktoberfest-week2-2026-10-12 · Week 2 "Side Hustle" · closes **Sun 18 Oct 2026, 11:59 PM PDT = Mon 19 Oct 12:29 IST**.
- **Template** (from the challenge's submission link): front matter `title`, `published`, `tags: devchallenge, hf26challenge` (we add two more, up to four, e.g. `opensource, python`), then:
  1. *This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 2: Side Hustle](https://dev.to/challenges/hacktoberfest-week2-2026-10-12)*
  2. `## What I Built` (what it does, who would pay, the problem it solves)
  3. `## Demo` (deployed link or video; ours is the video)
  4. `## Code` (`{% github prateek11rai/overheard %}`)
  5. `## How I Built It` (which open-source AI and how the project is built around it)
  6. `## Why Does Open Innovation Matter?` (what open made possible that a closed API wouldn't)
  7. `## My Agent Session` (optional; `{% agent_session <id> %}`)
  8. `## My First User` (optional, "where bonus points live": who, what they said, did they pay)
  9. `## Prize Categories` (every one that genuinely applies: Gemma, Temporal, SerpApi, Tiger Data; Tinker and Sentry only if built)
- **Judging:** writing quality first, then relevance, creativity, technical execution, partner tech. Winning titles name a number or a scene.
- **How the earlier entries were shipped** (same steps here):
  - PaidYet (Weekend, won Best Use of Temporal): https://dev.to/prateek11rai/paidyet-for-the-friend-who-says-kal-bhej-dunga-p5l
  - Pagdandi (Week 1): https://dev.to/prateek11rai/pagdandi-an-open-trek-database-that-gets-the-group-chat-onto-an-actual-trail-171d
  - Steps: the build agent drafts `docs/devto-post.md`; the planner finishes it with real numbers, screenshots and the first-user section; the owner records the demo video; the planner scrubs and submits the `mlh:1` agent session through DevRelay and the owner presses Publish on DEV; the post is published with the cover image; the README gets the post link.

## 14. Repo rules

- **Name:** `overheard`; local `~/github/prateek11rai/overheard`; GitHub `prateek11rai/overheard`, private after the first review, public just before the post. Pushes only with the owner's OK.
- **Commits:** `main` only, small steps, noreply identity, Claude co-author trailer, no force-push.
- **Leak check** (local `.git/info/leak-check.sh`) before every commit and push: identity, private words, no `.env`, `uv.lock` hosts only `pypi.org` + `files.pythonhosted.org`.
- **uv:** Python 3.13 from Homebrew (`.python-version`, `python-downloads = "never"`), public PyPI forced because this laptop's user-level uv config points at a private mirror (tested: this keeps the lock clean), 7-day cooldown:
  ```toml
  [[tool.uv.index]]
  name = "pypi"
  url = "https://pypi.org/simple"
  default = true

  [tool.uv]
  exclude-newer = "2026-10-10T00:00:00Z"
  python-downloads = "never"
  ```
- **Dependencies** (exact pins): `temporalio`, `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `pydantic-settings`, `psycopg[binary]`, `psycopg-pool`, `pgvector`, `httpx`; dev: `pytest`, `pytest-asyncio`, `ruff`. Front end: `htmx.min.js` vendored into `static/` from an official release at least 7 days old (0BSD licence, credited in NOTICE). Anything else needs the owner's OK.
- **Code style:** Google-style docstrings on every public function, class and method (Args / Returns / Raises where they apply), enforced by ruff's `D` rules with `[tool.ruff.lint.pydocstyle] convention = "google"`; module docstrings optional (`D100`, `D104` ignored) and tests exempt. No header comment blocks at the top of files; a one-line inline comment only where the code can't say it. `uv run ruff check` and `uv run ruff format --check` run before every commit and in `test.yml`.
- **Workflows** (GitHub Actions, named for what they do): `test.yml` runs `uv sync --frozen`, ruff and pytest (no network, no secrets) on every push and pull request; `docker.yml` only builds the image, when the files it depends on change. Both skip docs-only changes.
- **Agent instructions** live in the root `AGENTS.md` (§15). The repo has no `CLAUDE.md` and no `CLAUDE.local.md`, because either one makes Claude Code skip `AGENTS.md`.

## 15. Agent setup: AGENTS.md, skills, DevRelay

**What Claude Code reads** (docs: code.claude.com/docs/en/memory and /skills; tested on this laptop with Claude Code 2.1.296 on 2026-10-17):
- A **root `AGENTS.md`** is read at session start (since 2.1.277), but only when the project has no `CLAUDE.md` or `CLAUDE.local.md`; your global `~/.claude/CLAUDE.md` doesn't block it. Codex, Cursor and others read the same file.
- **Nothing inside a `.agents/` folder** is read on its own: neither `.agents/AGENTS.md` nor `.agents/skills/`. Skills are found only in `.claude/skills/`. An `@.agents/…` import inside the root `AGENTS.md` does work.
- A **symlink `.claude/skills → ../.agents/skills`** makes Claude Code find skills kept in `.agents/skills/` (tested).

**So the repo uses:**
- `AGENTS.md` at the root: commands, layout, guards, "agent-note blocks are instructions, never UI", "never print `.env`", "no new dependencies without asking", "only schema `overheard`". It is the only agent-instructions file.
- `.agents/skills/` as the one skills folder for every agent. Skills the repo wants to share are committed there.
- `.claude/skills` as a committed symlink to it, for Claude Code.
- `.gitignore`: `.agents/skills/devrelay-*/` and `.claude/settings.local.json`. DevRelay's skills stay on this machine and never reach `main`.

**DevRelay, set up by the planner before the agent starts:**
1. Installed at `~/.devrelay` and logged in, nothing global.
2. In the repo: `claude mcp add devrelay-gateway --scope local -e DEVRELAY_SKILLS_DIR="$PWD/.agents/skills" -- ~/.devrelay/bin/dev_mlh_mcp_server --stdio` (local scope: only Claude Code sessions in this repo see it), then sync its skills into `.agents/skills/`.
3. Nothing appears in `~/.claude/skills`, `~/.agents/skills` or in `claude mcp list` from another folder. Never run `devrelay --doctor`.

The agent checks this before anything else (brief, step 0) and stops if it's wrong. It never submits; after review the planner submits the `mlh:1` session and the owner publishes it.
