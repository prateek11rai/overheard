from pydantic import SecretStr
from temporalio.testing import ActivityEnvironment

from overheard import sources
from overheard.sources import SerpApiAccount
from overheard.workflows import Activities, PlanArgs, SearchArgs
from tests.conftest import add_product


def live(test_settings, monkeypatch):
    async def account(client, key):
        return SerpApiAccount(plan_searches=250, searches_left=241, this_month_usage=9)

    async def search(client, key, phrase, subreddits=None, window="d"):
        return []

    monkeypatch.setattr(sources, "serpapi_account", account)
    monkeypatch.setattr(sources, "search_reddit", search)
    return test_settings.model_copy(
        update={"overheard_offline": False, "serpapi_api_key": SecretStr("test-key-not-real")}
    )


async def test_the_run_log_says_which_searches_are_reused_from_today(
    db, test_settings, monkeypatch
):
    settings = live(test_settings, monkeypatch)
    product = await add_product(db, recipients=[])
    acts = Activities(settings, db)
    env = ActivityEnvironment()
    first = await env.run(acts.plan_check, PlanArgs("check-1", product_id=product.id))
    steps = {s.step_key: s.text for s in await db.steps(first.run_id)}
    assert steps[f"plan:{product.id}"] == "2 Reddit searches planned"
    for phrase in first.products[0].reddit_phrases:
        await env.run(acts.search_reddit, SearchArgs(first.run_id, "check-1", product.id, phrase))
    await db.finish_run(first.run_id, "done")

    again = await env.run(acts.plan_morning, PlanArgs("run-now-2", trigger="manual"))
    steps = {s.step_key: s.text for s in await db.steps(again.run_id)}
    assert steps[f"plan:{product.id}"] == "2 Reddit phrases · already searched today, reused"
    phrase = again.products[0].reddit_phrases[0]
    await env.run(acts.search_reddit, SearchArgs(again.run_id, "run-now-2", product.id, phrase))
    steps = {s.step_key: s.text for s in await db.steps(again.run_id)}
    assert steps[f"reddit:{product.id}:{phrase}"].endswith("· reused from today · 0 posts")
    await db.finish_run(again.run_id, "done")
    assert (await db.run(again.run_id)).searches_used == 0
