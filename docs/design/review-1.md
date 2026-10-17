# Review 1 · fixes before the owner's browser review

> Follow-up brief from the planner after `DONE-overheard-phase1`. Same rules as `build-brief.md` (phase 1: local only, no SerpApi / Hacker News / Tiger / SMTP calls, no Temporal downloads, Google docstrings, no header comments, commit per fix, ruff + pytest + leak check before each commit). Don't edit this file; answers get appended at the bottom.

Two reviews ran: a read-only code review and the planner's click-through of every screen in Playwright. Fix everything below, in this order. Each code fix gets a test that fails before and passes after. Where a finding says "decide", the decision is already made here.

## High

1. **Blank `OVERHEARD_PASSWORD` disables auth** (`settings.py`, `web/__init__.py`, `web/security.py`, `cli.py`). Treat an empty or whitespace value as unset, require at least 12 characters when set, and refuse to start on any non-loopback host without a valid one. Test: blank value + `OVERHEARD_HOST=0.0.0.0` refuses to start; a short value is rejected; requests without credentials get 401 when set.
2. **The schedule is only created by `overheard start` or a time change.** Also sync it (idempotently) in `overheard setup` and when `overheard worker` starts, so a compose deploy gets its morning run. Test: worker start with no schedule creates it; a second start leaves it unchanged.
3. **Overlapping runs pay twice.** Decide: (a) at most one paid Reddit search per (day, product, phrase): change the search-ledger key from `run_key` to the day (in the schedule's timezone), so any run that day reuses the stored result; Hacker News stays live on every run. (b) Refuse Run now / Check now while a morning run for that product is running (toast "Already running"); disable the button while a run is in progress. Test: two runs on the same day spend each phrase once; a second Run now while running is refused.

## Medium

4. **Quote check accepts stitched fragments** (`pipeline/quotes.py`). Match on word boundaries; each fragment at least 3 words; fragments must appear in order and within 300 characters of each other; at most 3 fragments. Test: `"I...need...app"` against "I really do not need a new app" fails; a real ellipsis quote still passes; the highlight never starts mid-word.
5. **Hacker News comments verified against the parent story's title** (`sources.py`, `quotes.py`, `labels.py`). For comments, the story title is context only; quotes must come from the comment body. Test with a fixture.
6. **Runs stuck in "running"** (`workflows/runtime.py`, `workflows/__init__.py`, `activities.py`). Every workflow (MorningBrief, ProductScan, Reembed, CompareRecent) finishes its run row in `finally` (failed / cancelled with a reason); a failed `start_workflow` marks its row failed; the Run log and the ◷ chip treat a `running` row whose workflow Temporal no longer knows as failed. Test: a workflow that raises leaves the row `failed`; a failed start leaves no `running` row.
7. **Reserve and plan maths** (`pipeline/plan.py`, `web/context.py`). The 10 % reserve applies to scheduled runs only; Run now / Check now / first checks may use it. Settings, the add-product badge, the ▾ menu cost and the planner all call one shared function (same rounding, same timezone). Test the shared function at a month boundary.
8. **The email subline shows the previous run** (`pipeline/brief.py`, `activities.py`). Pass the current run into `brief_for` so Saturday's email says Saturday.
9. **Switching the embedding model duplicates themes** (`db/themes.py`, `actions.py`). Keep using the old model until Reembed has re-embedded every post and recomputed every theme centroid with the new model, then switch the setting as Reembed's last step. If Temporal is down, refuse the switch with a clear toast. Test: during a re-embed, new posts join existing themes.
10. **Heartbeats vs slow models** (`activities.py`, `ai.py`). Heartbeat from a background task while a chat call is running. A failure in `update_themes` must not stop `send_digest`; record it as a failed step and send the email with what there is. Test: a slow fake model doesn't time out the activity; a theming failure still sends the digest.
11. **Timezone can't be changed** (`web/context.py`, `runtime.py`). Add the timezone to the ◷ popover and Settings → Time as a themed list (the computer's zone first, then common zones; real radio inputs, no native select), honour `OVERHEARD_TIMEZONE` as the initial default, and update the schedule when it changes.

## Low

12. CSV export guard: escape cells starting with `= + - @ \t \r`; don't touch empty cells.
13. Digest ledger: claim a row (`sending`) before sending and mark it `sent` after, so two overlapping attempts can't both send; file-mode names include the run (`<date>-<run>-<slug>.html`).
14. `search_path`: set it in the connection options at connect time (`options=-c search_path=<schema>`) as well as the session `SET`; document in RUNBOOK that transaction-mode poolers aren't supported.
15. Stale UI and demo edges:
    - Check now on `/runs` adds its row.
    - Every action that changes products, phrases or pause state also refreshes the Settings search-plan block (planner saw "5 phrases" stay after Resume until a reload).
    - "Remove demo data" in the web also deletes the demo digest files.
    - The CLI only deletes digest files that belong to demo products (exact slugs), never real products' files.
    - The `demo-` slug prefix is reserved for demo products (real products can't take it).
    - `demo load` is all-or-nothing: if it fails, the old demo data is still there.
16. Product delete keeps its search-ledger rows (`product_id` set to null) so the SerpApi caps stay honest; "Delete everything" may reset them (say so in the confirm dialog).
17. Rules and infra:
    - Pin GitHub Actions to commit SHAs and compose images by digest (read-only lookups on GitHub and the registries are fine for this).
    - Split `web/context.py` and `workflows/activities.py` under ~500 lines.
    - Remove the header comment from `002_digest_ledger.sql`.
    - Add a compose volume for the app's data dir.
    - `.env.example` has six keys; fix the wording "five" → "six" in `docs/design/architecture.md` §9, `spec.md` and the README.
18. Docs:
    - The DEV post and README must not claim Tiger / `time_bucket` trends work until phase 2 runs them. Say "runs on any Postgres with pgvector; uses TimescaleDB `time_bucket` when present (tested in phase 2)".
    - Fix the RUNBOOK lines that #1–#3 make wrong.
    - Re-check the README's quote-check wording after #4–#5.

## From the planner's browser review

19. **Reddit off (no `SERPAPI_API_KEY`):** Settings shows the equation, a "Reddit off · Hacker News only…" line and a separate "Hacker News only" badge. With no key: one line "Reddit off · Hacker News only" and a short hint to add `SERPAPI_API_KEY` to `.env`; no equation. The add-product badge and the ▾ menu say "Hacker News only", not "0 searches". Count demo products in the plan only when there are no real products, and label it "demo".
20. **Themes page counts disagree:** "↑ 14 posts · 5 this week" next to "All 18 posts"; "4 posts" next to "All 5 posts". One window for both, or say "14 in 4 weeks · all 18".
21. **A product that has never been checked** says "Nothing new about X since yesterday." next to "Not checked yet". Say "Listening. The first check is running." and refresh the page every few seconds until the first run finishes; then show its real state.
22. **Review progress** counts what's left ("1 of 3" → "1 of 2" → "1 of 1"). Count progress through the session ("2 of 3") and fill the bar.
23. **Undo only where it reverses something.** No Undo on "Morning brief started", "Listening. First check started" or "Checking … now".
24. **Done is unrecoverable once the toast is gone.** Add "Show done (N)" to the brief's status line that lists today's done posts with Undo; toasts with Undo stay 8 s and pause on hover.
25. **Console error** `htmx:oobErrorNoTarget #timepick` when the time is changed from the ◷ popover on a page without the Settings picker. Only send oob fragments whose targets exist on the requesting page (HX-Current-URL). Zero console errors on every screen.
26. **Tab ▾ caret** renders as a tiny dim dot. Make it a visible ▾ (size and contrast as in the mockup) with an accessible label.
27. **Setup says "✓ all systems ready"** while Reddit and email are off. Say "✓ ready · Reddit off · email to file" so nothing looks on that isn't.
28. **"Send a test" with no SMTP** must say where the test went (the file path) or that email is off.

## Acceptance

1. `uv run ruff check`, `uv run ruff format --check`, `uv run pytest` pass, with the new tests for 1–16.
2. Re-walk every step-14 interaction plus items 19–28 in Playwright on demo data; each one changes the page and is still true after a reload; the browser console is empty on every screen. Update the checklist in `docs/BUILD_LOG.md` and refresh the affected screenshots in `docs/img/review/`.
3. `bash .git/info/leak-check.sh` says clean.

## Stop line

When everything above is done and checked, print exactly `DONE-overheard-review1` on its own line and stop.

## Answers

(The planner adds answers to your questions here.)
