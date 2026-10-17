# Build log · phase 1

Build agent in tmux `mlh:1`, 2026-10-17 (IST). Brief: [design/build-brief.md](design/build-brief.md). Everything ran locally: Homebrew Postgres 17 + pgvector 0.8.7 (`overheard`, `overheard_test`), the Temporal dev server, Ollama 0.35.1 with `gemma4:e4b` and `embeddinggemma:300m`, and demo data. No SerpApi, Tiger, SMTP or Hacker News calls: the walk-through ran with `OVERHEARD_OFFLINE=1`.

## Step 14 checklist (real browser)

Chromium through Playwright, 1440 px wide, against `uv run overheard start` with demo data. "Reload" means a fresh page load from the server after the action.

| | Interaction | Result |
|---|---|---|
| ✓ | **Done** on a card | Card left; Worth a reply 2 → 1; tab and title (7) → (6); "Go through all 6"; toast "Marked done" with Undo. Still gone after reload. |
| ✓ | **Undo** | Card back, (7) again, toast "Back in the brief". |
| ✓ | **Skip** on a card | Same as Done with "Skipped · back tomorrow"; still gone after reload (snoozed to the next morning time); Undo brings it back. |
| ✓ | **Pause** (▾ menu) | Tab dims (opacity .6) and says "paused"; the menu item flips to Resume; brief subline starts "Paused ·". After reload the Settings row says paused with Resume. |
| ✓ | **Resume** (Settings) | Row and tab flip back with a toast. |
| ✓ | Paused product leaves the plan | Run now with the product active: its steps are in the run. Paused: the run says "nothing to check" and has no steps for it. |
| ✓ | **◷ time pick** | 08:30 in the top-bar grid → chip "Daily at 08:30", Settings time 08:30; `temporal schedule describe` → hour 8, minute 30, Asia/Kolkata, catch-up 12 h. |
| ✓ | **±5 stepper** (Settings) | 08:35 in the chip, Settings and Temporal. |
| ✓ | **Pause all** | Chip "Paused"; `temporal schedule describe` → Paused true, note "paused in Overheard". Survives reload. Restored to 07:00 and unpaused afterwards. |
| ✓ | **Add product** | Typing what it does filled 5 phrases from local gemma; Enter added a chip, × removed one, competitor chip added. Start listening → new tab and its brief ("Nothing new about Mealwise since yesterday."), toast "Listening. First check started"; the check ran through Temporal (offline steps) and appeared in the Run log. |
| ✓ | **Edit** | Changed description and quiet mornings, Save → the page reloads from the server with the saved values and a "Saved" toast. |
| ✓ | **Delete** | ▾ › Delete… opens a `<dialog>`; confirming removes the tab and goes to the first product. |
| ✗ | **Model card "in use" moves** / **Use** in Compare | Not shown live: only one chat model and one embedding model are installed, and `ollama pull` is off limits (Q3). Covered by `test_models_use_saves_the_pick` (the checked card moves and persists). |
| ✓ | **Run now** | Toast "Morning brief started"; a new row shows "running…", polls every 3 s, turns "done" and stops polling. |
| ✓ | **Check now** | Toast "Checking Shipnote now"; a new "Check · Shipnote" row (a demo product: "not checked"). |
| ✓ | Review **D / S / C / O / ?** | D: next post, 1 of 6, title (6). S: skipped, toast. C: "Reply copied". O: clicks the thread link (intercepted in the test so nothing left the laptop). ?: opens and closes the shortcut list. Undo from review shows the same post again. |
| ✓ | Review buttons | Skip, Done, Copy; **Shorter** rewrote the draft with local gemma. |
| ✓ | **Remove demo data** | Settings dialog → all demo products, posts, themes and runs gone → Setup. `demo clear` / `demo load` from the CLI work too. |

Re-checked after the package split: Done/Undo, Pause/Resume, time pick, Run now, review S/Undo.

CLI, live: `overheard brief` (0 products with demo only; `--reddit off` with `seed`'s two real products ran both scans offline), `seed` (idempotent), `email-test` (file mode writes a test email), `eval`, `demo load/clear`, `setup`, `doctor`, `start` (Ctrl-C stops all children, exit 0). The seeded products and test runs were removed afterwards.

## Acceptance (phase 1)

1. ✓ Fresh clone (scratch dir): `uv sync --frozen` and `uv run overheard setup` work; leak check clean.
2. ✓ `uv run ruff check` (docstring rules on), `uv run ruff format --check`, `uv run pytest`: 93 passed.
3. ✓ `setup` twice: second run applies 0 migrations. `tests/test_migrations.py` snapshots every relation, schema and extension outside `overheard` before and after two migrations: no change.
4. ✓ `start` brings up Temporal, worker, web; `doctor`: ✓ Postgres, Ollama, Temporal, worker, schedule; "not set yet" for SerpApi and SMTP (email shows file mode, because `.env` sets `EMAIL_MODE=file`).
5. ✓ `demo load` fills every screen; each was compared with its mockup (screenshots in `docs/img/review/`); `demo clear` empties it; `demo load` again works.
6. ✓ except the model switch (table above).
7. ✓ `temporal schedule describe --schedule-id morning-brief` shows time, timezone, 12 h catch-up, and follows the UI.
8. ✓ No `<select>` or `type="time"`, no `agent-note` on any page (asserted in `test_every_page_renders_with_demo_data`); review mode works from the keyboard.
9. ✓ `.data/digests/` demo digests render like `08-email.html` (`docs/img/review/08-email*.png`).
10. ✓ `uv run overheard eval --model gemma4:e4b --synthetic-only` completed: 28/30, 100% quotes check out, 100% asking recall, 2.1 s per post (proposed labels, not yet checked). Settings › Compare models shows the row.
11. ✓ Leak check clean before every commit; `git log --format='%ae %ce' | sort -u` shows only the noreply address.

## Decisions made without asking, and why

- **htmx 2.0.10, not 4.0.0.** Both are older than 7 days; 4.0 changed swap and event behaviour, and the architecture's examples (`hx-swap-oob`, `HX-Trigger`, `every 3s`) are htmx 2.
- **Temporal tests on the Homebrew dev server.** `start_time_skipping()` downloads a test server from temporal.download, which isn't on the phase-1 network list (Q1). The tests use `start_local(dev_server_existing_path=/opt/homebrew/bin/temporal)` with 1-second retry backoff (6 s for 5 tests). `OVERHEARD_TIME_SKIPPING=1` switches to the time-skipping server (CI would download it).
- **`python-preference = "only-system"` in `[tool.uv]`.** A uv-managed CPython 3.13.3 was picked ahead of Homebrew's 3.13.12; this forces the system one.
- **`uv_build` as build backend.** `[project.scripts]` needs the project installed; uv_build is Astral's backend, build-time only, not in `uv.lock`.
- **Extra setting `OVERHEARD_OFFLINE`** (documented in RUNBOOK, not in `.env.example`). Phase 1 must not call Hacker News, but adding a product starts a real check. Offline, searches are skipped and say so in the Run log; the whole Temporal path still runs.
- **`.agents/AGENTS.md` is git-ignored.** DevRelay installed it; it tells agents to use DevRelay unprompted, which the brief forbids. Treated like `.agents/skills/devrelay-*/` (Q2).
- **Skip on brief cards.** The mockup shows only Done on a card, but step 14 asks for "Done / Skip on a card".
- **What the brief shows:** posts with a useful label (asking, pain, you, competitor), not done, not snoozed, found in the last 7 days. Tab and title counts use the same rule. **The morning email** shows only posts found in that run, so open posts don't repeat in every email and a quiet morning is really quiet (found in self-review, tested).
- **Thinking off for Ollama.** gemma4 thinks by default: 13 s and a worse label on a sample call versus 1.6 s with `reasoning_effort: "none"`. Sent only when the server is Ollama.
- **Prompt-injection guard beyond the quote check.** A `you` label needs the product's name inside the verified quote, a competitor label a listed competitor's name; otherwise retry, then unverified. Post text goes in as JSON data; no tools; JSON-schema output.
- **Weekly buckets:** `time_bucket('7 days', found_at, origin => now())` with TimescaleDB, else `date_bin('7 days', found_at, now())`, i.e. rolling weeks, instead of `date_trunc('week')`, so "this week" doesn't reset every Monday.
- **Themes order:** growing, then new, then steady (each by growth, then size). This matches the mockup's order; plain growth ratio would put a brand-new theme first.
- **Run log data:** `runs` has `searches_used`, `new_posts` (stored when a run finishes, live while it runs) and a `demo` flag, because a morning run spans products; demo clear removes demo runs too. `run_steps` are idempotent per step key, so a retried activity updates its row (↻).
- **Reply drafts** are stored on the post (`reply_draft`), drafted on demand in review mode; demo drafts are written by hand.
- **Settings › Reddit searches** counts phrases of active products including demo ones, so the demo screen isn't empty. Run now and Check now costs count only products that can really be searched (0 without a key).
- **Theme threshold 0.62** (cosine, embeddinggemma) is a guess from the model's typical similarities; tune on real posts in phase 2.
- **Hybrid search** (full-text + pgvector, reciprocal rank fusion, spec §7) is in `db.hybrid_search` and tested; no screen uses it yet (no mockup has search).
- **Security extras:** CSP without inline scripts (htmx `allowEval: false`), Host allowlist, same-origin check on every non-GET, HTTP Basic when `OVERHEARD_PASSWORD` is set, only http(s) post links rendered, CSV export guards against spreadsheet formulas.
- **500-line rule:** db, pipeline, workflows, web and demo became packages (public imports unchanged). `web/context.py` (537) and `workflows/activities.py` (544) are a little over "about 500"; `demo/mealwise.py` (469) is data.
- **Docker images** pinned to releases ≥ 7 days old: python 3.13.12-slim, uv 0.12.24, pgvector 0.8.7-pg17, temporalio/temporal 1.9.1, ollama 0.40.2. Not built here.
- **CI actions** are pinned to major tags (`checkout@v4`, `setup-python@v5`, `setup-uv@v6`), not SHAs; pin them before the first push.

## Things I had to fix along the way

- `ruff format` reformats Python code blocks inside Markdown. Before the first commit it reformatted four blocks in `docs/design/architecture.md`; I restored them exactly from my earlier read (450 lines again) and excluded `docs/design` from ruff.
- zsh: a loop variable named `path` overwrote `$PATH` in one shell call (harmless, rerun).
- Background servers started with `( … &)` die when the tool call returns; I used proper background tasks instead.

## Network used in phase 1

PyPI (packages, `uv_build`); GitHub releases for IBM Plex Sans, JetBrains Mono and htmx; local Postgres, Temporal and Ollama. Also, read-only: the GitHub API to read release dates of uv, Temporal CLI, Ollama and pgvector, to pin Docker images at least 7 days old. Nothing else.

## Not done in phase 1 (by design or blocked)

- Live SerpApi, Hacker News, Tiger and SMTP; real runs; banner and cover; final screenshots; the eval with checked labels and Hacker News items by id.
- SMTP sending is written (STARTTLS/SSL with verification) but untested against a server.
- *Compare on my last 50 posts* and *Re-embed* are wired and tested at the function level but not exercised live (they need a second chat / embedding model).
