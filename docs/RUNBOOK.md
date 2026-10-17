# Overheard runbook

How to run, configure, deploy and fix Overheard. What it does: [design/spec.md](design/spec.md). How it is built: [design/architecture.md](design/architecture.md).

## Run it on a laptop

Prerequisites (macOS, Homebrew):

```bash
brew install uv python@3.13 temporal ollama postgresql@17 pgvector
brew services start postgresql@17
createdb overheard && psql -d overheard -c 'CREATE EXTENSION vector'
ollama serve &                     # or the Ollama app
ollama pull gemma4:e4b && ollama pull embeddinggemma:300m
```

Then, in the repo:

```bash
cp .env.example .env               # DATABASE_URL=postgresql:///overheard for the local database
uv sync                            # Python 3.13 from the system; uv never downloads one
uv run overheard setup             # .data/, migrations (schema "overheard" only), tool checks
uv run overheard doctor            # ✓ Postgres, Ollama, Temporal, worker, schedule; SerpApi/SMTP "not set yet" is fine
uv run overheard start             # open http://127.0.0.1:8000; Ctrl-C stops everything
```

`start` checks the model server (and starts `ollama serve` if it is the default local Ollama and isn't running), starts Homebrew's Temporal dev server (`$(brew --prefix)/bin/temporal`, never another `temporal` on PATH, never a download) with its state in `.data/temporal.db` unless one already answers on `TEMPORAL_ADDRESS`, runs the worker and the web app with `[temporal] [worker] [web]` log prefixes, and creates or updates the `morning-brief` schedule.

### Every command

| Command | Does |
|---|---|
| `uv run overheard setup` | creates `.data/`, runs migrations, checks the model server and Temporal |
| `uv run overheard doctor` | ✓/✗ for every piece; never prints a secret |
| `uv run overheard start` | everything above, until Ctrl-C |
| `uv run overheard web` · `worker` | one piece (what a deployment runs) |
| `uv run overheard demo load` · `demo clear` | add or remove the demo data |
| `uv run overheard seed` | add Mealwise and Shipnote as real products with no posts |
| `uv run overheard brief [--reddit off]` | run the morning job now and wait (needs a running worker) |
| `uv run overheard eval --model A --model B [--markdown] [--synthetic-only]` | compare models on `data/eval/` |
| `uv run overheard email-test` | send a test email to `SMTP_USER` |
| `uv run pytest` · `uv run ruff check` · `uv run ruff format --check` | tests and lint |

Tests use the `overheard_test` database (`OVERHEARD_TEST_DATABASE_URL`, default `postgresql:///overheard_test`, with `CREATE EXTENSION vector`) and run the workflow tests on the Homebrew Temporal dev server (`$(brew --prefix)/bin/temporal`, or `OVERHEARD_TEMPORAL_BIN`). Nothing is downloaded: if the CLI is missing, the tests fail and say `brew install temporal`.

## Settings

Environment variables win over `.env`; `.env` is read only when present. Only `DATABASE_URL` is required.

| Variable | Default | Set it when |
|---|---|---|
| `DATABASE_URL` | none (required) | always: Postgres with pgvector (Tiger Cloud, a local Postgres, or the compose `db`) |
| `SERPAPI_API_KEY` | empty | you want Reddit (through Google). Without it: Hacker News only |
| `SERPAPI_DAILY_CAP` · `SERPAPI_MONTHLY_CAP` | none | you want hard limits on searches on top of the automatic plan |
| `SMTP_HOST` · `SMTP_PORT` · `SMTP_USER` · `SMTP_PASS` | empty · `587` · empty · empty | you want the morning email (port 587 uses STARTTLS, 465 uses SSL; Gmail needs an app password) |
| `EMAIL_FROM` | `SMTP_USER` | sending as another address |
| `EMAIL_MODE` | `smtp` if `SMTP_HOST` is set, else `off` | `file` writes digests to `.data/digests/` instead of sending |
| `LLM_BASE_URL` | `http://127.0.0.1:11434` | the model server is elsewhere or isn't Ollama (any OpenAI-compatible server) |
| `LLM_API_KEY` | empty | that server needs a key |
| `CHAT_MODEL` · `EMBED_MODEL` | `gemma4:e4b` · `embeddinggemma:300m` | you want other first-run defaults (Settings wins after you pick) |
| `TEMPORAL_ADDRESS` · `TEMPORAL_NAMESPACE` | `127.0.0.1:7233` · `default` | a real Temporal or Temporal Cloud |
| `TEMPORAL_API_KEY` | empty | Temporal Cloud (turns on TLS) |
| `TEMPORAL_UI_URL` | `http://127.0.0.1:8233` | the Run log's "Temporal UI" link |
| `OVERHEARD_HOST` · `OVERHEARD_PORT` | `127.0.0.1` · `8000` | deploying (any other host needs `OVERHEARD_PASSWORD`) |
| `OVERHEARD_PASSWORD` | empty | any non-local host: every page asks for it (HTTP Basic, any user name). At least 12 characters; an empty or blank value counts as unset, so a non-local host refuses to start. |
| `OVERHEARD_TIMEZONE` | the computer's | the first morning timezone (validated); after that it is picked in the app (◷ or Settings › Time) |
| `OVERHEARD_DB_SCHEMA` | `overheard` | sharing a database. Overheard sets `search_path` per connection (in the connection options and with `SET`), so connect directly or through a session-mode pooler; transaction-mode poolers (PgBouncer `pool_mode=transaction`, Supavisor transaction mode) aren't supported. |
| `OVERHEARD_DATA_DIR` | `.data` | moving local files (digests, Temporal state) |
| `OVERHEARD_TEMPORAL_BIN` | Homebrew's (`$(brew --prefix)/bin/temporal`) | the Temporal CLI lives elsewhere (`start` and the tests use it; nothing is ever downloaded) |
| `OVERHEARD_OFFLINE` | `false` | developing without network: no SerpApi or Hacker News calls (searches are skipped and say so in the Run log) |

The morning time and timezone, pause-all, products, recipients and model choices are set in the app and stored in the database (table `settings`), not in the environment. A product with no recipients sends its digest to your own mailbox (`SMTP_USER`); the app and its logs never show that address, and a failed send is recorded by error name only.

## Demo data

`uv run overheard demo load` adds three made-up products (Mealwise, Shipnote, Plantpal paused) with posts whose quotes pass the quote check, themes with eight weeks of counts, three runs with steps, and one digest per product in `.data/digests/`. Everything hangs off products flagged `demo`; demo slugs start with `demo-`, so real products can live next to them. The morning job never scans demo products. While any exist, the top bar shows `demo data`; Settings › Your data › Remove demo data (or `uv run overheard demo clear`) removes all of it. Loading again replaces the previous demo data.

## The morning job

- Schedule `morning-brief`: daily at the chosen time in the chosen timezone, skips a run if the previous one is still going, catches up to 12 hours late. `overheard setup`, every worker start and `overheard start` create it if it's missing and update it only if it differs (a compose deploy gets it from the worker). `temporal schedule describe --schedule-id morning-brief` shows it. The ◷ button and Settings › Time update it; Pause all pauses it.
- **Run now** starts `MorningBrief` (same as the schedule). **Check now** starts `ProductScan` for one product. The scheduled morning and Check now email the posts found in that run; Run now emails each product's whole open brief, as the app shows it. Both are refused ("Already running") while a morning run, or a check of that product, is still going; the buttons show disabled until it finishes. Runs that Temporal no longer runs are shown as failed. Changing the embedding model starts `Reembed`; *Compare on my last 50 posts* starts `CompareRecent`. All show in the Run log.
- Search budget: one Reddit search per phrase per morning if phrases × days left in the month fits the searches left minus a 10% reserve (from SerpApi's free `account.json`); otherwise phrases take turns, least recently searched first. A used-up plan is a non-retryable failure that switches that morning to Hacker News only.
- Never paying twice: a Reddit search is paid once per day (in the schedule's timezone), product and phrase; any run that day (scheduled, Run now, Check now, a retry, a restarted worker or an overlapping run) reuses the saved result. Hacker News is searched live on every run. The 10 % reserve is kept back only for the scheduled run.
- Never emailing twice: `send_digest` claims the (run, product) row in `digest_ledger` before sending and marks it sent after the mail server accepts it (or the file `<date>-<run>-<slug>.html` is written, dated by the morning in the schedule's timezone); a retry or an overlapping attempt doesn't send the same morning email again.

## Deploy (Docker Compose)

Not run during the hackathon; `.github/workflows/docker.yml` builds the image on every relevant change.

1. `.env` for compose:
   ```dotenv
   POSTGRES_PASSWORD=<a long random string>
   DATABASE_URL=postgresql://overheard:<the same>@db:5432/overheard
   TEMPORAL_ADDRESS=temporal:7233
   LLM_BASE_URL=http://ollama:11434
   OVERHEARD_PASSWORD=<another long random string>
   # optional: SERPAPI_API_KEY, SMTP_*
   ```
2. `docker compose up -d` starts Postgres + pgvector, the Temporal dev server, Ollama, a one-shot `setup` (migrations), the worker and the web app.
3. `docker compose exec ollama ollama pull gemma4:e4b` and `... ollama pull embeddinggemma:300m`.
4. Open http://127.0.0.1:8000 on the host (the port is published on 127.0.0.1 only). For a real server, swap `temporal` for Temporal Cloud and `db` for any managed Postgres with pgvector (Tiger is tested in phase 2), keep the port on localhost and put a private tunnel or an authenticating proxy in front.

The image runs as a non-root user and sets `OVERHEARD_HOST=0.0.0.0`, so the web app will not start without `OVERHEARD_PASSWORD`. `GET /healthz` answers without the password, for health checks.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor`: ✗ Postgres | Check `DATABASE_URL`; run `uv run overheard setup`. "pgvector is not enabled": `CREATE EXTENSION vector` as an admin. |
| `doctor`: ✗ Ollama | `ollama serve` (or the Ollama app); `ollama pull` the missing model. Another server: set `LLM_BASE_URL`. |
| `setup`/`start`: "Temporal CLI not found" | `brew install temporal`, or set `OVERHEARD_TEMPORAL_BIN`. Overheard never downloads Temporal. |
| `doctor`: ✗ Temporal or worker | `uv run overheard start`, or run `temporal server start-dev` and `uv run overheard worker` yourself. |
| A toast says "Temporal isn't running" | Same as above; the time you picked is saved and the schedule updates when Temporal starts. |
| Settings shows a model as *unreachable* | The model server is down, or the model was removed. |
| Run log: "Hacker News only · search plan used up" | SerpApi has no searches left this month; Reddit comes back next month or with a bigger plan. |
| Run log: "Reddit off · no SerpApi key" | Add `SERPAPI_API_KEY` to `.env` and restart. |
| No email | `EMAIL_MODE` is `off` or `file` (look in `.data/digests/`), or SMTP is wrong: `uv run overheard email-test`. Quiet mornings email only if the product asks. |
| The app refuses to start on `0.0.0.0` | Set `OVERHEARD_PASSWORD` (12 characters or more), or use `127.0.0.1`. |
| "Already running" | A morning run or a check is still going; wait for its row in the Run log to finish. |
| "Cross-origin request refused" | A button was pressed from another origin (a different host name or port). Open the app at the address it prints. |
| Start over | Settings › Your data › Delete everything (products, posts, themes, runs). To drop all tables: `DROP SCHEMA overheard CASCADE`, then `uv run overheard setup`. |
