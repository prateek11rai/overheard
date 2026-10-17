from collections.abc import Sequence
from typing import Any

from psycopg import sql

from overheard.db.core import DatabaseCore
from overheard.db.models import POST_COLS, Post, ThemeStat, _floats, _id, _vec, index_name


class ThemeQueries(DatabaseCore):
    """Embeddings, themes and search."""

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

    async def switch_embedding(self, model: str) -> int:
        """Rebuild theme centroids for `model` and make it the embedding model, atomically.

        Called as the re-embed's last step, so the morning job keeps using the old model's
        themes until every post has a vector for the new one.

        Args:
            model: The new embedding model.

        Returns:
            Number of themes updated.
        """
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE themes t SET centroid = c.centroid, embed_model = %s FROM ("
                " SELECT p.theme_id, avg(e.embedding) AS centroid FROM posts p"
                " JOIN post_embeddings e ON e.post_id = p.id AND e.model = %s"
                " WHERE p.theme_id IS NOT NULL GROUP BY p.theme_id) c WHERE t.id = c.theme_id",
                (model, model),
            )
            updated = cur.rowcount
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES ('models', jsonb_build_object('embed', "
                "%s::text)) ON CONFLICT (key) DO UPDATE SET value = settings.value || "
                "jsonb_build_object('embed', %s::text), updated_at = now()",
                (model, model),
            )
            await conn.execute("DELETE FROM settings WHERE key = 'pending_embed'")
        return updated

    async def hybrid_search(
        self,
        product_id: int,
        text: str,
        vector: Sequence[float] | None,
        model: str,
        limit: int = 20,
        k: int = 60,
    ) -> list[tuple[Post, float]]:
        """Search a product's posts by words and meaning (reciprocal rank fusion).

        Full-text rank (`websearch_to_tsquery` on title and body) and vector rank (cosine
        distance to `vector` for `model`) are merged with RRF: score = sum of 1 / (k + rank).

        Args:
            product_id: Product id.
            text: What to look for, in words.
            vector: Its embedding, or None for full-text only.
            model: Embedding model of `vector`.
            limit: Most posts to return.
            k: RRF constant.

        Returns:
            (post, score) pairs, best first.
        """
        rows = await self._all(
            "WITH q AS (SELECT websearch_to_tsquery('english', %s) AS q), "
            "ft AS (SELECT p.id, row_number() OVER (ORDER BY ts_rank(p.search, q.q) DESC) AS r "
            "  FROM posts p, q WHERE p.product_id = %s AND p.search @@ q.q ORDER BY r LIMIT 100), "
            "vec AS (SELECT p.id, row_number() OVER (ORDER BY e.embedding <=> %s::vector) AS r "
            "  FROM posts p JOIN post_embeddings e ON e.post_id = p.id AND e.model = %s "
            "  WHERE p.product_id = %s AND %s::boolean ORDER BY r LIMIT 100) "
            "SELECT id, sum(1.0 / (%s + r)) AS score FROM (SELECT * FROM ft UNION ALL "
            "SELECT * FROM vec) x GROUP BY id ORDER BY score DESC, id LIMIT %s",
            (
                text,
                product_id,
                _vec(vector) if vector is not None else None,
                model,
                product_id,
                vector is not None,
                k,
                limit,
            ),
        )
        out = []
        for r in rows:
            post = await self.post(r["id"])
            if post is not None:
                out.append((post, float(r["score"])))
        return out

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
