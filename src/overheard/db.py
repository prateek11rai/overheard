import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg.rows import class_row, dict_row
from psycopg_pool import AsyncConnectionPool

from overheard.sources import Post as FoundPost

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
USEFUL_LABELS = ("ask", "pain", "you", "rival")
HIDDEN_LABELS = ("noise", "unverified")

PRODUCT_COLS = (
    "id, slug, name, description, phrases, competitors, subreddits, sources, recipients, "
    "quiet_day_email, status, demo, created_at"
)
POST_COLS = (
    "p.id, p.product_id, p.source, p.url, p.title, p.body, p.community, p.posted_at, p.found_at, "
    "p.label, p.quote, p.competitor, p.status, p.snoozed_until, p.theme_id, p.reply_draft, "
    "p.label_attempts"
)
OPEN_FILTER = (
    "p.label IN ('ask', 'pain', 'you', 'rival') "
    "AND p.found_at >= now() - interval '7 days' "
    "AND (p.status = 'new' OR (p.status = 'skipped' AND p.snoozed_until <= now()))"
)
LABEL_ORDER = "CASE p.label WHEN 'ask' THEN 0 WHEN 'pain' THEN 1 WHEN 'you' THEN 2 ELSE 3 END"


@dataclass
class Product:
    """A product Overheard listens for."""

    id: int
    slug: str
    name: str
    description: str
    phrases: list[str]
    competitors: list[str]
    subreddits: list[str]
    sources: str
    recipients: list[str]
    quiet_day_email: bool
    status: str
    demo: bool
    created_at: datetime

    @property
    def paused(self) -> bool:
        """Whether the product is paused."""
        return self.status == "paused"

    @property
    def reddit_on(self) -> bool:
        """Whether Reddit is one of the product's sources."""
        return self.sources == "reddit+hn"


@dataclass
class Post:
    """A post found for a product."""

    id: int
    product_id: int
    source: str
    url: str
    title: str
    body: str
    community: str
    posted_at: datetime | None
    found_at: datetime
    label: str | None
    quote: str | None
    competitor: str | None
    status: str
    snoozed_until: datetime | None
    theme_id: int | None
    reply_draft: str | None
    label_attempts: int


@dataclass
class Run:
    """One run of the morning job, a check, a re-embed or a comparison."""

    id: int
    workflow_id: str
    kind: str
    product_id: int | None
    status: str
    note: str
    reddit_planned: int
    searches_used: int
    new_posts: int
    demo: bool
    started_at: datetime
    finished_at: datetime | None
    product_name: str | None = None


@dataclass
class Step:
    """One step of a run, usually one Temporal activity."""

    id: int
    run_id: int
    product_id: int | None
    step_key: str
    text: str
    status: str
    seconds: float
    attempts: int
    created_at: datetime


@dataclass
class ThemeStat:
    """A theme with its post counts."""

    id: int
    name: str
    first_seen: datetime
    total: int
    posts_4w: int
    weekly: list[int] = field(default_factory=list)


def _vec(values: Sequence[float]) -> Vector:
    return Vector([float(x) for x in values])


def _floats(value: Any) -> list[float]:
    items = value.to_list() if hasattr(value, "to_list") else value
    return [float(x) for x in items]


def _id(row: dict | None) -> int:
    if row is None:
        raise RuntimeError("insert returned no row")
    return row["id"]


def index_name(model: str) -> str:
    """Name of the HNSW index for one embedding model.

    Args:
        model: The embedding model name.

    Returns:
        A stable, safe index name.
    """
    return "post_embeddings_" + hashlib.sha1(model.encode(), usedforsecurity=False).hexdigest()[:12]


class Database:
    """Postgres access for Overheard: one pool, every query, schema-scoped."""

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
        self.pool = AsyncConnectionPool(
            dsn,
            open=False,
            min_size=1,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            configure=self._configure,
        )
        self._timescale: bool | None = None

    async def _configure(self, conn: psycopg.AsyncConnection) -> None:
        await conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        await register_vector_async(conn)

    async def open(self) -> "Database":
        """Open the pool.

        Returns:
            This database, for chaining.
        """
        await self.pool.open(wait=True, timeout=15)
        return self

    async def close(self) -> None:
        """Close the pool."""
        await self.pool.close()

    async def __aenter__(self) -> "Database":
        """Open the pool for an `async with` block."""
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        """Close the pool at the end of an `async with` block."""
        await self.close()

    async def _all(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall()

    async def _one(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchone()

    async def _exec(self, query: str | sql.Composed, params: Sequence[Any] = ()) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return cur.rowcount

    async def _rows(self, cls: type, query: str, params: Sequence[Any] = ()) -> list:
        async with self.pool.connection() as conn, conn.cursor(row_factory=class_row(cls)) as cur:
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
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
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

    # products

    async def products(self) -> list[Product]:
        """All products in tab order (real first, then demo; oldest first).

        Returns:
            The products.
        """
        return await self._rows(
            Product, f"SELECT {PRODUCT_COLS} FROM products ORDER BY demo, created_at, id"
        )

    async def product(self, product_id: int) -> Product | None:
        """One product by id.

        Args:
            product_id: Product id.

        Returns:
            The product, or None.
        """
        rows = await self._rows(
            Product, f"SELECT {PRODUCT_COLS} FROM products WHERE id = %s", (product_id,)
        )
        return rows[0] if rows else None

    async def product_by_slug(self, slug: str) -> Product | None:
        """One product by slug.

        Args:
            slug: URL slug.

        Returns:
            The product, or None.
        """
        rows = await self._rows(
            Product, f"SELECT {PRODUCT_COLS} FROM products WHERE slug = %s", (slug,)
        )
        return rows[0] if rows else None

    async def free_slug(self, base: str) -> str:
        """A slug not used by any product, built from `base`.

        Args:
            base: The preferred slug.

        Returns:
            `base`, or `base-2`, `base-3`, …
        """
        taken = {r["slug"] for r in await self._all("SELECT slug FROM products")}
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        return slug

    async def create_product(self, **fields: Any) -> Product:
        """Insert a product.

        Args:
            **fields: Column values; `slug` and `name` are required.

        Returns:
            The new product.
        """
        cols = list(fields)
        query = sql.SQL("INSERT INTO products ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )
        row = await self._one(query, [fields[c] for c in cols])
        product = await self.product(row["id"])
        if product is None:
            raise RuntimeError("product insert returned nothing")
        return product

    async def update_product(self, product_id: int, **fields: Any) -> Product | None:
        """Update some columns of a product.

        Args:
            product_id: Product id.
            **fields: Column values to change.

        Returns:
            The updated product, or None if it doesn't exist.
        """
        if fields:
            query = sql.SQL("UPDATE products SET {} WHERE id = {}").format(
                sql.SQL(", ").join(
                    sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in fields
                ),
                sql.Placeholder(),
            )
            await self._exec(query, [*fields.values(), product_id])
        return await self.product(product_id)

    async def delete_product(self, product_id: int) -> bool:
        """Delete a product and everything that hangs off it.

        Args:
            product_id: Product id.

        Returns:
            Whether a product was deleted.
        """
        return await self._exec("DELETE FROM products WHERE id = %s", (product_id,)) > 0

    async def has_demo(self) -> bool:
        """Whether any demo product exists."""
        return await self._one("SELECT 1 AS x FROM products WHERE demo LIMIT 1") is not None

    async def delete_demo(self) -> int:
        """Delete demo products, their posts, themes and runs, and demo runs.

        Returns:
            Number of demo products deleted.
        """
        await self._exec("DELETE FROM runs WHERE demo")
        return await self._exec("DELETE FROM products WHERE demo")

    async def delete_everything(self) -> None:
        """Delete every product, post, theme, run, ledger row and eval result."""
        await self._exec(
            "TRUNCATE products, posts, post_embeddings, themes, runs, run_steps, search_ledger, "
            "eval_results RESTART IDENTITY CASCADE"
        )

    # posts

    async def upsert_posts(
        self, product_id: int, posts: Iterable[FoundPost], run_id: int | None
    ) -> tuple[int, int]:
        """Save posts by (product, URL); existing ones are left alone.

        Args:
            product_id: Product id.
            posts: Posts from the sources.
            run_id: The run that found them.

        Returns:
            (new posts saved, posts seen).
        """
        new = seen = 0
        async with self.pool.connection() as conn:
            for p in posts:
                seen += 1
                cur = await conn.execute(
                    "INSERT INTO posts (product_id, source, url, title, body, community, posted_at,"
                    " run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (product_id, url) DO NOTHING RETURNING id",
                    (
                        product_id,
                        p.source,
                        p.url,
                        p.title,
                        p.body,
                        p.community,
                        p.posted_at,
                        run_id,
                    ),
                )
                if await cur.fetchone():
                    new += 1
        return new, seen

    async def post(self, post_id: int) -> Post | None:
        """One post by id.

        Args:
            post_id: Post id.

        Returns:
            The post, or None.
        """
        rows = await self._rows(
            Post, f"SELECT {POST_COLS} FROM posts p WHERE p.id = %s", (post_id,)
        )
        return rows[0] if rows else None

    async def unlabelled_posts(self, product_id: int, limit: int = 500) -> list[Post]:
        """Posts of a product that have no label yet.

        Args:
            product_id: Product id.
            limit: Most posts to return.

        Returns:
            Unlabelled posts, oldest first.
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p WHERE p.product_id = %s AND p.label IS NULL "
            "ORDER BY p.id LIMIT %s",
            (product_id, limit),
        )

    async def set_label(
        self,
        post_id: int,
        label: str,
        quote: str | None,
        competitor: str | None,
        model: str,
        attempts: int,
    ) -> None:
        """Store a post's label and its checked quote.

        Args:
            post_id: Post id.
            label: One of ask, pain, you, rival, noise, unverified.
            quote: The sentence that justifies the label.
            competitor: The competitor named, for `rival`.
            model: The model that labelled it.
            attempts: How many tries it took.
        """
        await self._exec(
            "UPDATE posts SET label = %s, quote = %s, competitor = %s, label_model = %s, "
            "label_attempts = %s WHERE id = %s",
            (label, quote, competitor, model, attempts, post_id),
        )

    async def set_reply_draft(self, post_id: int, draft: str) -> None:
        """Store the reply draft for a post.

        Args:
            post_id: Post id.
            draft: The draft text.
        """
        await self._exec("UPDATE posts SET reply_draft = %s WHERE id = %s", (draft, post_id))

    async def open_posts(self, product_id: int) -> list[Post]:
        """Posts in a product's brief: useful labels, not done, not snoozed, last 7 days.

        Args:
            product_id: Product id.

        Returns:
            Posts in brief order (asking, pain, you, competitor; newest first).
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p WHERE p.product_id = %s AND {OPEN_FILTER} "
            f"ORDER BY {LABEL_ORDER}, p.found_at DESC, p.id",
            (product_id,),
        )

    async def hidden_posts(self, product_id: int) -> list[Post]:
        """Off-topic and unverified posts from the last 7 days.

        Args:
            product_id: Product id.

        Returns:
            Hidden posts, newest first.
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p WHERE p.product_id = %s "
            "AND p.label IN ('noise', 'unverified') AND p.found_at >= now() - interval '7 days' "
            "ORDER BY p.found_at DESC, p.id",
            (product_id,),
        )

    async def open_counts(self) -> dict[int, int]:
        """Number of posts in each product's brief.

        Returns:
            Product id to count (products with none are missing).
        """
        rows = await self._all(
            f"SELECT p.product_id, count(*) AS n FROM posts p WHERE {OPEN_FILTER} "
            "GROUP BY p.product_id"
        )
        return {r["product_id"]: r["n"] for r in rows}

    async def new_since(self, product_id: int, hours: int = 24) -> int:
        """Posts with a useful label found in the last few hours.

        Args:
            product_id: Product id.
            hours: Window size.

        Returns:
            The count.
        """
        row = await self._one(
            "SELECT count(*) AS n FROM posts WHERE product_id = %s AND found_at >= now() - "
            "make_interval(hours => %s) AND label IN ('ask', 'pain', 'you', 'rival')",
            (product_id, hours),
        )
        return row["n"] if row else 0

    async def set_post_status(
        self, post_id: int, status: str, snoozed_until: datetime | None = None
    ) -> Post | None:
        """Mark a post new, done or skipped.

        Args:
            post_id: Post id.
            status: new, done or skipped.
            snoozed_until: For skipped posts, when they come back.

        Returns:
            The updated post, or None.
        """
        await self._exec(
            "UPDATE posts SET status = %s, snoozed_until = %s WHERE id = %s",
            (status, snoozed_until, post_id),
        )
        return await self.post(post_id)

    async def recent_labelled(self, limit: int = 50) -> list[Post]:
        """The latest labelled posts of real (not demo) products.

        Args:
            limit: How many.

        Returns:
            Posts, newest first.
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p JOIN products pr ON pr.id = p.product_id "
            "WHERE NOT pr.demo AND p.label IS NOT NULL AND p.label <> 'unverified' "
            "ORDER BY p.found_at DESC, p.id DESC LIMIT %s",
            (limit,),
        )

    async def export_rows(self) -> list[dict]:
        """Every post with its product and theme, for CSV export.

        Returns:
            Rows as dicts.
        """
        return await self._all(
            "SELECT pr.name AS product, p.source, p.url, p.title, p.community, p.posted_at, "
            "p.found_at, p.label, p.quote, p.competitor, p.status, t.name AS theme "
            "FROM posts p JOIN products pr ON pr.id = p.product_id "
            "LEFT JOIN themes t ON t.id = p.theme_id ORDER BY p.found_at DESC, p.id"
        )

    # embeddings and themes

    async def ensure_embedding_index(self, model: str, dim: int) -> None:
        """Create the HNSW index for one embedding model if it is missing.

        Args:
            model: Embedding model name.
            dim: Its vector size.
        """
        query = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON post_embeddings USING hnsw "
            "((embedding::vector({})) vector_cosine_ops) WHERE model = {}"
        ).format(sql.Identifier(index_name(model)), sql.Literal(int(dim)), sql.Literal(model))
        await self._exec(query)

    async def posts_without_embedding(
        self, model: str, product_id: int | None = None, limit: int = 64
    ) -> list[Post]:
        """Labelled, useful posts of real products that have no vector for `model`.

        Args:
            model: Embedding model name.
            product_id: Only this product, when given.
            limit: Batch size.

        Returns:
            Posts to embed.
        """
        where = "AND p.product_id = %s" if product_id is not None else ""
        params: list[Any] = [model]
        if product_id is not None:
            params.append(product_id)
        params.append(limit)
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p JOIN products pr ON pr.id = p.product_id "
            "WHERE NOT pr.demo AND p.label IN ('ask', 'pain', 'you', 'rival') "
            "AND NOT EXISTS (SELECT 1 FROM post_embeddings e WHERE e.post_id = p.id "
            f"AND e.model = %s) {where} ORDER BY p.id LIMIT %s",
            params,
        )

    async def save_embedding(self, post_id: int, model: str, vector: Sequence[float]) -> None:
        """Store one post's vector for one model.

        Args:
            post_id: Post id.
            model: Embedding model name.
            vector: The embedding.
        """
        await self._exec(
            "INSERT INTO post_embeddings (post_id, model, dim, embedding) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (post_id, model) "
            "DO UPDATE SET embedding = EXCLUDED.embedding, dim = EXCLUDED.dim",
            (post_id, model, len(vector), _vec(vector)),
        )

    async def embedding(self, post_id: int, model: str) -> list[float] | None:
        """One post's vector for one model.

        Args:
            post_id: Post id.
            model: Embedding model name.

        Returns:
            The vector, or None.
        """
        row = await self._one(
            "SELECT embedding FROM post_embeddings WHERE post_id = %s AND model = %s",
            (post_id, model),
        )
        return None if row is None else _floats(row["embedding"])

    async def themes_for(self, product_id: int, model: str) -> list[dict]:
        """Themes of a product that have a centroid for `model`.

        Args:
            product_id: Product id.
            model: Embedding model name.

        Returns:
            Dicts with id, name, centroid (list of floats) and post_count.
        """
        rows = await self._all(
            "SELECT id, name, centroid, post_count FROM themes "
            "WHERE product_id = %s AND embed_model = %s AND centroid IS NOT NULL",
            (product_id, model),
        )
        for r in rows:
            r["centroid"] = _floats(r["centroid"])
        return rows

    async def unthemed_pain_posts(self, product_id: int, model: str) -> list[Post]:
        """Pain posts with a vector for `model` and no theme yet.

        Args:
            product_id: Product id.
            model: Embedding model name.

        Returns:
            Posts, oldest first.
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p JOIN post_embeddings e ON e.post_id = p.id "
            "AND e.model = %s WHERE p.product_id = %s AND p.label = 'pain' AND p.theme_id IS NULL "
            "ORDER BY p.id",
            (model, product_id),
        )

    async def create_theme(
        self, product_id: int, name: str, model: str, centroid: Sequence[float]
    ) -> int:
        """Start a theme.

        Args:
            product_id: Product id.
            name: Theme name.
            model: Embedding model of the centroid.
            centroid: The first vector.

        Returns:
            The theme id.
        """
        row = await self._one(
            "INSERT INTO themes (product_id, name, embed_model, centroid, post_count) "
            "VALUES (%s, %s, %s, %s, 0) RETURNING id",
            (product_id, name, model, _vec(centroid)),
        )
        return _id(row)

    async def add_to_theme(self, post_id: int, theme_id: int, centroid: Sequence[float]) -> None:
        """Put a post in a theme and store the theme's new centroid.

        Args:
            post_id: Post id.
            theme_id: Theme id.
            centroid: The theme's updated centroid.
        """
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE posts SET theme_id = %s WHERE id = %s", (theme_id, post_id))
            await conn.execute(
                "UPDATE themes SET centroid = %s, post_count = "
                "(SELECT count(*) FROM posts WHERE theme_id = %s) WHERE id = %s",
                (_vec(centroid), theme_id, theme_id),
            )

    async def recompute_centroids(self, model: str) -> int:
        """Rebuild every theme's centroid from its posts' vectors for `model`.

        Args:
            model: Embedding model name.

        Returns:
            Number of themes updated.
        """
        return await self._exec(
            "UPDATE themes t SET centroid = c.centroid, embed_model = %s FROM ("
            " SELECT p.theme_id, avg(e.embedding) AS centroid FROM posts p"
            " JOIN post_embeddings e ON e.post_id = p.id AND e.model = %s"
            " WHERE p.theme_id IS NOT NULL GROUP BY p.theme_id) c WHERE t.id = c.theme_id",
            (model, model),
        )

    async def theme_stats(self, product_id: int, weeks: int = 8) -> list[ThemeStat]:
        """Themes of a product with weekly post counts (week 0 is the last 7 days).

        Uses TimescaleDB's `time_bucket` when it is installed, else `date_bin`.

        Args:
            product_id: Product id.
            weeks: How many weeks of counts.

        Returns:
            Themes with `weekly` counts, newest week first.
        """
        bucket = (
            "time_bucket('7 days', p.found_at, origin => now())"
            if await self.has_timescale()
            else "date_bin('7 days', p.found_at, now())"
        )
        themes = await self._all(
            "SELECT t.id, t.name, t.first_seen, count(p.id) AS total, "
            "count(p.id) FILTER (WHERE p.found_at >= now() - interval '28 days') AS posts_4w "
            "FROM themes t LEFT JOIN posts p ON p.theme_id = t.id "
            "WHERE t.product_id = %s GROUP BY t.id ORDER BY t.id",
            (product_id,),
        )
        counts = await self._all(
            f"SELECT p.theme_id, round(extract(epoch FROM now() - {bucket}) / 604800)::int - 1 "
            "AS week, count(*) AS n FROM posts p JOIN themes t ON t.id = p.theme_id "
            "WHERE t.product_id = %s AND p.found_at >= now() - make_interval(days => %s) "
            "GROUP BY 1, 2",
            (product_id, weeks * 7),
        )
        out = []
        for t in themes:
            weekly = [0] * weeks
            for c in counts:
                if c["theme_id"] == t["id"] and 0 <= c["week"] < weeks:
                    weekly[c["week"]] += c["n"]
            out.append(
                ThemeStat(
                    id=t["id"],
                    name=t["name"],
                    first_seen=t["first_seen"],
                    total=t["total"],
                    posts_4w=t["posts_4w"],
                    weekly=weekly,
                )
            )
        return out

    async def theme_posts(self, theme_id: int, limit: int | None = None) -> list[Post]:
        """Posts in a theme, newest first.

        Args:
            theme_id: Theme id.
            limit: Most posts to return.

        Returns:
            The posts.
        """
        return await self._rows(
            Post,
            f"SELECT {POST_COLS} FROM posts p WHERE p.theme_id = %s "
            "ORDER BY p.found_at DESC, p.id LIMIT %s",
            (theme_id, limit),
        )

    async def theme(self, theme_id: int) -> dict | None:
        """One theme's id, product and name.

        Args:
            theme_id: Theme id.

        Returns:
            The theme as a dict, or None.
        """
        return await self._one(
            "SELECT id, product_id, name, first_seen FROM themes WHERE id = %s", (theme_id,)
        )

    # runs and steps

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
            "SELECT idempotency_key, result_count, results FROM search_ledger "
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
            "SELECT results FROM search_ledger WHERE run_key = %s AND product_id = %s ORDER BY id",
            (run_key, product_id),
        )
        return [item for r in rows for item in r["results"]]

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
