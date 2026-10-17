# Overheard: product spec (v0.5, 2026-10-17)

> Mockups: `mockups/index.html` (open in a browser; v4, dark). How it's built: `architecture.md`.
>
> Examples everywhere use made-up sample products: **Mealwise** (plans a week of dinners from what's already in your fridge), **Shipnote** (turns merged pull requests into release notes customers read) and **Plantpal** (paused). Competitor names in samples (PlateUp) are made up too.

## 1. One line

Every morning, Overheard tells you what people said on Reddit and Hacker News about the problems each of your products solves. It runs on your laptop with an open-weight model and emails one short digest per product.

## 2. Why someone pays for this today

- **GummySearch** sold exactly this (Reddit audience research: pain points, "is there a tool for…" requests). About $96k revenue in 2023 and ~400 paying customers at its peak (GetLatka estimate). It [closed to new users on 2025-11-30 and deletes all user data on 2026-12-01](https://gummysearch.com/docs/articles/gummysearch-is-now-closed-6533h) because it couldn't get a commercial licence for Reddit's Data API.
- Paid successors are still selling: Syften $29.95–119.95/mo, PainOnSocial $19–49/mo (~$1.3k MRR on TrustMRR), Leadverse (~$4.0k MRR) and Replymer (~$4.1k MRR) on TrustMRR, ReplyGuy $49+/mo.
- Best open-source alternatives found on 2026-10-17: openmagpie (69★, CLI only) and redsignal (29★). The post claims "no maintained self-hostable version we could find", never "nobody has open-sourced it".

## 3. Why open matters here (the post's argument)

- **Cost per post is zero.** Labelling a few hundred posts a day through a paid API costs money every morning; a 4B model on a laptop costs nothing.
- **Your research stays yours.** Which products you watch and which competitors you track is strategy. With a local model, no AI company sees it.
- **No platform can switch you off.** GummySearch died because it rented its data access. Overheard uses only open or paid-per-search sources you control, and the code is yours to fork.
- **Any model.** Swap Gemma for any open-weight model; the repo ships an eval so you can see which one labels best on your laptop.

## 4. Who uses it

A solo maker or small team with one to five products (side projects, an OSS library, a SaaS). Single user, runs on localhost, no sign-in.

## 5. What it does (the morning loop)

At the morning time (default 07:00, local), one Temporal workflow runs every active product:

1. **Plan searches.** One Reddit search (one SerpApi credit) per phrase per morning; Hacker News is free for every phrase. Overheard reads the plan size and searches left from SerpApi's free `account.json` endpoint and checks that phrases × days left in the month fits, keeping a 10 % reserve for Run now and first checks. If it doesn't fit, phrases take turns (least recently searched first) and each product shows how often its phrases are checked. There is no budget setting; `SERPAPI_DAILY_CAP` / `SERPAPI_MONTHLY_CAP` stay as optional hard caps in `.env`.
2. **Search.**
   - Reddit through Google: SerpApi `engine=google`, `q=site:reddit.com "<phrase>"` (or `site:reddit.com/r/<sub>`), limited to the past day or week. Gives title, snippet, URL, date.
   - Hacker News: Algolia `search_by_date` for stories and comments newer than the last run. Gives full text.
3. **Save.** Upsert by (product, URL); only new posts move on.
4. **Label.** The chat model labels each new post with one of five labels and must return the exact sentence that justifies it. Code checks the quote is really in the post. If it isn't, the model is asked once more; a second failure marks the post "unverified" (hidden with Noise).
   - Pain point · Asking for a tool · Mentions you · Competitor (names the competitor) · Noise
5. **Embed and theme.** Each post gets an embedding (stored in Postgres with pgvector). A post joins the nearest theme if it's similar enough, otherwise it starts a new theme that the model names. Weekly counts mark themes new, growing or steady.
6. **Digest.** One HTML email per product, to that product's recipients, through the user's own SMTP mailbox. Quiet mornings send a one-line "nothing new" email only if the product asks for it. No SMTP configured means the digest stays in the app.

"Run now" starts the same workflow by hand. A morning missed because the laptop was closed runs when it opens again (Temporal schedule catch-up window, 12 h).

## 6. Screens and look (mockups v4)

Products are tabs in the top bar; there is no sidebar. Each screen answers one question and has one primary button. Dashed yellow `agent-note` blocks in the mockups are build notes, never UI.

| Screen | Mockup | What it's for |
|---|---|---|
| Setup | `01-setup.html` | Three steps: model (picked from what Ollama has), morning email (test send, or skip), first product. One "all systems ready" badge; on failure it becomes a short list of only the failing checks with the fix. |
| Morning brief | `02-brief.html` | One per product. Headline sentence, one line under it, primary button "Go through all N". Sections: Worth a reply, Pain points (under their themes), Mentions. Post titles link to the thread. One status line at the end. |
| Review mode | `03-review.html` | One post at a time, reply draft inline, Skip / Done as big buttons with their key printed inside (S, D, C copy, O open, ? list). The only screen with shortcuts. |
| Add/edit product | `04-add-product.html` | Name, what it does, suggested phrases with a live "N phrases · N searches a day" badge, morning email. Competitors, subreddits, sources and quiet mornings under "More options" as option groups. |
| Themes | `05-themes.html` | Four weeks of themes sorted by growth, with quotes. |
| Run log | `06-runs.html` | Recent mornings and one run's steps; Run now; link to the Temporal UI. |
| Settings | `07-settings.html` | Products, morning time (themed picker), the Reddit cost as an equation and meter (read-only), email test, models as option cards (comparison folded), export and delete. |
| Morning email | `08-email.html` | Dark like the app; same sections as the brief; "Open the brief" button; optional quiet-morning email. |
| Logo | `09-brand.html` | The mark and wordmark. |

**Look.** Dark only: [Catppuccin](https://github.com/catppuccin/catppuccin) Mocha (MIT; credit it in the README and the stylesheet). Hacktoberfest-style edges: square corners, 2 px borders, hard offset shadows (no blur), section blocks with shadows tinted by label (blue asking, red pain, yellow mentions), a faint dot grid behind. Type: IBM Plex Sans for reading, JetBrains Mono for the wordmark, labels, sources, numbers and keys (both SIL OFL), served from font files in the app (no calls to Google at runtime).

**Interaction rules.**
- Navigation is a link: mauve, underlined. `→` stays in the app, `↗` opens the thread in a new tab. Post titles are links.
- Actions are buttons: bright edge, hard shadow, they press in on click. The primary button is mauve.
- Plain text is never mauve or underlined.
- Every choice (times, options, models) fills with mauve when picked. No native `<select>` or time input: the time is a themed grid of presets with a ±5 min stepper, opened from the ◷ button in the top bar or Settings.
- No eyebrows above titles; at most one short line under a title. Explanations that only matter to the builder go in build notes, not the UI.
- Done, Skip, Copy and similar show a toast with Undo.

**Logo.** A closing quote mark in a mauve square with a hard blue offset shadow; wordmark `overheard` in JetBrains Mono Bold. Favicon is the mark; the tab title carries the count, e.g. `(7) overheard`.

**Top bar.** Logo, numbered product tabs (active tab filled mauve) each with a ▾ menu (edit, check now with its search cost, pause, delete), "＋ Add", the "◷ Daily at 07:00" button (popover: time grid, last run, Run now with its cost, pause all), Settings.

## 7. Data (Postgres, schema `overheard`)

- `products` (id, name, description, phrases[], competitors[], subreddits[], sources, recipients[], quiet_day_email, status active/paused, created_at)
- `posts` (id, product_id, source reddit/hn, url, title, body_or_snippet, community, posted_at, found_at, label, quote, competitor, status new/done/skipped, snoozed_until, theme_id, search tsvector) with unique (product_id, url); embeddings in `post_embeddings` (post, model, dimension, vector) so the embedding model can change (architecture §6)
- `themes` (id, product_id, name, centroid vector, first_seen, post_count)
- `runs` and `run_steps` (workflow id, product, step, result, searches used, timings), `search_ledger` (date, product, phrase, idempotency key) for the budget
- `settings` (morning time, timezone, chosen models, paused_all)
- Trends use `time_bucket()` (TimescaleDB) over `posts.found_at`. Hybrid search merges full-text and vector rank (reciprocal rank fusion). A plain Postgres with pgvector works for self-hosters; TimescaleDB is optional.

## 8. Models are pluggable

- `LLM_BASE_URL` points at Ollama (default `http://127.0.0.1:11434`) or any OpenAI-compatible server (LM Studio, llama.cpp, vLLM). `CHAT_MODEL` and `EMBED_MODEL` are the defaults until the user picks in Settings.
- Overheard detects what is installed, which models are chat or embedding models, their embedding size, and which are loaded right now (architecture §6). `ollama pull <model>` makes a model appear in Settings within 10 seconds.
- Changing the embedding model re-embeds stored posts in a background Temporal workflow.
- Comparing models: `uv run overheard eval --model A --model B` on a checked set, and "Compare on my last 50 posts" in Settings on real posts without changing labels; a **Use** button switches in one click (architecture §7). Numbers only ever come from runs.
- Models are asked for by role (label, theme name, reply, phrases; embeddings), so a tuned model can take one role later (architecture §8).

## 9. Partner categories (genuine use only)

| Category | Prize | Use |
|---|---|---|
| Temporal | $100 | Schedule, one workflow per morning with a child per product, retries on flaky searches, resume after sleep without re-spending searches, budget stop as a non-retryable failure, re-embed workflow. |
| SerpApi | $100 | The only route to Reddit after Reddit closed self-serve API access; budget-aware. |
| Tiger Data | $100 | Posts, embeddings (pgvector), hybrid search, `time_bucket` trends, on the existing free Tiger service in a new schema. |
| Gemma | $200 | Default chat and embedding models, run locally. |
| Tinker | $200 | Stretch: fine-tune Qwen3.5-4B as a labeller (Tinker has no Gemma) and show its gain on held-out posts versus base models. |
| Sentry Agent Tracing | $100 | Stretch: trace each labelling and drafting call (latency, tokens per model). |

Not entered: Render, .tech and DigitalOcean (no hosting), ElevenLabs (790 characters left until 11 Nov), Copilot, Mastra, MongoDB, Backboard, Entire, TabPFN, Arduino.

## 10. Security and privacy

- Binds to 127.0.0.1. It refuses any other address unless `OVERHEARD_PASSWORD` is set, so a deployment is never open by accident (architecture §10).
- Configuration comes from environment variables (a `.env` file locally); only six are needed (architecture §9). Secrets never leave the environment; the UI shows whether each is set, never the value. Logs never print keys.
- Posts are untrusted input to the model: wrapped as data, labels come from a fixed enum, outputs are schema-validated, quotes are verified, the model gets no tools, and post text is HTML-escaped in the UI and email. A prompt-injection post is part of the test set.
- Stores only title, snippet or text, URL, community and date; no usernames.
- Nothing is ever posted anywhere. Drafts are copy-only and the prompt adds a "I built this" disclosure.

## 11. Honest limits (said in README and post)

- Reddit content is Google's title and snippet only, not full threads or comments, and Google indexes with some delay.
- The SerpApi free plan is 250 searches a month (about 8 a day); without a key, Overheard runs on Hacker News alone.
- Labels from a 4B model are good, not perfect; the eval shows how good.
- Inspired by GummySearch; not affiliated with it.

## 12. Non-goals

Hosting it for the hackathon (the repo is deploy-ready: Dockerfile + compose, architecture §10), multi-user accounts, Reddit's API, scraping Reddit pages, auto-posting, X/Twitter, mobile apps, paid AI APIs.

## 13. Demo and post

- Video: `uv run overheard start` → setup → add Mealwise (and Shipnote via seed) → Run now → the brief fills in → review mode → the Temporal UI showing a retried step → the morning email in the inbox → compare two models and switch with **Use**.
- First user: an indie maker runs it on their own product for a morning or two; the post reports what they said and whether they'd pay. Never invented.
- Title pattern from winners: a concrete number or scene, e.g. "GummySearch deletes everyone's data on December 1. I rebuilt the part I used, on a 4B model on my laptop."

## 14. Submission

- **Challenge:** https://dev.to/challenges/hacktoberfest-week2-2026-10-12 (Week 2, "Side Hustle"). Closes Sun 18 Oct 2026 11:59 PM PDT = **Mon 19 Oct 12:29 IST**. A new repo built inside the window; the first submission counts.
- **Post template** (DEV's submission link): tags `devchallenge, hf26challenge` (+ up to two more), then *This is a submission for the Hacktoberfest Open-Source AI Challenge Week 2: Side Hustle*, and the sections What I Built · Demo · Code (`{% github prateek11rai/overheard %}`) · How I Built It · Why Does Open Innovation Matter? · My Agent Session (`{% agent_session <id> %}`) · My First User · Prize Categories. Full text in architecture §13.
- **How earlier entries were shipped:** PaidYet (won Best Use of Temporal, https://dev.to/prateek11rai/paidyet-for-the-friend-who-says-kal-bhej-dunga-p5l) and Pagdandi (https://dev.to/prateek11rai/pagdandi-an-open-trek-database-that-gets-the-group-chat-onto-an-actual-trail-171d): agent drafts `docs/devto-post.md` → planner finishes it with real numbers and screenshots → owner records the video → the `mlh:1` agent session is submitted through DevRelay and published by the owner → the post goes up with the cover → the README links the post.
- **Assets:** README banner and DEV cover from `mockups/10-readme-banner.html` and `11-devto-cover.html`, filled with one real verified post (architecture §12).

## 15. Open decisions

1. First user.
2. Tinker stretch: yes or no.
3. Reuse the Pagdandi Tiger service (new schema `overheard`) and the same SerpApi and SMTP credentials in the new repo's `.env`.
4. Pull one or two more small models for the comparison (e.g. `qwen3:4b`, `llama3.2:3b`, about 2–3 GB each).
