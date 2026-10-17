<!-- TODO(planner): banner docs/img/banner.png (1280×400, from docs/design/mockups/10-readme-banner.html with one real verified post), phase 2 -->

# overheard

Every morning, what people said about the problem your product solves.

<!-- TODO(planner): docs/img/brief.png (real run, phase 2). Review screenshot with demo data: docs/img/review/02-brief.png -->

Overheard reads Reddit (through Google, via SerpApi) and Hacker News for the phrases that describe each of your products, labels what it finds with an open-weight model on your own laptop, and sends you one short email per product: who asked for a tool like yours, which pains keep coming up, and who mentioned you or a competitor. Every label comes with a sentence quoted from the post, and the app checks that the quote is really there.

## Why

People pay for this today:

- **GummySearch** sold Reddit audience research (pain points, "is there a tool for…" requests): about $96k revenue in 2023 and roughly 400 paying customers at its peak (GetLatka estimate). It [closed to new users on 30 Nov 2025 and deletes all user data on 1 Dec 2026](https://gummysearch.com/docs/articles/gummysearch-is-now-closed-6533h), because it couldn't get a commercial licence for Reddit's Data API.
- Paid successors are still selling: Syften $29.95–119.95/mo, PainOnSocial $19–49/mo (~$1.3k MRR on TrustMRR), Leadverse (~$4.0k MRR) and Replymer (~$4.1k MRR) on TrustMRR, ReplyGuy $49+/mo.
- The open-source options we found (on 2026-10-17) are a CLI (openmagpie) and a small project (redsignal). We found no maintained, self-hostable app that does the whole morning loop.

Overheard is that app: open source, on your laptop, with any open-weight model. Your list of products and competitors never leaves your machine, and labelling a few hundred posts costs nothing.

## How it works

```
you ── http://127.0.0.1:8000 ──┐
                               ▼
   one Python project (uv): web (FastAPI + HTMX) and worker (Temporal)
        │                         │                      │
  Postgres + pgvector        Temporal server        outside world
  posts, labels, themes      schedule + history     SerpApi (Reddit via Google)
                                                     Hacker News API
                                                     Ollama (your model)
                                                     SMTP (your mailbox)
```

At the morning time (07:00 by default), one Temporal workflow runs each active product:

1. **Plan searches.** One Reddit search (one SerpApi credit) per phrase per morning; Hacker News is free. If your plan can't cover every phrase every day, phrases take turns.
2. **Search** Reddit through Google (title and snippet) and Hacker News (full text): the past day, or the past week on a new product's first check.
3. **Save** new posts only.
4. **Label** each post as *asking for a tool*, *pain point*, *mentions you*, *competitor* or *off-topic*. The model must return the exact sentence that justifies the label. Code checks that the quote is really in the post: at least three words, matched on word boundaries (ignoring case, spacing and quote styles), with `…` allowed for at most two left-out passages that sit close together, so a few words picked from all over a post never pass. A Hacker News comment must be quoted from the comment itself, not its thread's title. If the check fails, the model gets one more try, and after that the post is hidden as *unverified*. Post text goes to the model as data, the model gets no tools, and a *mentions you* label only stands if the quote names your product.
5. **Theme** pain points with embeddings in pgvector: a post joins the nearest theme or starts a new one, which the model names.
6. **Email** one digest per product from your own mailbox.

A morning missed because the laptop was asleep runs when it wakes (12-hour catch-up). A search that fails is retried, and a retry never spends a second search.

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

`.env.example` has six settings; only `DATABASE_URL` is needed to start, the other five are optional:

```dotenv
DATABASE_URL=postgresql://user:password@host:5432/db?sslmode=require
SERPAPI_API_KEY=
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
```

Want to look around first? `uv run overheard demo load` adds three made-up products with posts, themes and runs. `uv run overheard demo clear` (or Settings › Remove demo data) removes them.

## Screens

<!-- TODO(planner): 2×2 grid from real runs (brief, review, themes, settings) and the email. Demo-data versions are in docs/img/review/. -->

| Brief | Review |
|---|---|
| ![Morning brief](docs/img/review/02-brief.png) | ![Review mode](docs/img/review/03-review.png) |
| **Themes** | **Settings** |
| ![Themes](docs/img/review/05-themes.png) | ![Settings](docs/img/review/07-settings.png) |

The morning email: ![Email](docs/img/review/08-email.png)

## Models

Overheard talks to Ollama, or to any OpenAI-compatible server (LM Studio, llama.cpp, vLLM), through `LLM_BASE_URL`. It finds what is installed, tells chat models from embedding models, and shows each as *in use*, *loaded*, *installed* or *unreachable* in Settings.

- **Add one:** `ollama pull qwen3:4b`. It shows up in Settings within 10 seconds; click it to use it.
- **Embeddings:** picking a new embedding model re-embeds your posts in the background.
- **Compare:** `uv run overheard eval --model gemma4:e4b --model qwen3:4b --markdown` labels the checked set (`data/eval/`) with each model and prints the table below. Settings › Compare models shows the same numbers with a **Use** button, and *Compare on my last 50 posts* tries another model on your real posts without changing any label.

First run on this laptop, 30 synthetic items whose labels are proposed but not yet checked by a person:

| | gemma4:e4b |
|---|---|
| right label | 28/30 |
| quote checks out | 100% |
| asking-for-a-tool recall | 100% |
| seconds per post | 2.1 |

<!-- TODO(planner): replace with the phase 2 eval (checked labels, Hacker News items by id, two or more models). -->

## Deploy

```bash
cp .env.example .env   # DATABASE_URL=postgresql://overheard:<POSTGRES_PASSWORD>@db:5432/overheard,
                       # TEMPORAL_ADDRESS=temporal:7233, LLM_BASE_URL=http://ollama:11434,
                       # OVERHEARD_PASSWORD=..., POSTGRES_PASSWORD=...
docker compose up -d
docker compose exec ollama ollama pull gemma4:e4b && docker compose exec ollama ollama pull embeddinggemma:300m
```

The web app listens on 127.0.0.1 by default and refuses any other address unless `OVERHEARD_PASSWORD` is set. Details, every setting and troubleshooting: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Honest limits

- Reddit content is Google's title and snippet only, not full threads or comments, and Google indexes with some delay.
- SerpApi's free plan is 250 searches a month (about 8 a day). Without a key, Overheard runs on Hacker News alone.
- Labels from a 4B model are good, not perfect; the eval shows how good on your laptop.
- Inspired by GummySearch; not affiliated with it.

## Built for Hacktoberfest 2026

<!-- TODO(planner): link to the DEV post. -->

Built for the [Hacktoberfest Open-Source AI Challenge, Week 2: Side Hustle](https://dev.to/challenges/hacktoberfest-week2-2026-10-12). Categories entered: Gemma, Temporal, SerpApi, Tiger Data. Overheard runs on any Postgres with pgvector and uses TimescaleDB's `time_bucket` when present (tested on Tiger in phase 2).

## After the deadline

Commits after 2026-10-19 06:59 UTC are listed here.

- None yet.

## Credits and licence

- Colours: [Catppuccin](https://github.com/catppuccin/catppuccin) Mocha (MIT).
- Fonts: [IBM Plex Sans](https://github.com/IBM/plex) and [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) (SIL Open Font License), served from the app.
- [htmx](https://github.com/bigskysoftware/htmx) (0BSD), [Temporal](https://temporal.io), [Tiger Data](https://www.tigerdata.com) (any Postgres with pgvector works; TimescaleDB `time_bucket` when present, tested in phase 2), [SerpApi](https://serpapi.com), [Gemma](https://ai.google.dev/gemma) through [Ollama](https://ollama.com).

MIT licence: see [LICENSE](LICENSE) and [NOTICE](NOTICE).
