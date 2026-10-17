from overheard.db.core import DatabaseCore
from overheard.db.models import (
    HIDDEN_LABELS,
    LABEL_ORDER,
    MIGRATIONS_DIR,
    OPEN_FILTER,
    POST_COLS,
    PRODUCT_COLS,
    USEFUL_LABELS,
    Post,
    Product,
    Run,
    Step,
    ThemeStat,
    index_name,
)
from overheard.db.posts import PostQueries
from overheard.db.products import ProductQueries
from overheard.db.runs import RunQueries
from overheard.db.themes import ThemeQueries


class Database(ProductQueries, PostQueries, ThemeQueries, RunQueries):
    """Postgres access for Overheard: one pool, every query, schema-scoped."""


__all__ = [
    "Database",
    "DatabaseCore",
    "HIDDEN_LABELS",
    "LABEL_ORDER",
    "MIGRATIONS_DIR",
    "OPEN_FILTER",
    "POST_COLS",
    "PRODUCT_COLS",
    "Post",
    "Product",
    "Run",
    "Step",
    "ThemeStat",
    "USEFUL_LABELS",
    "index_name",
]
