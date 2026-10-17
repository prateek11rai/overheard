import json
from datetime import date
from typing import Any

from psycopg import sql

from overheard.db.core import DatabaseCore
from overheard.db.models import Run, Step, _id


class RunQueries(DatabaseCore):
    """Runs, steps, the search ledger and eval results."""

    async def upsert_run(
        self,
        workflow_id: str,
        kind: str,
        product_id: int | None = None,
        reddit_planned: int | None = None,
        demo: bool = False,
    ) -> int:
        """Create the run row for a workflow, or return the existing one.

        Args:
            workflow_id: Temporal workflow id.
            kind: morning, check, reembed or compare.
            product_id: For a check, the product.
            reddit_planned: Reddit searches planned, when known.
            demo: Whether this is demo data.

        Returns:
            The run id.
        """
        row = await self._one(
            "INSERT INTO runs (workflow_id, kind, product_id, reddit_planned, demo) "
            "VALUES (%s, %s, %s, coalesce(%s, 0), %s) ON CONFLICT (workflow_id) DO UPDATE SET "
            "reddit_planned = coalesce(%s, runs.reddit_planned) RETURNING id",
            (workflow_id, kind, product_id, reddit_planned, demo, reddit_planned),
        )
        return _id(row)

    async def finish_run(self, run_id: int, status: str, note: str = "") -> None:
        """Mark a run finished.

        Args:
            run_id: Run id.
            status: done, hn_only or failed.
            note: Short text shown next to the status.
        """
        await self._exec(
            "UPDATE runs r SET status = %s, note = %s, finished_at = now(), searches_used = "
            "(SELECT count(*) FROM search_ledger l WHERE l.run_key = r.workflow_id "
            "AND l.source = 'reddit'), new_posts = (SELECT count(*) FROM posts p "
            "WHERE p.run_id = r.id) WHERE id = %s",
            (status, note, run_id),
        )

    async def add_step(
        self,
        run_id: int,
        step_key: str,
        text: str,
        status: str = "ok",
        seconds: float = 0.0,
        attempts: int = 1,
        product_id: int | None = None,
    ) -> None:
        """Record or update one step of a run (idempotent per step key).

        Args:
            run_id: Run id.
            step_key: Unique key of the step within the run.
            text: What happened, in plain words.
            status: ok, retried, warn or failed.
            seconds: How long it took.
            attempts: Which attempt finished it.
            product_id: The product, when the step belongs to one.
        """
        await self._exec(
            "INSERT INTO run_steps (run_id, product_id, step_key, text, status, seconds, attempts)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (run_id, step_key) DO UPDATE SET "
            "text = EXCLUDED.text, status = EXCLUDED.status, seconds = EXCLUDED.seconds, "
            "attempts = EXCLUDED.attempts",
            (run_id, product_id, step_key, text, status, seconds, attempts),
        )

    def _runs_query(self, where: str) -> str:
        live_searches = (
            "(SELECT count(*) FROM search_ledger l WHERE l.run_key = r.workflow_id"
            " AND l.source = 'reddit')::int"
        )
        live_new = "(SELECT count(*) FROM posts p WHERE p.run_id = r.id)::int"
        return (
            "SELECT r.id, r.workflow_id, r.kind, r.product_id, r.status, r.note, r.reddit_planned,"
            f" CASE WHEN r.status = 'running' THEN {live_searches} ELSE r.searches_used END"
            " AS searches_used,"
            f" CASE WHEN r.status = 'running' THEN {live_new} ELSE r.new_posts END AS new_posts,"
            " r.demo, r.started_at, r.finished_at, pr.name AS product_name"
            f" FROM runs r LEFT JOIN products pr ON pr.id = r.product_id {where}"
        )

    async def runs(self, limit: int = 20) -> list[Run]:
        """Recent runs, newest first.

        Args:
            limit: How many.

        Returns:
            The runs.
        """
        return await self._rows(
            Run, self._runs_query("") + " ORDER BY r.started_at DESC, r.id DESC LIMIT %s", (limit,)
        )

    async def running_runs(self) -> list[Run]:
        """Runs whose row still says running.

        Returns:
            The runs, oldest first.
        """
        return await self._rows(
            Run, self._runs_query("WHERE r.status = 'running'") + " ORDER BY r.started_at"
        )

    async def run(self, run_id: int) -> Run | None:
        """One run by id.

        Args:
            run_id: Run id.

        Returns:
            The run, or None.
        """
        rows = await self._rows(Run, self._runs_query("WHERE r.id = %s"), (run_id,))
        return rows[0] if rows else None

    async def run_by_workflow(self, workflow_id: str) -> Run | None:
        """One run by its Temporal workflow id.

        Args:
            workflow_id: Workflow id.

        Returns:
            The run, or None.
        """
        rows = await self._rows(Run, self._runs_query("WHERE r.workflow_id = %s"), (workflow_id,))
        return rows[0] if rows else None

    async def last_run(self, kind: str = "morning") -> Run | None:
        """The latest run of one kind.

        Args:
            kind: Run kind.

        Returns:
            The run, or None.
        """
        rows = await self._rows(
            Run,
            self._runs_query("WHERE r.kind = %s") + " ORDER BY r.started_at DESC LIMIT 1",
            (kind,),
        )
        return rows[0] if rows else None

    async def steps(self, run_id: int) -> list[Step]:
        """Steps of a run in order.

        Args:
            run_id: Run id.

        Returns:
            The steps.
        """
        return await self._rows(
            Step,
            "SELECT id, run_id, product_id, step_key, text, status, seconds, attempts, created_at "
            "FROM run_steps WHERE run_id = %s ORDER BY created_at, id",
            (run_id,),
        )

    async def last_product_run(self, product_id: int) -> dict | None:
        """When a product was last checked and how many phrases were searched.

        Args:
            product_id: Product id.

        Returns:
            A dict with started_at and phrases, or None.
        """
        return await self._one(
            "SELECT r.started_at, (SELECT count(*) FROM search_ledger l WHERE l.run_key = "
            "r.workflow_id AND l.product_id = %s) AS phrases FROM runs r "
            "WHERE r.status <> 'running' AND (r.product_id = %s OR (r.kind = 'morning' AND "
            "EXISTS (SELECT 1 FROM run_steps s WHERE s.run_id = r.id AND s.product_id = %s))) "
            "ORDER BY r.started_at DESC LIMIT 1",
            (product_id, product_id, product_id),
        )

    # search ledger

    async def ledger_get(self, key: str) -> dict | None:
        """A saved search by its idempotency key.

        Args:
            key: The idempotency key.

        Returns:
            The ledger row, or None.
        """
        return await self._one(
            "SELECT idempotency_key, run_key, result_count, results FROM search_ledger "
            "WHERE idempotency_key = %s",
            (key,),
        )

    async def ledger_save(
        self,
        key: str,
        run_key: str,
        day: date,
        product_id: int | None,
        phrase: str,
        source: str,
        results: list[dict],
    ) -> bool:
        """Save a search and its results once.

        Args:
            key: Idempotency key.
            run_key: The run (workflow id).
            day: The day the search counts against.
            product_id: Product id.
            phrase: The phrase searched.
            source: reddit or hn.
            results: The posts found, as JSON-ready dicts.

        Returns:
            Whether a new row was written.
        """
        return (
            await self._exec(
                "INSERT INTO search_ledger (idempotency_key, run_key, day, product_id, phrase, "
                "source, result_count, results) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (idempotency_key) DO NOTHING",
                (key, run_key, day, product_id, phrase, source, len(results), json.dumps(results)),
            )
            > 0
        )

    async def ledger_results(self, run_key: str, product_id: int) -> list[dict]:
        """Every result saved for one run and product.

        Args:
            run_key: The run (workflow id).
            product_id: Product id.

        Returns:
            Result dicts from all of that run's searches for the product.
        """
        rows = await self._all(
            "SELECT l.results FROM search_ledger l JOIN search_uses u ON u.idempotency_key = "
            "l.idempotency_key WHERE u.run_key = %s AND l.product_id = %s ORDER BY l.id",
            (run_key, product_id),
        )
        return [item for r in rows for item in r["results"]]

    async def ledger_use(self, run_key: str, key: str) -> None:
        """Record that a run used a saved search (paid by it or by an earlier run).

        Args:
            run_key: The run (workflow id).
            key: The search's idempotency key.
        """
        await self._exec(
            "INSERT INTO search_uses (run_key, idempotency_key) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (run_key, key),
        )

    async def reddit_used(self, since: date) -> int:
        """Reddit searches spent since a day (inclusive).

        Args:
            since: First day to count.

        Returns:
            The count.
        """
        row = await self._one(
            "SELECT count(*) AS n FROM search_ledger WHERE source = 'reddit' AND day >= %s",
            (since,),
        )
        return row["n"] if row else 0

    async def last_searched(self) -> dict[tuple[int, str], date]:
        """When each (product, phrase) was last searched on Reddit.

        Returns:
            (product id, phrase) to the last day.
        """
        rows = await self._all(
            "SELECT product_id, phrase, max(day) AS day FROM search_ledger "
            "WHERE source = 'reddit' GROUP BY 1, 2"
        )
        return {(r["product_id"], r["phrase"]): r["day"] for r in rows}

    async def digest_get(self, key: str) -> dict | None:
        """A digest already sent or saved, by its idempotency key.

        Args:
            key: `run_key|product_id`.

        Returns:
            The ledger row, or None.
        """
        return await self._one(
            "SELECT idempotency_key, outcome, sent_at FROM digest_ledger "
            "WHERE idempotency_key = %s",
            (key,),
        )

    async def digest_save(self, key: str, run_key: str, product_id: int, outcome: str) -> bool:
        """Record that a digest was sent or saved.

        Args:
            key: `run_key|product_id`.
            run_key: The run (workflow id).
            product_id: Product id.
            outcome: What happened, for the Run log.

        Returns:
            Whether a new row was written.
        """
        return (
            await self._exec(
                "INSERT INTO digest_ledger (idempotency_key, run_key, product_id, outcome) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
                (key, run_key, product_id, outcome),
            )
            > 0
        )

    # evals

    async def save_eval(self, **fields: Any) -> int:
        """Store one model's comparison result.

        Args:
            **fields: Columns of `eval_results`.

        Returns:
            The row id.
        """
        if "details" in fields:
            fields["details"] = json.dumps(fields["details"])
        cols = list(fields)
        query = sql.SQL("INSERT INTO eval_results ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )
        row = await self._one(query, [fields[c] for c in cols])
        return _id(row)

    async def latest_evals(self, kind: str = "checked") -> list[dict]:
        """The newest result per model.

        Args:
            kind: checked (the eval set) or recent (last posts).

        Returns:
            Rows as dicts.
        """
        return await self._all(
            "SELECT DISTINCT ON (model) * FROM eval_results WHERE kind = %s "
            "ORDER BY model, created_at DESC",
            (kind,),
        )
