# Overheard v0.1.0

*The banner (`docs/img/banner.png`) is attached to this release as an asset by the planner.*

The first release: every morning, what people said on Reddit and Hacker News about the problems each of your products solves, labelled by an open-weight model on your own laptop.

## What it does

- Reads Reddit (through Google, via SerpApi) and Hacker News for the phrases that describe each of your products.
- Labels every post with a local model as *asking for a tool*, *pain point*, *mentions you*, *competitor* or *off-topic*. Each label carries a sentence quoted from the post, and code checks that the quote is really there before you see it; a post whose quote doesn't check out after one retry stays hidden as *unverified*.
- Groups pain points into themes with embeddings in pgvector, marked new or growing week over week.
- Sends one short email per product from your own mailbox, or writes it to a file. Reply drafts are copy-only, always say you built the product, and nothing is ever posted for you.
- Runs on a Temporal schedule (07:00 by default, 12-hour catch-up for a laptop that was asleep). A Reddit search is paid once a day per phrase, and a morning's email is sent once, even across retries and restarts.

How it fits together: [the architecture](img/diagrams/architecture.png) and [the morning run](img/diagrams/morning-run.png). Every setting and troubleshooting: [RUNBOOK](RUNBOOK.md).

## Quick start

Prerequisites (macOS with Homebrew): `brew install uv python@3.13 temporal ollama postgresql@17 pgvector`, then `ollama pull gemma4:e4b` and `ollama pull embeddinggemma:300m`.

```bash
git clone https://github.com/prateek11rai/overheard && cd overheard
cp .env.example .env          # set DATABASE_URL; SerpApi and SMTP are optional
uv sync
uv run overheard setup        # .data/, database tables, tool checks
uv run overheard doctor       # ✓/✗ for every piece
uv run overheard start        # Temporal + worker + web; open http://127.0.0.1:8000
```

Only `DATABASE_URL` is needed to start; `SERPAPI_API_KEY` turns on Reddit and `SMTP_*` turns on email. `uv run overheard demo load` adds three made-up products to look around with.

## What was tested live

On 18 October 2026, with two well-known products as the topics (Supabase and Obsidian, two phrases each), Tiger Cloud (Postgres 18.6, TimescaleDB 2.30.2, pgvector 0.8.7), `gemma4:e4b` and `embeddinggemma:300m` through Ollama, SerpApi, Hacker News and SMTP:

- First checks found 25 posts (14 from Reddit for 4 searches, 11 from Hacker News), and a later check one more. 25 of the 26 got a label whose quote the code found in the post; one stayed hidden as unverified. Labelling took about 2.9 seconds a post.
- A Run now reused the day's four Reddit searches from the ledger (0 spent) and emailed both briefs; the owner confirmed they arrived.
- The worker was killed 0.17 seconds after a paid search was saved. Temporal retried the activity on the restarted worker, the retry reused the saved result, and SerpApi's counter went up by exactly one.
- Weekly theme counts ran through TimescaleDB's `time_bucket` on Tiger and matched the plain-Postgres fallback; `overheard doctor` checks it.
- On the checked set of 90 posts (30 written for it, 60 Hacker News items fetched by id), `gemma4:e4b` got 76 right, 94% of its quotes checked out, it found all 8 "asking for a tool" posts, at 2.9 seconds a post. The labels are proposed, not yet checked by a person ([data/eval](../data/eval/README.md)).
- 191 tests pass, including workflow tests against a Temporal dev server.

The full log of every live search, email and run is in [BUILD_LOG](BUILD_LOG.md).

## Limits

- Reddit content is Google's title and snippet only, not full threads or comments, and Google indexes with some delay.
- SerpApi's free plan is 250 searches a month, about 8 a day. Without a key, Overheard runs on Hacker News alone.
- Labels from a 4B model are good, not perfect; run the eval on your own laptop to see how good.
- One morning of real posts is not enough for themes to mean much: the live run formed one, and it only loosely fit.
- Single user, on localhost. The web app refuses any address other than 127.0.0.1 unless `OVERHEARD_PASSWORD` is set.
- Inspired by GummySearch; not affiliated with it.

## Credits

- Colours: [Catppuccin](https://github.com/catppuccin/catppuccin) Mocha (MIT).
- Fonts: [IBM Plex Sans](https://github.com/IBM/plex) and [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) (SIL Open Font License), served from the app.
- [htmx](https://github.com/bigskysoftware/htmx) (0BSD), [Temporal](https://temporal.io), [Tiger Data](https://www.tigerdata.com), [SerpApi](https://serpapi.com), [Gemma](https://ai.google.dev/gemma) through [Ollama](https://ollama.com).

MIT licence: see [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
