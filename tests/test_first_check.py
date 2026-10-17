from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from temporalio.testing import ActivityEnvironment

from overheard import sources
from overheard.pipeline import ProductPlan
from overheard.workflows import Activities, PlanArgs, ScanArgs, SearchArgs
from tests.conftest import add_product


async def test_a_products_first_check_looks_back_a_week(db, test_settings):
    product = await add_product(db, recipients=[])
    acts = Activities(test_settings, db)
    env = ActivityEnvironment()
    first = await env.run(acts.plan_check, PlanArgs("check-first", product_id=product.id))
    assert first.products[0].window == "w"
    await db.finish_run(first.run_id, "done")
    later = await env.run(acts.plan_check, PlanArgs("check-later", product_id=product.id))
    assert later.products[0].window == "d"


async def test_the_week_reaches_reddit_and_hacker_news(db, test_settings, monkeypatch):
    live = test_settings.model_copy(
        update={"overheard_offline": False, "serpapi_api_key": SecretStr("test-key-not-real")}
    )
    product = await add_product(db, recipients=[])
    run_id = await db.upsert_run("check-week", "check", product.id)
    seen: dict = {}

    async def fake_reddit(client, key, phrase, subreddits=None, window="d"):
        seen["window"] = window
        return []

    async def fake_hn(client, phrase, since):
        seen["since"] = since
        return []

    monkeypatch.setattr(sources, "search_reddit", fake_reddit)
    monkeypatch.setattr(sources, "search_hn", fake_hn)
    acts = Activities(live, db)
    env = ActivityEnvironment()
    await env.run(
        acts.search_reddit,
        SearchArgs(run_id, "check-week", product.id, "meal plan from fridge", [], "w"),
    )
    assert seen["window"] == "w"
    steps = [s.text for s in await db.steps(run_id)]
    assert any("past week" in t for t in steps)
    plan = ProductPlan(product.id, product.slug, product.name, [], ["meal plan from fridge"])
    plan.window = "w"
    await env.run(acts.search_hn, ScanArgs(product.id, run_id, "check-week", plan))
    assert seen["since"] < datetime.now(UTC) - timedelta(days=6)
