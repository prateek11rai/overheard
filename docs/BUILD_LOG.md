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
| ✓ | **Model card "in use" moves** / **Use** in Compare | With the owner's OK (A3): `ollama cp gemma4:e4b overheard-check:latest` (a local copy, no download). It appeared in Settings; clicking its card moved "in use" to it (toast "Now using overheard-check:latest", still true after reload); **Use** on `gemma4:e4b` in Compare models moved it back (also after reload). Then `ollama rm overheard-check:latest`. Screenshot: `docs/img/review/07d-settings-two-models.png`. |
| ✓ | **Run now** | Toast "Morning brief started"; a new row shows "running…", polls every 3 s, turns "done" and stops polling. |
| ✓ | **Check now** | Toast "Checking Shipnote now"; a new "Check · Shipnote" row (a demo product: "not checked"). |
| ✓ | Review **D / S / C / O / ?** | D: next post, 1 of 6, title (6). S: skipped, toast. C: "Reply copied". O: clicks the thread link (intercepted in the test so nothing left the laptop). ?: opens and closes the shortcut list. Undo from review shows the same post again. |
| ✓ | Review buttons | Skip, Done, Copy; **Shorter** rewrote the draft with local gemma. |
| ✓ | **Remove demo data** | Settings dialog → all demo products, posts, themes and runs gone → Setup. `demo clear` / `demo load` from the CLI work too. |

Re-checked after the package split: Done/Undo, Pause/Resume, time pick, Run now, review S/Undo.

CLI, live: `overheard brief` (0 products with demo only; `--reddit off` with `seed`'s two real products ran both scans offline), `seed` (idempotent), `email-test` (file mode writes a test email), `eval`, `demo load/clear`, `setup`, `doctor`, `start` (Ctrl-C stops all children, exit 0). The seeded products and test runs were removed afterwards.

## Acceptance (phase 1)

1. ✓ Fresh clone (scratch dir): `uv sync --frozen` and `uv run overheard setup` work; leak check clean.
2. ✓ `uv run ruff check` (docstring rules on), `uv run ruff format --check`, `uv run pytest`: 98 passed.
3. ✓ `setup` twice: second run applies 0 migrations (001 and 002 the first time). `tests/test_migrations.py` snapshots every relation, schema and extension outside `overheard` before and after two migrations: no change.
4. ✓ `start` brings up Temporal, worker, web; `doctor`: ✓ Postgres, Ollama, Temporal, worker, schedule; "not set yet" for SerpApi and SMTP (email shows file mode, because `.env` sets `EMAIL_MODE=file`).
5. ✓ `demo load` fills every screen; each was compared with its mockup (screenshots in `docs/img/review/`); `demo clear` empties it; `demo load` again works.
6. ✓ every interaction, including the model switch (A3).
7. ✓ `temporal schedule describe --schedule-id morning-brief` shows time, timezone, 12 h catch-up, and follows the UI.
8. ✓ No `<select>` or `type="time"`, no `agent-note` on any page (asserted in `test_every_page_renders_with_demo_data`); review mode works from the keyboard.
9. ✓ `.data/digests/` demo digests render like `08-email.html` (`docs/img/review/08-email*.png`).
10. ✓ `uv run overheard eval --model gemma4:e4b --synthetic-only` completed: 28/30, 100% quotes check out, 100% asking recall, 2.1 s per post (proposed labels, not yet checked). Settings › Compare models shows the row.
11. ✓ Leak check clean before every commit; `git log --format='%ae %ce' | sort -u` shows only the noreply address.

## Decisions made without asking, and why

- **htmx 2.0.10, not 4.0.0.** Both are older than 7 days; 4.0 changed swap and event behaviour, and the architecture's examples (`hx-swap-oob`, `HX-Trigger`, `every 3s`) are htmx 2.
- **Temporal only from Homebrew (A1, the owner's answer).** Nothing Temporal is ever downloaded: no time-skipping test server, no CLI. `settings.temporal_binary()` finds `$(brew --prefix)/bin/temporal` explicitly (never PATH: this laptop has an older `~/.local/bin/temporal` 1.3.0 first on PATH, which my first test version picked up), honours `OVERHEARD_TEMPORAL_BIN`, and fails with "install it with `brew install temporal`". `start`, `setup` and the workflow tests use it; the tests always run `WorkflowEnvironment.start_local(dev_server_existing_path=...)` with 1-second retry backoff (about 6 s for the workflow tests). CI installs the CLI with the runner's Homebrew (`/home/linuxbrew/.linuxbrew/bin/brew install temporal`).
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
- **Never emailing twice (A4).** Migration `002_digest_ledger.sql` adds `digest_ledger` keyed by (run, product). `mail.deliver_once()` checks it before sending and writes it right after the mail server accepts (or the file is written); quiet mornings with no email leave no record. *Review 1 (#13):* it now claims the row as `sending` first and marks it `sent` after, so two overlapping attempts can't both send; file names carry the run. `tests/test_digest_ledger.py` runs the real `send_digest` activity twice (Temporal `ActivityEnvironment`) in SMTP and file mode and proves one email or file, with the retry logged as "… · not sent again".
- **Reply drafts** are stored on the post (`reply_draft`), drafted on demand in review mode; demo drafts are written by hand.
- **Settings › Reddit searches** counts phrases of active products including demo ones, so the demo screen isn't empty. Run now and Check now costs count only products that can really be searched (0 without a key). *Review 1 (#7, #19):* one shared `reddit_budget()` / `phrase_cost()` for every number; demo products count only when there are no real ones (labelled "demo"); without a key every place says "Hacker News only".
- **Theme threshold 0.62** (cosine, embeddinggemma) is a guess from the model's typical similarities; tune on real posts in phase 2.
- **Hybrid search** (full-text + pgvector, reciprocal rank fusion, spec §7) is in `db.hybrid_search` and tested; no screen uses it yet (no mockup has search).
- **Security extras:** CSP without inline scripts (htmx `allowEval: false`), Host allowlist, same-origin check on every non-GET, HTTP Basic when `OVERHEARD_PASSWORD` is set, only http(s) post links rendered, CSV export guards against spreadsheet formulas.
- **500-line rule:** db, pipeline, workflows, web and demo became packages (public imports unchanged). `web/context.py` (537) and `workflows/activities.py` (544) are a little over "about 500"; `demo/mealwise.py` (469) is data. *Review 1 (#17):* both split; the largest module is now `workflows/runtime.py` (428).
- **Docker images** pinned to releases ≥ 7 days old: python 3.13.12-slim, uv 0.12.24, pgvector 0.8.7-pg17, temporalio/temporal 1.9.1, ollama 0.40.2. Not built here.
- **CI actions** are pinned to major tags (`checkout@v4`, `setup-python@v5`, `setup-uv@v6`), not SHAs; pin them before the first push. *Review 1 (#17):* pinned to commit SHAs (checkout v4.4.0, setup-python v5.6.0, setup-uv v6.8.0), and every compose, Dockerfile and CI service image by digest.

## Things I had to fix along the way

- `ruff format` reformats Python code blocks inside Markdown. Before the first commit it reformatted four blocks in `docs/design/architecture.md`; I restored them exactly from my earlier read (450 lines again) and excluded `docs/design` from ruff.
- zsh: a loop variable named `path` overwrote `$PATH` in one shell call (harmless, rerun).
- Background servers started with `( … &)` die when the tool call returns; I used proper background tasks instead.

## Network used in phase 1

PyPI (packages, `uv_build`); GitHub releases for IBM Plex Sans, JetBrains Mono and htmx; local Postgres, Temporal and Ollama. Nothing Temporal was downloaded (the workflow tests always used a local CLI). Also, read-only: the GitHub API to read release dates of uv, Temporal CLI, Ollama and pgvector, to pin Docker images at least 7 days old. Nothing else.

## Not done in phase 1 (by design or blocked)

- Live SerpApi, Hacker News, Tiger and SMTP; real runs; banner and cover; final screenshots; the eval with checked labels and Hacker News items by id.
- SMTP sending is written (STARTTLS/SSL with verification) but untested against a real server.
- *Compare on my last 50 posts* and *Re-embed* are wired and tested at the function level but not exercised live (they need a second chat / embedding model).

## Review 1

Brief: [design/review-1.md](design/review-1.md), 2026-10-17/18 (IST). One commit per item (#1 to #28), each with a test that failed before the fix, plus two follow-ups found in the re-walk. `uv run pytest`: 157 passed (98 after phase 1); ruff check and format clean; leak check clean before every commit.

### Re-walk checklist (real browser)

Same setup as step 14: Chromium through Playwright, 1440 px, `OVERHEARD_OFFLINE=1 uv run overheard start`, demo data. Every row changed the page and was still true after a reload. A console listener (errors, warnings and page errors) ran on every screen, popover and dialog: **0 entries**.

| | Interaction | Result |
|---|---|---|
| ✓ | **Done** on a card | Card leaves, counts and title drop; toast "Marked done" with Undo stays 8 s and pauses while hovered (#24). Still gone after reload. |
| ✓ | **Undo** | Card back with its counts. |
| ✓ | **Skip** | "Skipped · back tomorrow" with Undo; still gone after reload. |
| ✓ | **Show done** (#24) | Status line "Show done (N)" lists today's done posts under "Done today" with Undo; Undo brings each back. |
| ✓ | **Pause** (▾ menu) / **Resume** (Settings) | Toasts "Shipnote paused" / "resumed" (no Undo); tab, Settings row and the Settings search-plan block all refresh in the same response (#15); true after reload. |
| ✓ | Paused product leaves the plan | Active: its steps are in the run. Paused: "nothing to check". |
| ✓ | **◷ time pick** | 08:30 from the ◷ popover on Settings (chip and Settings picker both follow), 07:30 from the brief and 07:00 from the themes page (no Settings picker there: no `oobErrorNoTarget`, #25). Temporal follows. |
| ✓ | **Timezone** (#11) | Asia/Tokyo from the ◷ popover and Europe/London from Settings › Time: toast "Every morning at 07:00 Europe/London"; `temporal schedule describe` showed TimeZoneName Asia/Tokyo. Restored to Asia/Kolkata. |
| ✓ | **±5** | 08:35 in the chip, Settings and Temporal (minute 35, hour 8). |
| ✓ | **Pause all** | Chip "Paused"; Temporal Paused true, note "paused in Overheard"; survives reload. Restored: 07:00 Asia/Kolkata, 12 h catch-up, overlap skip, not paused. |
| ✓ | **Add product** (#21) | Badge "2 phrases · Hacker News only"; toast "Listening. First check started" (no Undo); brief "Listening. The first check is running." polls every 3 s, then shows the real state, and the ▾ menu flips from "Checking…" (disabled) to "Check now" without a reload. |
| ✓ | **Edit** | New description saved, "Saved" toast, same after reload. |
| ✓ | **Delete** (#16) | Dialog: "Its posts, themes and runs go too; the Reddit searches it used still count toward your SerpApi plan. This can't be undone." Confirm removes the tab, lands on the first product; same after reload. |
| ✓ | **Run now** (#3) | Toast "Morning brief started" (no Undo); while it runs the button says "Running…", disabled, "Already running", and a second Run now is refused. |
| ✓ | **Check now** | From the ▾ menu: "Checking Docsprout now" (no Undo), menu "Checking…" while it runs. On `/runs` the new row appears (#15). |
| ✓ | Review **D / S / C / O / ?** (#22) | Progress counts through the session: 1 of 7 → 2 of 7 → 3 of 7, filled segments; Undo → 2 of 7; reload keeps 2 of 7. C "Reply copied"; O clicks the thread link (intercepted); ? opens and closes the list. |
| ✓ | Review buttons | Skip, Done next, Copy; **Shorter** rewrote the draft with local gemma (169 → 164 characters), kept after reload. |
| ✓ | **Remove demo data** (#15) | Dialog → Setup, no tabs; the three demo digest files deleted, the non-demo test email kept; `uv run overheard demo load` brought everything back. |
| – | Model card / **Use** in Compare | Not re-walked: it needs a second installed model, and the phase-1 `ollama cp` was a one-off OK (A3). #9 is covered by tests (switch refused without Temporal; Reembed switches the setting last; new posts join old themes meanwhile). |

| | Item | Result |
|---|---|---|
| ✓ | 19 Reddit off | Settings: one line "Reddit off · Hacker News only" and "Add SERPAPI_API_KEY to .env to search Reddit too."; no equation. Add-product badge, a real product's ▾ menu and the ◷ Run now badge say "Hacker News only"; a demo product's menu says "demo". |
| ✓ | 20 Themes counts | "↑ 5 this week · 14 in 4 weeks" next to "All 18 posts"; "4 in 4 weeks" next to "All 5 posts"; "new · 5 posts". Brief: "↑ 14 in 4 weeks". |
| ✓ | 21 Never checked | See Add product. |
| ✓ | 22 Review progress | See Review. |
| ✓ | 23 Undo only where it reverses | No Undo on Run now, Check now, first check, pause, resume; Undo on Done and Skip. |
| ✓ | 24 Done recoverable | See Show done. |
| ✓ | 25 oob only where it fits | Time changed from the brief and themes pages: console empty. |
| ✓ | 26 Tab caret | Visible ▾ with "Mealwise menu" label (`10-product-menu.png`). |
| ✓ | 27 Setup badge | "✓ ready · Reddit off · email to file" (`01-setup.png`). |
| ✓ | 28 Send a test | Toast "Test email saved to .data/digests/<date>-test-test.html". |

Screenshots refreshed in `docs/img/review/` (all except `07d-settings-two-models.png`, which needs a second model); new: `02f-brief-show-done.png`.

### Found in the re-walk, fixed with a test

- **The bar stayed "Checking…" after a first check** (#21 follow-up). The brief poll swapped only the brief; its last response now also sends the bar.
- **Timezone names overlapped in Settings › Time** (#11 follow-up). The 66 px cell for time presets also applied to the timezone list (the ◷ popover was fine); timezone options now size to their text.

### Mistakes, and what I changed

- **#14 was committed with a failing test.** My check script piped `pytest` into `tail`, so its exit status was lost (expected `overheard, public`, got `overheard,public`). The next commit fixed the test; the script now runs with `set -eo pipefail`.
- **#10's commit had unsorted test imports.** I edited a test after the last ruff run; fixed in #11's commit. ruff now runs last.
- One walk script of mine posted Undo with an undefined id (a 422 in the console). A bug in the script, not the app; reran clean.

### Noticed, not changed (for the planner)

- Digest file names use the UTC date (`mail.write_file`), while the search ledger's day is in the schedule's timezone. At 00:48 IST the demo files say 2026-10-17. Same day for a 07:00 Asia/Kolkata schedule; a schedule in, say, Australia/Sydney would name the morning's file with the previous day.
- After **Check now** from a brief page (not a first check), the ▾ menu says "Checking…" until the next page load. Only `/runs` and the first-check brief poll refresh it on their own.

### Network in review 1

Local Postgres, Temporal and Ollama, plus read-only lookups for #17's pinning: the GitHub API (`gh api`) for action tags → commit SHAs and release dates, and Docker Hub (`auth.docker.io`, `registry-1.docker.io`) and `ghcr.io` (token and manifest requests) for image digests. Nothing pulled, built or downloaded; no SerpApi, Hacker News, Tiger or SMTP.

## Phase 2 · live services

Brief: [design/phase-2.md](design/phase-2.md), 2026-10-18 between 01:45 and about 03:00 IST, with the planner's answers A1, A2 and the email order. Tiger Cloud (Postgres 18.6, TimescaleDB 2.30.2, pgvector 0.8.7) through `DATABASE_URL`, SerpApi, Hacker News, the owner's SMTP, local Ollama (`gemma4:e4b`, `embeddinggemma:300m`) and a fresh Temporal dev server. `uv run pytest`: 185 passed (157 after review 1); ruff check and format clean; leak check clean.

### Fixes 1–5 (each with a test that failed first)

| | Fix | Commit |
|---|---|---|
| 1 | Schedule sync survives the create race on a fresh Temporal (`start` and the worker both create; the loser describes and updates). Two concurrent syncs tested with a barrier. | `b80474d` |
| 2 | `start` shows only Ollama's warnings and errors (`level=WARN/ERROR`, 5xx requests, fatal start errors). | `426c74f` |
| 3 | Digest files are dated by the schedule's timezone (digest activity, Send a test, demo). | `3b62c7c` |
| 4 | The ▾ menu flips back when a Check now finishes. First a poller inside the bar (`6efd938`); the live re-add showed `htmx:oobErrorNoTarget #bar` when another response replaced the bar mid-poll, so the poller now sits outside the bar and keys on the running runs. | `23d8b89` |
| 5 | Products without recipients mail `SMTP_USER`; the form, Setup and Settings say "your own mailbox (SMTP_USER)"; SMTP errors are raised by name and code only (`SMTPRecipientsRefused` quoted the address back into steps, Temporal and logs). | `c19ecdf` |

### Other changes made during the phase (each with a test)

- `cd61703` Hacker News requests go through a process-wide gate: at most one a second (the network rule).
- `baa96fb` A product's first check looks back a week (Reddit `qdr:w`, Hacker News 7 days); later runs look at the past day. My decision: the spec says "the past day or week", and a new product's first brief was otherwise empty.
- `8b5cd55` Hacker News matches every word of a phrase, not the exact phrase (Q2). The four phrases had 0 exact hits in a week, 11 all-words hits.
- `1a1191d` The add form's "first check now · N searches" badge follows the phrases (it stayed "0 searches").
- `0c206bf` A1: a hand-started Run now emails the whole open brief; scheduled mornings and Check now send what that run found.
- `3b113c6`, `df7e0ac` Phrases already searched today are free in the plan and never take turns; the Run log says "reused from today".
- `ca0ebae` Settings names `SERPAPI_MONTHLY_CAP` next to the plan and says how many a day fit ("rotating · 3 a day").
- `8c4ca66` `doctor` prints "TimescaleDB 2.30.2 · time_bucket works" (or that counts use `date_bin`).

### Every live SerpApi search (9 by the ledger, 5 billed; cap 12)

SerpApi's `this_month_usage` was 5 before the phase and 10 after (`account.json` is free; I read it between steps). The planner's ledger counts 9.

| Time (IST) | Product | Phrase | Window | Posts | Run | Billed |
|---|---|---|---|---|---|---|
| 02:04:59 | Supabase (first add) | supabase alternative | week | 1 | check-supabase-…203450 | yes |
| 02:05:01 | Supabase (first add) | supabase auth | week | 9 | same | yes |
| 02:07:19 | Obsidian (first add) | obsidian sync | week | 4 | check-obsidian-…203714 | yes |
| 02:07:28 | Obsidian (first add) | obsidian alternative | week | 0 | same | yes |
| 02:30:15 | Supabase (re-added, A2) | supabase alternative | week | 1 | check-supabase-…210010 | no¹ |
| 02:30:17 | Supabase (re-added) | supabase auth | week | 9 | same | no¹ |
| 02:30:51 | Obsidian (re-added) | obsidian sync | week | 4 | check-obsidian-…210046 | no¹ |
| 02:30:53 | Obsidian (re-added) | obsidian alternative | week | 0 | same | no¹ |
| 02:42:29 | Obsidian | obsidian plugins (temporary, step 8) | day | 0 | check-obsidian-…211219 | yes |

¹ SerpApi's counter didn't move: the same queries had run 25 minutes earlier, and SerpApi serves an identical query within an hour from its cache without charge. The ledger still counts them, so the app's caps stay on the safe side.

The Run now (step 7) planned all 4 phrases and reused them all: 0 searches. No scheduled morning ran during the phase.

### Every email (3 of 3, all to the owner's own address)

| Time (IST) | Email | How |
|---|---|---|
| 02:36:47 | "Overheard: test email" | `uv run overheard email-test` |
| about 02:37:40 | Supabase digest, the whole open brief | Run now (A1) |
| about 02:37:47 | Obsidian digest, the whole open brief | Run now (A1) |

The owner confirmed in the window that all three arrived and look right. Every other digest went to a file: the four first checks (`EMAIL_MODE=file`), and step 8's check (its worker ran with `EMAIL_MODE=file` in its own environment, as agreed; `.env` was edited once, `file` → `smtp`, with a silent `sed`).

### Every run

| Run | Kind | Started (IST) | Searches | New posts | Result |
|---|---|---|---|---|---|
| check-supabase-…203450 | first check | 02:04:50 | 2 | 10 | done; product deleted for A2 |
| check-obsidian-…203714 | first check | 02:07:14 | 2 | 4 | done; product deleted for A2 |
| check-supabase-…210010 | first check | 02:30:10 | 2 | 13 | done in 65 s |
| check-obsidian-…210046 | first check | 02:30:46 | 2 | 12 | done in 62 s (1 quote re-checked, 1 unverified) |
| run-now-…210703 | Run now (morning) | 02:37:03 | 0 | 0 | done in 39 s; 4 searches reused; 2 emails |
| check-obsidian-…211219 | Check now (step 8) | 02:42:19 | 1 | 1 | done in 87 s; worker killed mid-run, `search_reddit` retried |

### Step 6 · the live data

- Supabase: 13 posts (10 Reddit, 3 Hacker News); Obsidian: 13 (4 Reddit, 9 Hacker News, one from step 8's check).
- 26 posts: 25 labelled with a quote that `verify_quote()` finds in the post again (re-checked from the database at the end, 0 failures), 1 hidden as unverified. Labels: 20 mentions you, 2 asking for a tool, 1 pain point, 2 off-topic.
- 1 theme formed ("Difficulty adding new data easily", Supabase). Labelling took 2.9 s a post (73 s for the 25 posts of the first checks).
- The digest files have the same structure as `08-email.html` (headline, Worth a reply, Pain points, Mentions, Open the brief); `docs/img/email.png`.
- The Run log and the Temporal UI show every run (`docs/img/runs.png`, `docs/img/temporal-runs.png`).

### Step 8 · ledger crash test, live

Obsidian got a temporary third phrase, `obsidian plugins`. `start` was stopped and its parts were run separately (Ollama, the Temporal dev server with the same arguments, `overheard web`, and `EMAIL_MODE=file overheard worker`), so that only the worker could be killed. A watcher polled `search_ledger` every 50 ms and sent SIGKILL to the worker as soon as the row appeared.

- 02:42:29.713: the paid Reddit search for `obsidian plugins` was saved. 02:42:29.884: worker killed (exit 137) before it reported the activity complete.
- The restarted worker picked the run up. Temporal timed the first attempt out (40 s) and retried; attempt 2 found the row: "Reddit · obsidian plugins · retried · reused from today · 0 posts" (`docs/img/runs-crash-test.png`, `docs/img/temporal-retry.png`).
- `search_ledger` has one Reddit row for the phrase (created by the interrupted attempt, used once), and SerpApi's counter went 9 → 10. Paid once.
- The phrase was removed afterwards; both products have two phrases again.

### Step 9 · state at the end

`overheard doctor`: ✓ Postgres (pgvector 0.8.7), ✓ TimescaleDB 2.30.2 · time_bucket works, ✓ Ollama, ✓ Temporal, ✓ worker, ✓ schedule (morning-brief · daily 07:00 Asia/Kolkata · on), ✓ SerpApi, ✓ Hacker News, ✓ SMTP. `temporal schedule describe --schedule-id morning-brief`: hour 7 minute 0, Asia/Kolkata, overlap Skip, catch-up 12 h, not paused.

### Step 10 · eval and TimescaleDB

- `uv run overheard eval --model gemma4:e4b --markdown` on 90 posts (30 synthetic, 60 Hacker News items fetched live by id; none had been deleted). Labels proposed, not yet checked by a person. Right label 76/90 (synthetic 28/30, Hacker News 48/60), quote checks out 94% (5 Hacker News posts unverified), asking recall 8/8, 2.9 s a post. The result file keeps the UTC date, so it replaced phase 1's `2026-10-17-gemma4-e4b.json` (still in history).
- The Hacker News items were picked from Algolia searches over the last 60–180 days (16 requests). "Who wants to be hired" comments were left out because they carry personal email addresses, and so were political threads.
- TimescaleDB recorded check (scratch script, run at 02:21 and again at 02:58): on Tiger, `theme_stats` through `time_bucket` and through the forced `date_bin` fallback gave the same weekly counts for both products. Tests cover `date_bin` and the doctor line on local Postgres, which has no TimescaleDB.

### Steps 11–13 · assets and docs

- `docs/img/banner.png` (1280×400) and `docs/img/devto-cover.png` (1000×420): mockups 10 and 11 filled with "What's the backend database you are using for your apps?" (r/appledevelopers, asking for a tool, quote verified). No username, no address, no product name that isn't ours. Rendered on loopback with the app's own fonts (the mockups' Google Fonts link was dropped, since it isn't on this phase's network list).
- Screenshots with the live data in `docs/img/` (`docs/img/review/` stays as the demo record). Each was refused if the page text contained anything shaped like an email address; none was.
- README, RUNBOOK and `docs/devto-post.md` now carry the live numbers. TODOs left for the planner and owner: the post title, the owner's sentence, the cover upload, the video, the agent session, My First User, and the README's link to the DEV post.

### Mistakes, and what I changed

- **`3b113c6` went in with ruff E501.** My commit command piped the check script into `tail`, so a failing check still committed (the same trap as review 1's #14, one level up). Fixed in `df7e0ac`; since then every commit runs only when the check script itself exits 0.
- The first two first checks were redone (A2) after I found the exact-phrase Hacker News query was too strict; the four repeat searches came from SerpApi's cache.
- My scratch inspection script was named `inspect.py` and shadowed the standard library; renamed.

### Noticed, not changed

- At 02:04:06 Temporal's SQLite store logged 53 "context deadline exceeded" errors within one second, while Ollama was first loading `gemma4:e4b`; it recovered and every run finished. It didn't happen again, including during the 5-minute eval. Once, Temporal logged a "context canceled" when I left its UI mid-load. The other starts were clean.
- The ▾ menu's Check now cost says "3 searches" even when some of those phrases were already paid today (it shows the per-check cost, not today's new spend).
- Digest file names: the date is local now, but the run tag still carries the workflow id's UTC timestamp (`…-upabase20261017t210010…`).
- The bar poller now polls every 3 s on every page, also when nothing runs (one small query, so the bar can show runs started elsewhere).
- Hacker News "Who wants to be hired" comments are real posts and can contain their authors' email addresses; they are stored like any post and shown in full in review mode. None of the screenshots shows one.
- The 07:00 schedule is on, and the app is stopped at the end of the phase. If `overheard start` runs today before 19:00 IST, Temporal catches up the missed 07:00 morning. That run falls on the same day as today's ledger, so it reuses the 4 searches (0 spent) and emails only what it finds new; with nothing new it sends nothing (quiet-morning emails are off). Tomorrow's 07:00 run is a normal morning: up to 3 searches under the cap, plus the emails.

### Network in phase 2

SerpApi: the 9 searches above plus free `account.json` reads. Hacker News (Algolia, at most one request a second through the gate): about 15 for the runs, 10 diagnostics (exact vs all-words matching), 16 to pick eval items, 60 item fetches during the eval. Tiger through `DATABASE_URL`. SMTP: the 3 emails above, to the owner's own address. Local Ollama and Temporal. No PyPI changes (no new dependencies), no Reddit pages, no Google Fonts, no push or publishing.

## Ship · diagrams, product video, post draft

Brief: [design/ship.md](design/ship.md), 2026-10-18 from 03:10 IST, with the planner's answers A0 (relative paths inside the repo; absolute raw URLs only in the DEV post) and A1 (write "about $1.3k", not "~$1.3k", because GitHub reads paired single tildes as strikethrough).

- **1 · Polish** (`16a2a01`): when a label's verified quote comes from the title, the brief, review mode and the email highlight the title; a comment's thread title never; a title-only story no longer repeats its title. Tested.
- **2 · Social preview** (`20694fb`): `docs/img/social-preview.png`, 1280×640, the banner design with the same real post; a layout check confirmed every element sits inside the middle 1100×560.
- **3 · Diagrams** (`94c4270`): five UML diagrams as static HTML + SVG in `docs/design/diagrams/` (one shared stylesheet with the app's palette, fonts and block edges), rendered at 2× into `docs/img/diagrams/`. A scratch script generated the pages, and only the pages are committed. The README shows the architecture and the morning run.
- **4 · Product video** (`a03f084`): `docs/demo/overheard-demo-product.mp4` and `~/Downloads/overheard-demo-product.mp4`, 128 s, 1440×900, H.264 yuv420p 30 fps, 6.6 MB. It has seven scenes, each recorded in its own Playwright context with `recordVideo`, plus title cards rendered from HTML in the app's fonts (this ffmpeg has no drawtext). Still frames are halved with `mpdecimate=max=1`, so waits for the database shrink and motion keeps its speed. A cursor dot, click ring and key badge were injected into the recording context only. Every screen's text was scanned for anything shaped like an email address during recording (none), and frames were reviewed afterwards. Review mode showed only the first two Supabase posts: the third is a Hacker News comment whose body contains its author's address. Both Undos put the data back. The add-product form was never submitted. The email clip is merged when the owner sends it.
- **5 · DEV post** (`837feab`): full draft, about 2,000 words, three title options in a comment, all five diagrams and three screenshots by raw URL. TODOs remain for the planner and owner.
- **6 · Release notes** (`5578f78`): `docs/release-notes-v0.1.0.md`, docs by relative path; the planner attaches the banner.

**Budget: 0 searches, 0 emails.** The app ran as separate processes, with the worker started as `EMAIL_MODE=file uv run overheard worker` (its environment checked with `ps`). Since 03:10 IST the ledger has 0 new Reddit rows (9 in total, as after phase 2) and SerpApi's counter still reads 10. The only run was the video's Check now on Obsidian: it reused today's two searches, found nothing new and sent no email ("Quiet morning · no email"). `digest_ledger` has no rows since 03:10.

Noticed, not changed: `docs/design/spec.md` line 14 has the same `~$` strikethrough as the README had (the planner's file).

## Questions from the agent

Phase 2, asked 2026-10-18 around 02:10 IST. **Answered** in [design/phase-2.md](design/phase-2.md) (A1 option 1, A2 option 1, email order); applied as described above. Steps 1–5 are done and committed, as are the parts of step 10 that don't need the live run (the HN items by id, `doctor`'s TimescaleDB line). Step 6's first checks ran live: 4 SerpApi searches, 14 Reddit posts, every quote verifies. No live searches or emails since.

**Q1 (step 7): the Run now email would be empty.** The morning email shows only posts *found in that run* (a phase-1 decision, so posts don't repeat every morning). Step 7's Run now reuses the day's Reddit results from the ledger, so none of its Reddit posts are new to that run. Only Hacker News posts that appeared since the first check would count, and HN had nothing in the past day for these phrases. So both digests would be "quiet", and with quiet-morning emails off (the default), **nothing would be emailed**.
1. **Recommended:** a hand-started **Run now** emails the full open brief (what the app shows). Scheduled mornings and Check now stay "new in this run". A small change with a test; both emails would have real content.
2. No code change: switch `EMAIL_MODE` to `smtp` before redoing step 6's first checks (see Q2), so their digests are the emails (2) plus `email-test` (1) = 3. Step 7's Run now still shows the 0-search reuse but sends nothing.
3. Turn on "Send a short email" for quiet mornings on both products, so Run now emails "Nothing new about X since yesterday" (not a real digest).

**Q2 (step 6): no Hacker News posts arrived.** HN was queried with the exact phrase in quotes (my phase-1 choice; the spec doesn't ask for it). None of the four phrases occurs verbatim on HN in the past week, although "supabase" alone has 13 hits and "obsidian" 26. I've switched HN to all-words matching (`8b5cd55`). The same phrases then match 11 HN items in the past week (3/0/6/2), but 0 in the past day. A product's first check looks back a week (`baa96fb`, my decision: the spec says "the past day or week"); later runs look back a day. So:
1. **Recommended:** delete Supabase and Obsidian and add them again through the UI, so their first checks rerun with all-words HN matching. That's +4 SerpApi searches, for a phase total of 4 + 4 + 0 (step 7) + 1 (step 8) = **9 of 12**.
2. Keep them as they are: HN posts arrive only from later past-day runs, so the brief shows Reddit only for now.

If you pick Q2 option 1, Q1 option 2 also works without code: switch to `smtp` first, and the re-added products' first-check digests become the 2 emails.

**FYI, decided (safe direction, say if you disagree):**
- **Step 8:** after the `.env` switch to `smtp`, step 8's Check now would send a 4th email. I'll run that step's worker with `EMAIL_MODE=file` set in its process environment (not an `.env` edit), so it writes a file instead.
- **Step 8 phrase:** I'll add a third phrase to Obsidian so there is "one phrase not yet searched today" (1 search), then remove the phrase afterwards so both products end with two phrases.
- **The 07:00 schedule:** a scheduled morning while I'm still working would spend up to 4 more searches and send up to 2 emails, over both caps. If the work runs past about 06:50 IST, I'll move the morning time away and set it back to 07:00 afterwards, so no scheduled run fires during this phase.
