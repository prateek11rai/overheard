# AGENTS.md

Rules for any coding agent (Claude Code, Codex, Cursor, …) working in this repo. This is the only agent-instructions file: there is no `CLAUDE.md` or `CLAUDE.local.md`, because either one makes Claude Code skip this file.

## What this is

Overheard: every morning, what people said on Reddit (through Google via SerpApi) and Hacker News about the problems each of your products solves. One Python project managed with uv. FastAPI + Jinja2 + HTMX render the pages; a Temporal worker runs the morning job; a local open-weight model (Ollama) labels posts.

- What it does: `docs/design/spec.md`
- How it's built: `docs/design/architecture.md`
- How it looks: `docs/design/mockups/` (v4)

## Commands

```bash
uv sync                          # install (Python 3.13 from the system, never downloaded)
uv run overheard setup           # .data/, migrations, tool checks
uv run overheard doctor          # ✓/✗ for every piece, never a secret
uv run overheard start           # Temporal dev server + worker + web, Ctrl-C stops all
uv run overheard demo load       # sample products for trying the app (demo clear removes them)
uv run ruff check && uv run ruff format --check
uv run pytest -q                 # uses the overheard_test database
```

## Layout

- `src/overheard/`: `cli.py` (the `overheard` command), `settings.py`, `db/` (every query: models, core, products, posts, themes, runs), `migrations/`, `ai.py` (models by role), `sources.py` (SerpApi, Hacker News), `pipeline/` (quotes, search plan and ledger, labels, themes, brief), `workflows/` (Temporal workflows, activities, schedule and worker), `mail.py`, `web/` (security, shared helpers, pages, actions), `demo/`, `evals.py`, `templates/`, `static/`. A file that passes about 500 lines becomes a folder of the same name.
- `tests/` with `tests/fixtures/` (recorded API replies, never live calls in tests)
- `data/eval/` checked posts for comparing models
- `.agents/skills/` is the one skills folder for every agent; `.claude/skills` is a symlink to it

## Rules

- Dashed yellow `agent-note` blocks in the mockups are instructions for the builder. They never appear in the app.
- Never print, cat, grep, log or summarise `.env`, keys, passwords or email addresses. The app reads `.env` through `settings.py`; the UI shows whether a secret is set, never its value.
- No new dependencies without asking the owner. Exact pins in `pyproject.toml`, public PyPI only, 7-day cooldown (`exclude-newer`).
- Only touch the database schema `overheard` (or `OVERHEARD_DB_SCHEMA`). Tests use the `overheard_test` database.
- Every server listens on 127.0.0.1. The web app refuses another host unless `OVERHEARD_PASSWORD` is set.
- Post text is untrusted: it goes to the model as data, labels come from a fixed set, quotes are verified, the model gets no tools, and templates escape everything.
- Code style: Google-style docstrings on public functions, classes and methods (ruff `D` rules); no header comment blocks; a one-line comment only where the code can't say it.
- Before every commit: `uv run ruff check`, `uv run ruff format --check`, `uv run pytest -q`, and the local leak check. Never `--no-verify`.
- No pushes, releases or publishing without the owner's OK.
