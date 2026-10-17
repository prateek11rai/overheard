from typing import Any

from psycopg import sql

from overheard.db.core import DatabaseCore
from overheard.db.models import PRODUCT_COLS, Product


class ProductQueries(DatabaseCore):
    """Queries about products."""

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
