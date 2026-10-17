import contextlib
import copy
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Self

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg.rows import class_row, dict_row
from psycopg_pool import AsyncConnectionPool

from overheard.db.models import MIGRATIONS_DIR


class DatabaseCore:
    """The pool, query helpers, migrations and app settings."""

    def __init__(self, dsn: str, schema: str = "overheard", max_size: int = 6) -> None:
        """Create the database helper (the pool opens on `open()`).

        Args:
            dsn: Postgres connection string.
            schema: Schema that holds every Overheard table.
            max_size: Largest number of pooled connections.
        """
        if not schema.isidentifier():
            raise ValueError("schema must be a plain identifier")
        self.dsn = dsn
        self.schema = schema
        # Session-level settings: transaction-mode poolers (e.g. PgBouncer) aren't supported.
        self.connect_kwargs = {"options": f"-c search_path={schema},public"}
        self.pool = AsyncConnectionPool(
            dsn,
            open=False,
            min_size=1,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row, **self.connect_kwargs},
            configure=self._configure,
        )
        self._timescale: bool | None = None
        self._conn: psycopg.AsyncConnection | None = None

    async def _configure(self, conn: psycopg.AsyncConnection) -> None:
        await conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        await register_vector_async(conn)

    async def open(self) -> Self:
        """Open the pool.

        Returns:
            This database, for chaining.
        """
        await self.pool.open(wait=True, timeout=15)
        return self

    async def close(self) -> None:
        """Close the pool."""
        await self.pool.close()

    async def __aenter__(self) -> Self:
        """Open the pool for an `async with` block."""
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        """Close the pool at the end of an `async with` block."""
        await self.close()

    @contextlib.asynccontextmanager
    async def _connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        if self._conn is not None:
            yield self._conn
        else:
            async with self.pool.connection() as conn:
                yield conn

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[Self]:
        """Run several queries as one transaction.

        Yields:
            A view of this database whose every query uses the same connection; it commits
            when the block ends and rolls back if it raises.
        """
        async with self.pool.connection() as conn, conn.transaction():
            tx = copy.copy(self)
            tx._conn = conn
            yield tx

    async def _all(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> list[dict]:
        async with self._connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    async def _one(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> dict | None:
        async with self._connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchone()

    async def _exec(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> int:
        async with self._connection() as conn:
            cur = await conn.execute(query, params)
            return cur.rowcount

    async def _rows(self, cls: type, query: str, params: Sequence[Any] = ()) -> list:
        async with self._connection() as conn, conn.cursor(row_factory=class_row(cls)) as cur:
            await cur.execute(query, params)
            return await cur.fetchall()

    # migrations and health

    async def migrate(self) -> list[str]:
        """Create the schema and apply new migrations, inside the schema only.

        Returns:
            Names of the migrations applied now (empty when up to date).

        Raises:
            RuntimeError: If the pgvector extension is missing and can't be created.
        """
        applied: list[str] = []
        async with await psycopg.AsyncConnection.connect(
            self.dsn, autocommit=True, **self.connect_kwargs
        ) as conn:
            cur = await conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            if await cur.fetchone() is None:
                try:
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except psycopg.Error as exc:
                    raise RuntimeError(
                        "pgvector is not enabled in this database and this user can't enable "
                        "it. Run CREATE EXTENSION vector as an admin."
                    ) from exc
            ident = sql.Identifier(self.schema)
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident))
            await conn.execute(sql.SQL("SET search_path TO {}, public").format(ident))
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                async with conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('overheard'))")
                    cur = await conn.execute(
                        "SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,)
                    )
                    if await cur.fetchone():
                        continue
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,)
                    )
                    applied.append(path.name)
        return applied

    async def health(self) -> dict[str, Any]:
        """Check the connection, pgvector and the schema.

        Returns:
            A dict with `ok`, `pgvector`, `schema` and `migrations` keys.
        """
        row = await self._one(
            "SELECT (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS pgvector, "
            "(SELECT count(*) FROM information_schema.tables WHERE table_schema = %s) AS tables",
            (self.schema,),
        )
        return {
            "ok": True,
            "pgvector": row["pgvector"] if row else None,
            "tables": row["tables"] if row else 0,
        }

    async def timescale(self) -> str | None:
        """TimescaleDB's version once a real `time_bucket` call has answered.

        Returns:
            The version, or None when TimescaleDB isn't installed.
        """
        row = await self._one("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        if row is None:
            return None
        await self._one("SELECT time_bucket('7 days', now(), origin => now()) AS week")
        return row["extversion"]

    async def has_timescale(self) -> bool:
        """Whether TimescaleDB is installed (for `time_bucket`)."""
        if self._timescale is None:
            row = await self._one("SELECT 1 AS x FROM pg_extension WHERE extname = 'timescaledb'")
            self._timescale = row is not None
        return self._timescale

    # settings

    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Read one app setting.

        Args:
            key: Setting name.
            default: Value when it was never set.

        Returns:
            The stored JSON value or the default.
        """
        row = await self._one("SELECT value FROM settings WHERE key = %s", (key,))
        return default if row is None else row["value"]

    async def set_setting(self, key: str, value: Any) -> None:
        """Store one app setting.

        Args:
            key: Setting name.
            value: Any JSON-serialisable value.
        """
        await self._exec(
            "INSERT INTO settings (key, value) VALUES (%s, %s::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, json.dumps(value)),
        )

    async def all_settings(self) -> dict[str, Any]:
        """Read every app setting.

        Returns:
            Setting name to value.
        """
        return {r["key"]: r["value"] for r in await self._all("SELECT key, value FROM settings")}
