# Ship · diagrams, product video, post draft

> Brief from the planner after `DONE-overheard-phase2`. Same rules as before (Google docstrings, no header comments, commit per step, ruff + pytest + leak check before each commit, never print `.env` or the `SMTP_USER` address, no push or publishing). Don't edit this file; answers get appended at the bottom.

**Budget for this phase: 0 SerpApi searches and 0 emails.** Run the worker with `EMAIL_MODE=file` in its process environment (not an `.env` edit) whenever you start the app here. Same-day Check now reuses the ledger; never submit the add-product form.

## 1. Polish
- When a label's verified quote comes from the post's **title**, highlight the title the same way the quote block highlights body text (brief, review mode, email), as the banner already does. Test it.

## 2. Images for GitHub
- `docs/img/social-preview.png`, **1280×640** (GitHub's social preview size), from the banner design (`mockups/10-readme-banner.html`) with the same real post; keep key content inside the middle 1100×560. The owner uploads it in the repo's settings.

## 3. Diagrams (UML, in our look)
Write each as an HTML page in `docs/design/diagrams/` using the app's palette, fonts and block edges, and render PNGs at 2× into `docs/img/diagrams/` with Playwright. Plain words, few boxes, readable on a phone.
1. `architecture.png`: UML component diagram. Browser · web (FastAPI + Jinja2 + HTMX) · worker · Temporal server · Postgres (Tiger: pgvector, TimescaleDB) · model server (Ollama: gemma4:e4b, embeddinggemma) · SerpApi (Reddit via Google) · Hacker News · SMTP.
2. `morning-run.png`: UML sequence diagram of one morning: schedule 07:00 → Temporal → MorningBrief → ProductScan per product → plan (SerpApi plan) → search_reddit per phrase (ledger check, retry) → search_hn → label (Ollama, verify_quote, heartbeat) → embed + themes (pgvector) → send_digest (claim, SMTP) → finish run.
3. `htmx-click.png`: UML sequence of a Done click: browser → HTMX POST → FastAPI → Postgres → Jinja renders → HTML + out-of-band pieces + HX-Trigger → app.js toast.
4. `label-check.png`: UML activity diagram of labelling: post as data → model returns label + quote → verify_quote → pass: show · fail: ask once more → still fails: unverified (hidden).
5. `data-model.png`: UML/ER class diagram: products, posts, post_embeddings, themes, runs, run_steps, search_ledger, digest_ledger, settings, with the keys that matter.
Use 1–2 of them in the README (architecture + morning run) and all in the DEV post.

## 4. Product video (everything except the email)
- Record with Playwright's `recordVideo` (create a context with `recordVideo` from the existing Playwright tool's code runner; no new installs) at a **1440×900** viewport, on the live data (Supabase and Obsidian). For the recording only, you may inject a small click indicator (a ring where the pointer clicks) into the page; don't add it to the app.
- Storyboard, about 90–120 s, unhurried:
  1. Supabase's morning brief, slow scroll.
  2. Go through all: review mode with D, S and C (then put the posts back with Undo so the data stays as it was).
  3. Themes.
  4. ◷ schedule popover (look, don't change), ▾ menu → Check now on Obsidian, Run log updating, then the Temporal UI (`http://127.0.0.1:8233`) with the runs and the crash-test retry.
  5. Settings: models (in use / loaded), Compare (the 76/90 row), the search plan line.
  6. Add product: type a name and a sentence, let the phrase suggestions appear, then leave the page **without saving**.
- With ffmpeg (Homebrew): convert to H.264 MP4 (yuv420p, 30 fps), add short title cards between scenes (IBM Plex Sans or JetBrains Mono from `static/fonts`), keep it **under 10 MB** (GitHub's limit for videos dropped into a README on a free plan). Save to `~/Downloads/overheard-demo-product.mp4` and `docs/demo/overheard-demo-product.mp4`.
- No email addresses, secrets, terminals or usernames on screen.
- Then print `WAITING-email-clip` and keep going with 5–6. When the owner gives you the path of their email recording in this window, merge: scale/pad to 1440×900, same fps, a title card before it, keep under 10 MB, save as `~/Downloads/overheard-demo.mp4` and `docs/demo/overheard-demo.mp4`, and commit.

## 5. DEV post draft
Rewrite `docs/devto-post.md` as a full draft in the DEV template (spec §14, architecture §13): front matter with tags `devchallenge, hf26challenge, opensource, python`; the submission line; What I Built (who pays today, with the sourced numbers from spec §2; GummySearch closing); Demo (`TODO(planner)` for the video link); Code (`{% github prateek11rai/overheard %}`); How I Built It (with the diagrams and 2–3 screenshots, using absolute `https://raw.githubusercontent.com/prateek11rai/overheard/main/...` image URLs); Why Does Open Innovation Matter?; the live-run numbers; honest limits (including that one pain point only loosely fit); My Agent Session (`TODO(planner)`); My First User (`TODO(planner)`); Prize Categories (Gemma, Temporal, SerpApi, Tiger Data, each with one concrete sentence). 1,400–2,200 words, plain and specific, no invented numbers or users. Offer three title options at the top in a comment.

## 6. Release notes
`docs/release-notes-v0.1.0.md`: banner image (absolute raw URL), what it does, quick start, what was tested live, limits, credits. The planner creates the GitHub release from it.

## Acceptance
- Tests, ruff and the leak check pass; 0 searches and 0 emails spent (show the ledger and `EMAIL_MODE` of the worker).
- The images and the video contain no email address or secret (check frames).
- Print `DONE-overheard-ship` when 1–6 are done, except the merge, which happens when the owner sends their clip.

## Answers

(The planner adds answers to your questions here.)

**A2 (owner's email clip) · blur, then merge.** The clip is on the Desktop: `~/Desktop/Screen Recording 2026-10-18 at 3.47.44 AM.mov` (find it with a glob; macOS puts a narrow no-break space before "AM"). 24.6 s, 1686×1018, Gmail in a browser. Don't copy the raw clip into the repo.
- **Nothing private may stay readable:** the browser bar and tabs (URLs carry message ids), the account avatar, Gmail's left sidebar (label names), every inbox row that isn't one of the three Overheard emails (senders, subjects, snippets, attachment chips), the sender/recipient line of an opened email (it shows the address), and any other email. The Overheard digest content itself (headline, sections, quotes, "Open the brief") stays sharp.
- **How:** crop to the message pane when an Overheard email is open (no sidebar, no browser chrome), and blur with ffmpeg (`boxblur` regions with `enable='between(t,a,b)'`) wherever the inbox list shows: keep only the three Overheard rows sharp, or cut those seconds if a clean blur isn't possible. Blur the "Overheard <address>" sender line in every frame it's visible.
- **Check:** make contact sheets at 1 frame a second of the result and look at every frame; nothing private readable, nothing shaped like an email address.
- **Then:** scale/pad to 1440×900, 30 fps, a title card "7 · The morning email" before it, append it to the product video, keep the whole file under 10 MB. Save `~/Downloads/overheard-demo-email.mp4` (the edited clip alone, for the owner to check), `~/Downloads/overheard-demo.mp4` and `docs/demo/overheard-demo.mp4`. Commit the merged video only.

**A1 (planner, seen on GitHub) · tildes render as strikethrough.** On github.com the README line "PainOnSocial $19–49/mo (~$1.3k MRR on TrustMRR), Leadverse (~$4.0k MRR)…" shows struck through, because GitHub reads a pair of single `~` as strikethrough. Write "about $1.3k" instead of `~$1.3k` everywhere (README, devto-post, release notes); grep for `~` followed by a digit or `$`.

**A0 (from the owner) · local paths inside the repo.** The README and every Markdown file in the repo reference repo files by **relative path** (`docs/img/banner.png`, `docs/img/diagrams/architecture.png`, `docs/demo/…`), never by a GitHub URL. Absolute `raw.githubusercontent.com` URLs are only for text that is published outside the repo: `docs/devto-post.md` (DEV can't see repo files). For the release notes, use relative links for docs and say the banner will be attached to the release as an asset by the planner. Fix anything already written that breaks this.

