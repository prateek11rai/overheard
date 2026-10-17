# Phase 2 · live services

> Brief from the planner after `DONE-overheard-review1` and the owner's go. Same rules as `build-brief.md` (Google docstrings, no header comments, no Temporal downloads, commit per step, ruff + pytest + leak check before each commit, no push or publishing), except the network rules below. Don't edit this file; answers get appended at the bottom.

## What's ready

- `.env` now points at a **new Tiger Cloud service** (Postgres 18.6, TimescaleDB 2.30.2, pgvector 0.8.7) and has `SERPAPI_API_KEY`, `SMTP_HOST/PORT/USER/PASS`, `SERPAPI_MONTHLY_CAP=60` and `EMAIL_MODE=file`. The planner ran `overheard setup` (schema `overheard`, 7 migrations) and `overheard doctor` (all ✓). Never print, cat, grep or summarise `.env`, and never print or screenshot the email address in `SMTP_USER`.
- Phase 1's local Temporal history and demo digests were moved to `.data/phase1/`; Temporal started fresh. The Tiger database has no demo data; keep it that way.
- The planner has stopped its own `overheard start`. Run the app yourself in a background shell (`uv run overheard start`), restart it after code changes, and stop it when you finish.

## Network for phase 2

SerpApi (**at most 12 live searches in this phase**, each logged in BUILD_LOG; the cap in `.env` is a backstop), Hacker News (Algolia and Firebase, ≤ 1 request/second), Tiger via `DATABASE_URL`, SMTP **only to the owner's own address (`SMTP_USER`)**, local Ollama and Temporal, PyPI. Never fetch Reddit pages. You may change exactly one line of `.env`, `EMAIL_MODE=file` → `EMAIL_MODE=smtp`, with a `sed` that prints nothing, when step 7 says so.

## Do, in order

**Fixes first** (each with a test that fails before and passes after)
1. **Fresh-start crash.** On a brand-new Temporal, `overheard start` and the worker both create the `morning-brief` schedule; the loser raises `ScheduleAlreadyRunningError` and `start` dies. Make schedule sync race-safe: if create says it already exists, describe and update instead. Test two concurrent syncs.
2. **Ollama's request logs flood `overheard start`** (`[ollama] [GIN] … GET /api/tags` lines). Show only Ollama's warnings and errors in the combined output.
3. **Email file names use the UTC date** while the ledger counts days in the schedule's timezone. Use the schedule's timezone for the date everywhere a "morning" is named.
4. **After Check now on a brief page**, the ▾ menu says "Checking…" until a reload. It flips back when that product's run finishes, like the bar does.
5. **Default recipient.** A product with no recipients sends its digest to `SMTP_USER` (the owner's own mailbox). The add/edit form and Settings say "your own mailbox (SMTP_USER)" instead of showing the address, and the address never appears in logs, run steps or screenshots.

**Live run with two real products** (the owner wants real topics, so the brief shows real discussion)
6. Through the UI in Playwright (1440 px wide), add two well-known products with **two phrases each** and no recipients (so step 5 sends to the owner):
   - **Supabase** · "Open-source Postgres backend: database, auth, storage and edge functions in one place." · phrases `supabase alternative`, `supabase auth` · competitor `Firebase`.
   - **Obsidian** · "Markdown note-taking app that keeps notes as local files, with links, graph view and plugins." · phrases `obsidian sync`, `obsidian alternative` · competitors `Notion`, `Logseq`.
   Each first check spends at most 2 searches. Check: real Reddit and Hacker News posts arrive; every label has a quote that passes `verify_quote()`; themes form; Run log steps and the Temporal UI show the runs; the digest files in `.data/digests/` look like `08-email.html`.
7. Switch `EMAIL_MODE` to `smtp` (the one allowed `.env` edit), restart, send `uv run overheard email-test`, then **Run now**. It must reuse the day's Reddit results from the ledger (0 new searches; show it) and email both digests to the owner. **At most 3 emails in this phase.** Ask the owner in this window to confirm they arrived and look right before going on.
8. Ledger crash test, live: start a Check now for one product with **one** phrase not yet searched today, kill the worker while it runs, restart it, and show from `search_ledger` that the phrase was paid once.
9. Let `overheard doctor` and `temporal schedule describe --schedule-id morning-brief` confirm the state; leave the schedule on at 07:00 Asia/Kolkata.

**Comparison, assets, docs**
10. `uv run overheard eval --model gemma4:e4b` on the full checked set: add up to 60 Hacker News items **by id** (fetched live, no third-party text stored) to the synthetic set, propose labels in `data/eval/labels.proposed.jsonl`, and say clearly in Settings and the README that the labels are proposed, not yet checked by a person. Run it on the TimescaleDB path too: themes' `time_bucket` now runs on Tiger, so add a test or a recorded check that it works there.
11. `docs/img/banner.png` (1280×400) and `docs/img/devto-cover.png` (1000×420) from `mockups/10-readme-banner.html` and `11-devto-cover.html`, filled with **one real, verified post** from the live run (title, community, quote; no username, no email address).
12. Final screenshots of every screen with the real data into `docs/img/` (replace the phase-1 review set where it makes sense; keep `docs/img/review/` as the demo record). No secrets, no email addresses visible.
13. README, RUNBOOK and `docs/devto-post.md`: fill in real numbers from the live run (posts found, labels, themes, searches used, seconds per post) where they are true; keep `TODO(planner)` for the video, the agent session and the whole "My First User" section. BUILD_LOG gets a phase-2 section with every live search, email and run.

## Acceptance

1. `uv run ruff check`, `uv run ruff format --check`, `uv run pytest` pass, with new tests for 1–5.
2. A fresh `overheard start` (stop everything first, then start) comes up without errors, and its output is readable.
3. Supabase and Obsidian show real posts with verified quotes, themes and runs; the digests reached the owner (they confirmed in this window).
4. SerpApi searches in this phase ≤ 12, emails ≤ 3, both logged.
5. `bash .git/info/leak-check.sh` says clean; no email address appears anywhere in the repo or screenshots.

## Stop line

When everything above is done and checked, print exactly `DONE-overheard-phase2` on its own line and stop. No push, publishing or session submission.

## Answers

(The planner adds answers to your questions here.)
