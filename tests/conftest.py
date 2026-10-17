import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from overheard.db import Database, Product
from overheard.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DSN = os.environ.get("OVERHEARD_TEST_DATABASE_URL", "postgresql:///overheard_test")
SCHEMA = "overheard"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def _reset_schema() -> None:
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        name = conn.execute("SELECT current_database()").fetchone()[0]
        if not name.endswith("_test"):
            raise RuntimeError(f"refusing to reset a database that isn't a test one: {name}")
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


@pytest.fixture
async def db():
    _reset_schema()
    database = Database(TEST_DSN, SCHEMA)
    await database.migrate()
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=TEST_DSN,
        email_mode="file",
        overheard_data_dir=tmp_path / "data",
        overheard_offline=True,
    )


def make_product(**over) -> Product:
    base = dict(
        id=1,
        slug="mealwise",
        name="Mealwise",
        description="Plans a week of dinners from what's already in your fridge.",
        phrases=["meal plan from fridge", "use up leftovers"],
        competitors=["PlateUp"],
        subreddits=[],
        sources="reddit+hn",
        recipients=[],
        quiet_day_email=False,
        status="active",
        demo=False,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    base.update(over)
    return Product(**base)


async def add_product(db: Database, **over) -> Product:
    fields = dict(
        slug="mealwise",
        name="Mealwise",
        description="Plans a week of dinners from what's already in your fridge.",
        phrases=["meal plan from fridge", "use up leftovers"],
        competitors=["PlateUp"],
    )
    fields.update(over)
    return await db.create_product(**fields)
