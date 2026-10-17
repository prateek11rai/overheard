from collections.abc import Iterable
from datetime import datetime

from overheard.db.core import DatabaseCore
from overheard.db.models import LABEL_ORDER, OPEN_FILTER, POST_COLS, Post
from overheard.sources import Post as FoundPost


class PostQueries(DatabaseCore):
    """Queries about posts."""

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
                    " run_id, is_comment) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
                        p.is_comment,
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

    async def useful_count(self, product_id: int, days: int = 28) -> int:
        """Posts with a useful label found in the last few days.

        Args:
            product_id: Product id.
            days: Window size.

        Returns:
            The count.
        """
        row = await self._one(
            "SELECT count(*) AS n FROM posts WHERE product_id = %s AND found_at >= now() - "
            "make_interval(days => %s) AND label IN ('ask', 'pain', 'you', 'rival')",
            (product_id, days),
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
