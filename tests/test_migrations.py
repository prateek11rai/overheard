import psycopg

from overheard.db import Database
from tests.conftest import SCHEMA, TEST_DSN, _reset_schema

OUTSIDE = (
    "SELECT n.nspname, c.relname, c.relkind FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast', %s) "
    "AND n.nspname NOT LIKE 'pg_temp%%' AND n.nspname NOT LIKE 'pg_toast_temp%%' ORDER BY 1, 2"
)


def snapshot():
    with psycopg.connect(TEST_DSN) as conn:
        objs = conn.execute(OUTSIDE, (SCHEMA,)).fetchall()
        schemas = conn.execute("SELECT nspname FROM pg_namespace ORDER BY 1").fetchall()
        exts = conn.execute("SELECT extname FROM pg_extension ORDER BY 1").fetchall()
    return objs, schemas, exts


async def test_migrate_twice_only_touches_its_schema():
    _reset_schema()
    before_objs, before_schemas, before_exts = snapshot()
    db = Database(TEST_DSN, SCHEMA)
    assert await db.migrate() == ["001_init.sql", "002_digest_ledger.sql"]
    assert await db.migrate() == []
    after_objs, after_schemas, after_exts = snapshot()
    assert after_objs == before_objs
    assert after_exts == before_exts
    assert set(after_schemas) - set(before_schemas) == {(SCHEMA,)}
    await db.open()
    health = await db.health()
    await db.close()
    assert health["pgvector"] and health["tables"] >= 10
