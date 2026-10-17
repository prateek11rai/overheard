import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from overheard import demo
from overheard.db import Database
from overheard.settings import Settings
from overheard.web import create_app, parse_emails, safe_url, slugify
from tests.conftest import SCHEMA, TEST_DSN, _reset_schema

ORIGIN = {"Origin": "http://127.0.0.1:8000"}
PAGES = [
    "/p/demo-mealwise",
    "/p/demo-mealwise/review",
    "/p/demo-mealwise/themes",
    "/p/demo-shipnote",
    "/p/demo-plantpal",
    "/products/new",
    "/runs",
    "/settings",
    "/setup",
    "/p/demo-mealwise?show=hidden",
]


def settings_for(tmp_path, **over) -> Settings:
    base = dict(
        _env_file=None,
        database_url=TEST_DSN,
        email_mode="file",
        overheard_data_dir=tmp_path / "data",
        overheard_offline=True,
        temporal_address="127.0.0.1:1",
        llm_base_url="http://127.0.0.1:9",
    )
    base.update(over)
    return Settings(**base)


async def _prepare(settings: Settings) -> None:
    db = Database(TEST_DSN, SCHEMA)
    await db.migrate()
    async with db:
        await demo.load(db, settings)


@pytest.fixture
def settings(tmp_path):
    _reset_schema()
    s = settings_for(tmp_path)
    asyncio.run(_prepare(s))
    return s


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as c:
        yield c


def ask(path):
    return {**ORIGIN, "HX-Request": "true", "HX-Current-URL": "http://127.0.0.1:8000" + path}


def trig(r) -> str:
    return json.dumps(json.loads(r.headers["HX-Trigger"]), ensure_ascii=False)


def tab_count(html: str, name: str) -> str | None:
    m = re.search(
        rf"{name}(?:<span class=\"n\">(\d+)</span>|<span class=\"pz\">paused</span>)", html
    )
    return None if m is None else (m.group(1) or "paused")


def test_every_page_renders_with_demo_data(client):
    for path in PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        html = r.text
        assert "agent-note" not in html and "BUILD NOTE" not in html, path
        assert "<select" not in html and 'type="time"' not in html, path
    brief = client.get("/p/demo-mealwise").text
    assert "<title>(7) overheard</title>" in brief
    assert "2 people asked for something like Mealwise." in brief
    assert "Go through all 7" in brief
    assert "demo data" in brief
    assert "5 off-topic hidden" in brief
    themes = client.get("/p/demo-mealwise/themes").text
    assert "last 4 weeks · 41 posts" in themes
    assert (
        themes.index("Food goes bad")
        < themes.index("Recipes ignore")
        < themes.index("Planning takes")
    )
    assert "Plantpal is paused." in client.get("/p/demo-plantpal").text


def test_security_headers_and_home_redirect(client):
    r = client.get("/p/demo-mealwise")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/", follow_redirects=False).headers["location"] == "/p/demo-mealwise"
    assert client.get("/healthz").json()["ok"] is True


def test_done_and_undo_change_the_page_and_persist(client):
    page = client.get("/p/demo-mealwise").text
    post_id = re.search(r'data-post="(\d+)"', page).group(1)
    r = client.post(f"/api/posts/{post_id}/done", headers=ask("/p/demo-mealwise"))
    assert r.status_code == 200
    assert f'data-post="{post_id}"' not in r.text
    assert "Go through all 6" in r.text
    assert 'hx-swap-oob="true"' in r.text and tab_count(r.text, "Mealwise") == "6"
    assert '"(6) overheard"' in trig(r)
    assert f"/api/posts/{post_id}/undo" in trig(r)
    assert "Go through all 6" in client.get("/p/demo-mealwise").text
    r = client.post(f"/api/posts/{post_id}/undo", headers=ask("/p/demo-mealwise"))
    assert f'data-post="{post_id}"' in r.text
    assert "Go through all 7" in client.get("/p/demo-mealwise").text


def test_skip_snoozes_until_the_next_morning(client):
    page = client.get("/p/demo-mealwise").text
    post_id = re.search(r'data-post="(\d+)"', page).group(1)
    r = client.post(f"/api/posts/{post_id}/skip", headers=ask("/p/demo-mealwise"))
    assert "Skipped · back tomorrow" in trig(r)
    assert "Go through all 6" in client.get("/p/demo-mealwise").text


def test_review_counts_progress_through_the_session(client):
    first = client.get("/p/demo-mealwise/review", follow_redirects=False)
    assert first.status_code == 303 and "session=" in first.headers["location"]
    url = first.headers["location"]
    page = client.get(url).text
    assert "1 of 7" in page and page.count('<i class="cur">') == 1
    pid = re.search(r"/api/posts/(\d+)/done", page).group(1)
    r = client.post(f"/api/posts/{pid}/done", headers=ask(url))
    assert "2 of 7" in r.text
    assert (
        r.text.count('<i class="done">') == 1 and "Ask HN: How do you plan weekly meals?" in r.text
    )
    second = re.search(r"/api/posts/(\d+)/skip", r.text).group(1)
    r = client.post(f"/api/posts/{second}/skip", headers=ask(url))
    assert "3 of 7" in r.text and r.text.count('<i class="done">') == 2
    r = client.post(f"/api/posts/{second}/undo", headers=ask(url))
    assert "2 of 7" in r.text and "Ask HN: How do you plan weekly meals?" in r.text


def test_pause_and_resume_a_product(client):
    page = client.get("/settings").text
    pid = re.search(r"/api/products/(\d+)/pause", page).group(1)
    r = client.post(f"/api/products/{pid}/pause", headers=ask("/settings"))
    assert r.headers["HX-Reswap"] == "none"
    assert "Mealwise paused" in trig(r)
    assert tab_count(r.text, "Mealwise") == "paused"
    assert f"/api/products/{pid}/resume" in r.text
    assert tab_count(client.get("/settings").text, "Mealwise") == "paused"
    r = client.post(f"/api/products/{pid}/resume", headers=ask("/settings"))
    assert tab_count(r.text, "Mealwise") == "7"


def test_schedule_time_and_pause_all_are_saved(client):
    r = client.post("/api/schedule/time", data={"time": "08:30"}, headers=ask("/settings"))
    assert "Daily at 08:30" in r.text
    assert "Temporal isn't running" in trig(r)
    assert "◷ Daily at 08:30" in client.get("/settings").text
    r = client.post("/api/schedule/step", data={"minutes": "5"}, headers=ask("/settings"))
    assert "Daily at 08:35" in r.text
    r = client.post("/api/schedule/pause-all", headers=ask("/settings"))
    assert "◷ Paused" in r.text
    assert "◷ Paused" in client.get("/runs").text
    r = client.post("/api/schedule/time", data={"time": "25:00"}, headers=ask("/settings"))
    assert "That isn't a time" in trig(r)


def test_add_edit_and_delete_a_product(client):
    r = client.post(
        "/products",
        data={
            "name": "Mealwise",
            "description": "Plans dinners from your fridge.",
            "phrases": ["meal plan from fridge", "Pantry Recipes"],
            "recipients": "me@example.com, not-an-email",
            "sources": "hn",
            "quiet": "send",
        },
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/p/mealwise")
    page = client.get("/p/mealwise").text
    assert "Listening. Not checked yet." in page
    assert page.count(">Mealwise<") == 2
    pid = re.search(r"/products/(\d+)/edit", client.get("/settings").text).group(1)
    form = client.get(f"/products/{pid}/edit").text
    assert 'value="pantry recipes"' in form and 'value="me@example.com"' in form
    assert 'value="hn" checked' in form and 'value="send" checked' in form
    r = client.post(
        f"/products/{pid}",
        data={"name": "Mealwise", "description": "New words", "phrases": ["fridge dinners"]},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/products/{pid}/edit?done=saved"
    form = client.get(f"/products/{pid}/edit?done=saved").text
    assert "New words" in form and 'value="fridge dinners"' in form and 'data-toast="Saved"' in form
    r = client.delete(f"/api/products/{pid}", headers=ask("/p/mealwise"))
    assert r.headers["HX-Redirect"] == "/?done=deleted"
    assert client.get("/p/mealwise", follow_redirects=False).status_code == 303
    assert ">Mealwise<" in client.get("/p/demo-mealwise").text


def test_chips_and_badge(client):
    r = client.post(
        "/api/chips/phrases",
        data={"phrases": ["a b", "c d"], "phrases_new": "E F", "sources": "reddit+hn"},
        headers=ask("/products/new"),
    )
    assert 'value="e f"' in r.text and "3 phrases · Hacker News only" in r.text
    r = client.post(
        "/api/chips/phrases?remove=0",
        data={"phrases": ["a b", "c d"]},
        headers=ask("/products/new"),
    )
    assert 'value="a b"' not in r.text and 'value="c d"' in r.text


def test_models_use_saves_the_pick(client):
    r = client.post("/api/models/use", data={"chat": "qwen3:4b"}, headers=ask("/settings"))
    assert "Now using qwen3:4b" in trig(r)
    assert 'value="qwen3:4b" checked' in r.text
    assert 'value="qwen3:4b" checked' in client.get("/settings").text


def test_run_now_without_temporal_says_so(client):
    r = client.post("/api/runs/now", headers=ask("/runs"))
    assert "Temporal isn't running" in trig(r)


def test_cross_origin_posts_are_refused(client):
    page = client.get("/p/demo-mealwise").text
    post_id = re.search(r'data-post="(\d+)"', page).group(1)
    r = client.post(f"/api/posts/{post_id}/done", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post(f"/api/posts/{post_id}/done")
    assert r.status_code == 403
    assert "Go through all 7" in client.get("/p/demo-mealwise").text


def test_post_text_is_escaped(client, settings):
    async def add_evil():
        async with Database(TEST_DSN, SCHEMA) as db:
            p = await db.product_by_slug("demo-mealwise")
            await db._exec(
                "INSERT INTO posts (product_id, source, url, title, body, community, label, quote, "
                "status) VALUES (%s, 'reddit', 'javascript:alert(1)', %s, %s, 'r/x', 'ask', %s, 'new')",
                (
                    p.id,
                    "<script>alert(1)</script> fridge",
                    "<img src=x onerror=alert(1)> is there an app",
                    "is there an app",
                ),
            )

    asyncio.run(add_evil())
    html = client.get("/p/demo-mealwise").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert 'href="javascript:' not in html


def test_unknown_host_header_is_refused(client):
    assert client.get("/p/demo-mealwise", headers={"Host": "evil.example"}).status_code == 400


def test_other_hosts_need_a_password(tmp_path):
    with pytest.raises(RuntimeError, match="OVERHEARD_PASSWORD"):
        create_app(settings_for(tmp_path, overheard_host="0.0.0.0"))


def test_password_protects_every_page(settings, tmp_path):
    s = settings_for(tmp_path, overheard_host="0.0.0.0", overheard_password="pw-for-tests")
    with TestClient(create_app(s), base_url="http://127.0.0.1:8000") as c:
        assert c.get("/p/demo-mealwise").status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/p/demo-mealwise", auth=("any", "wrong")).status_code == 401
        assert c.get("/p/demo-mealwise", auth=("any", "pw-for-tests")).status_code == 200


def test_export_csv_guards_formulas(client):
    r = client.get("/api/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "Mealwise" in r.text


def test_remove_demo_data(client):
    r = client.post("/api/demo/clear", headers=ask("/settings"))
    assert r.headers["HX-Redirect"] == "/?done=demo-cleared"
    assert client.get("/", follow_redirects=False).headers["location"] == "/setup"


def test_helpers():
    assert slugify("Meal Wise!") == "meal-wise"
    assert safe_url("javascript:alert(1)") == "#"
    assert (
        safe_url("https://news.ycombinator.com/item?id=1")
        == "https://news.ycombinator.com/item?id=1"
    )
    assert parse_emails("a@b.co, nope; c@d.io a@b.co") == ["a@b.co", "c@d.io"]


def test_blank_password_on_another_host_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERHEARD_PASSWORD", "   ")
    monkeypatch.setenv("OVERHEARD_HOST", "0.0.0.0")
    s = Settings(_env_file=None, database_url=TEST_DSN)
    with pytest.raises(RuntimeError, match="OVERHEARD_PASSWORD"):
        create_app(s)
    from overheard import cli

    assert cli._web(s) == 2


class FakeHandle:
    def __init__(self, client, wid):
        self.client, self.wid = client, wid

    async def describe(self):
        from types import SimpleNamespace

        return SimpleNamespace(status=self.client.status_of(self.wid))


class FakeTemporal:
    """Just enough of a Temporal client: every known workflow is running."""

    def __init__(self, running=()):
        self.running = set(running)
        self.started = []

    def status_of(self, wid):
        from temporalio.client import WorkflowExecutionStatus

        return (
            WorkflowExecutionStatus.RUNNING
            if wid in self.running
            else WorkflowExecutionStatus.COMPLETED
        )

    def get_workflow_handle(self, wid):
        return FakeHandle(self, wid)

    async def start_workflow(self, *args, **kwargs):
        self.started.append(kwargs["id"])
        self.running.add(kwargs["id"])

    def get_schedule_handle(self, _):
        raise RuntimeError("no schedules in this fake")


def test_a_second_run_now_is_refused_while_one_runs(client, monkeypatch):
    fake = FakeTemporal()

    async def connect(_settings):
        return fake

    monkeypatch.setattr("overheard.workflows.connect", connect)
    r = client.post("/api/runs/now", headers=ask("/runs"))
    assert "Morning brief started" in trig(r) and len(fake.started) == 1
    page = client.get("/runs").text
    assert re.search(r'<button[^>]*hx-post="/api/runs/now"[^>]*disabled', page)
    r = client.post("/api/runs/now", headers=ask("/runs"))
    assert "Already running" in trig(r) and len(fake.started) == 1
    pid = re.search(r"/api/products/(\d+)/check", page).group(1)
    r = client.post(f"/api/products/{pid}/check", headers=ask("/runs"))
    assert "Already running" in trig(r) and len(fake.started) == 1
    # Once Temporal says it finished, both work again.
    fake.running.clear()
    r = client.post(f"/api/products/{pid}/check", headers=ask("/runs"))
    assert "Checking" in trig(r) and len(fake.started) == 2


def test_run_log_shows_a_run_temporal_no_longer_runs_as_failed(client, monkeypatch):
    fake = FakeTemporal()

    async def connect(_settings):
        return fake

    monkeypatch.setattr("overheard.workflows.connect", connect)

    async def stale():
        async with Database(TEST_DSN, SCHEMA) as db:
            rid = await db.upsert_run("lost-run", "morning")
            await db._exec(
                "UPDATE runs SET started_at = now() - interval '2 hours' WHERE id = %s", (rid,)
            )

    asyncio.run(stale())
    page = client.get("/runs").text
    assert "running…" not in page
    assert "failed · stopped: completed" in page
    assert re.search(r'<button[^>]*hx-post="/api/runs/now"(?![^>]*disabled)', page)


def test_switching_embeddings_waits_for_the_reembed(client, monkeypatch):
    r = client.post("/api/models/use", data={"embed": "nomic-embed-text"}, headers=ask("/settings"))
    assert "Temporal isn't running" in trig(r)
    page = client.get("/settings").text
    assert 'name="embed" value="embeddinggemma:300m" checked' in page

    fake = FakeTemporal()

    async def connect(_settings):
        return fake

    monkeypatch.setattr("overheard.workflows.connect", connect)
    r = client.post("/api/models/use", data={"embed": "nomic-embed-text"}, headers=ask("/settings"))
    assert "switches when every post is re-embedded" in trig(r)
    assert any(w.startswith("reembed-") for w in fake.started)
    page = client.get("/settings").text
    assert 'name="embed" value="embeddinggemma:300m" checked' in page
    assert "re-embedding…" in page


def test_the_timezone_can_be_changed_and_updates_the_schedule(client, monkeypatch):
    synced = []

    async def sync(_client, time_of_day, tz, paused):
        synced.append((time_of_day, tz, paused))
        return "updated"

    async def connect(_settings):
        return FakeTemporal()

    monkeypatch.setattr("overheard.workflows.connect", connect)
    monkeypatch.setattr("overheard.workflows.sync_schedule", sync)
    page = client.get("/settings").text
    assert 'name="tz" value="UTC"' in page and "<select" not in page
    r = client.post("/api/schedule/timezone", data={"tz": "Asia/Tokyo"}, headers=ask("/settings"))
    assert "Asia/Tokyo" in trig(r)
    assert synced[-1][1] == "Asia/Tokyo"
    page = client.get("/settings").text
    assert 'name="tz" value="Asia/Tokyo" checked' in page
    assert "Asia/Tokyo" in page.split('id="timepick"')[1][:400]
    r = client.post("/api/schedule/timezone", data={"tz": "Mars/Olympus"}, headers=ask("/settings"))
    assert "isn't a timezone" in trig(r)


def test_csv_cells_that_look_like_formulas_are_escaped():
    from overheard.web.security import csv_safe

    for risky in ("=SUM(A1)", "+1", "-2", "@cmd", "\tx", "\rx"):
        assert csv_safe(risky) == "'" + risky
    assert csv_safe("") == ""
    assert csv_safe(None) is None
    assert csv_safe("plain text") == "plain text"
    assert csv_safe(3) == 3


def test_real_products_cannot_take_the_demo_prefix(client):
    r = client.post(
        "/products", data={"name": "Demo Tracker"}, headers=ORIGIN, follow_redirects=False
    )
    assert r.headers["location"].startswith("/p/p-demo-tracker")


def test_remove_demo_data_in_the_web_deletes_demo_digests(client, settings):
    folder = settings.digests_dir
    assert any("-demo-" in f.name for f in folder.iterdir())
    client.post("/api/demo/clear", headers=ask("/settings"))
    assert not any("demo-" in f.name for f in folder.iterdir())


def test_pause_on_settings_refreshes_the_search_plan(tmp_path):
    s = settings_for(tmp_path, serpapi_api_key="test-key-not-real")
    _reset_schema()
    asyncio.run(_prepare(s))
    with TestClient(create_app(s), base_url="http://127.0.0.1:8000") as c:
        page = c.get("/settings").text
        assert "7 phrases" in page
        pid = re.search(r"/api/products/(\d+)/pause", page).group(1)
        r = c.post(f"/api/products/{pid}/pause", headers=ask("/settings"))
        assert 'id="reddit-plan" hx-swap-oob="true"' in r.text
        assert "2 phrases" in r.text.split('id="reddit-plan"')[1]


def test_check_now_on_the_run_log_adds_its_row(client, monkeypatch):
    fake = FakeTemporal()

    async def connect(_settings):
        return fake

    monkeypatch.setattr("overheard.workflows.connect", connect)
    page = client.get("/runs").text
    pid = re.search(r"/api/products/(\d+)/check", page).group(1)
    r = client.post(f"/api/products/{pid}/check", headers=ask("/runs"))
    assert 'id="runs-main" hx-swap-oob="true"' in r.text
    assert "Check · Mealwise" in r.text


def test_reddit_off_is_one_plain_line(client):
    page = client.get("/settings").text
    plan = page.split('id="reddit-plan"')[1].split("</section>")[0]
    assert "Reddit off · Hacker News only" in plan
    assert "SERPAPI_API_KEY" in plan
    assert 'class="eq"' not in plan and "this month" not in plan
    bar = client.get("/p/demo-mealwise").text.split("</header>")[0]
    assert "0 searches" not in bar
    assert "Hacker News only" in bar or "demo" in bar
    form = client.get("/products/new").text
    assert "0 searches" not in form and "Hacker News only" in form


def test_search_plan_counts_demo_only_without_real_products(tmp_path):
    s = settings_for(tmp_path, serpapi_api_key="test-key-not-real")
    _reset_schema()
    asyncio.run(_prepare(s))
    with TestClient(create_app(s), base_url="http://127.0.0.1:8000") as c:
        plan = c.get("/settings").text.split('id="reddit-plan"')[1].split("</section>")[0]
        assert "7 phrases" in plan and "demo" in plan
        c.post(
            "/products", data={"name": "Tidyshelf", "phrases": ["a b c", "d e f"]}, headers=ORIGIN
        )
        plan = c.get("/settings").text.split('id="reddit-plan"')[1].split("</section>")[0]
        assert "2 phrases" in plan and "· demo" not in plan and "Demo products" not in plan
        bar = c.get("/p/tidyshelf").text.split("</header>")[0]
        assert "2 searches" in bar


def test_theme_counts_name_their_window(client):
    themes = client.get("/p/demo-mealwise/themes").text
    assert "↑ 5 this week · 14 in 4 weeks" in themes and "All 18 posts →" in themes
    assert "4 in 4 weeks" in themes and "All 5 posts →" in themes
    assert "↑ 14 posts" not in themes
    brief = client.get("/p/demo-mealwise").text
    assert "↑ 14 in 4 weeks" in brief


def test_the_menu_flips_back_when_a_check_now_finishes(client):
    client.post("/products", data={"name": "Tidyshelf", "phrases": ["a b c"]}, headers=ORIGIN)

    async def run(action):
        async with Database(TEST_DSN, SCHEMA) as db:
            product = await db.product_by_slug("tidyshelf")
            if action == "start":
                await db.upsert_run("check-tidyshelf-2", "check", product.id)
                await db.add_step(
                    (await db.run_by_workflow("check-tidyshelf-2")).id,
                    "plan",
                    "planned",
                    product_id=product.id,
                )
            else:
                await db.finish_run((await db.run_by_workflow("check-tidyshelf-2")).id, "done")

    idle = client.get("/p/tidyshelf").text
    idle_state = re.search(r'id="bar-state" name="state" value="([^"]+)"', idle).group(1)
    asyncio.run(run("start"))
    page = client.get("/p/tidyshelf").text
    bar, rest = page.split("</header>", 1)
    assert "Checking…" in bar
    # The poller sits outside the bar, so swapping the bar never cuts off its request.
    assert 'id="bar-poll"' in rest and 'id="bar-poll"' not in bar
    assert re.search(r'id="bar-poll"[^>]*hx-get="/api/bar"[^>]*hx-trigger="every 3s"', rest)
    assert 'hx-include="#bar-state"' in rest
    state = re.search(r'id="bar-state" name="state" value="([^"]+)"', bar).group(1)
    assert state != idle_state
    assert client.get(f"/api/bar?state={state}", headers=ask("/p/tidyshelf")).status_code == 204
    r = client.get(f"/api/bar?state={idle_state}", headers=ask("/p/tidyshelf"))
    assert r.status_code == 200 and "Checking…" in r.text

    asyncio.run(run("finish"))
    r = client.get(f"/api/bar?state={state}", headers=ask("/p/tidyshelf"))
    assert r.status_code == 200
    assert 'id="bar" hx-swap-oob="true"' in r.text
    assert "Check now" in r.text and "Checking…" not in r.text
    assert f'value="{idle_state}"' in r.text and 'id="bar-poll"' not in r.text
    assert "closePopovers" not in r.headers.get("HX-Trigger", "")
    assert (
        client.get(f"/api/bar?state={idle_state}", headers=ask("/p/tidyshelf")).status_code == 204
    )


def test_a_product_never_checked_says_so_and_refreshes(client):
    client.post("/products", data={"name": "Tidyshelf", "phrases": ["a b c"]}, headers=ORIGIN)
    page = client.get("/p/tidyshelf").text
    assert "Listening. Not checked yet." in page
    assert "Nothing new about Tidyshelf" not in page

    async def start_check():
        async with Database(TEST_DSN, SCHEMA) as db:
            product = await db.product_by_slug("tidyshelf")
            await db.upsert_run("check-tidyshelf-1", "check", product.id)

    asyncio.run(start_check())
    page = client.get("/p/tidyshelf").text
    assert "Listening. The first check is running." in page
    assert 'hx-get="/api/brief/tidyshelf" hx-trigger="every 3s"' in page
    assert "Checking…" in page
    fragment = client.get("/api/brief/tidyshelf", headers=ask("/p/tidyshelf")).text
    assert 'id="brief-main"' in fragment and "first check is running" in fragment
    assert 'id="bar"' not in fragment

    async def finish():
        async with Database(TEST_DSN, SCHEMA) as db:
            run = await db.run_by_workflow("check-tidyshelf-1")
            product = await db.product_by_slug("tidyshelf")
            await db.add_step(run.id, "plan", "planned", product_id=product.id)
            await db.finish_run(run.id, "done")

    asyncio.run(finish())
    fragment = client.get("/api/brief/tidyshelf", headers=ask("/p/tidyshelf")).text
    assert "Nothing new about Tidyshelf" in fragment and "every 3s" not in fragment
    assert 'id="bar" hx-swap-oob="true"' in fragment
    assert "Check now" in fragment and "Checking…" not in fragment


def test_undo_is_offered_only_where_it_reverses_something(client, monkeypatch):
    fake = FakeTemporal()

    async def connect(_settings):
        return fake

    monkeypatch.setattr("overheard.workflows.connect", connect)
    r = client.post("/api/runs/now", headers=ask("/runs"))
    assert "undo" not in json.loads(r.headers["HX-Trigger"])["toast"]
    page = client.get("/settings").text
    pid = re.search(r"/api/products/(\d+)/pause", page).group(1)
    r = client.post(f"/api/products/{pid}/pause", headers=ask("/settings"))
    assert "undo" not in json.loads(r.headers["HX-Trigger"])["toast"]
    brief = client.get("/p/demo-mealwise").text
    post = re.search(r'data-post="(\d+)"', brief).group(1)
    r = client.post(f"/api/posts/{post}/done", headers=ask("/p/demo-mealwise"))
    assert json.loads(r.headers["HX-Trigger"])["toast"]["undo"] == f"/api/posts/{post}/undo"
    r = client.post(f"/api/posts/{post}/undo", headers=ask("/p/demo-mealwise"))
    assert "undo" not in json.loads(r.headers["HX-Trigger"])["toast"]


def test_done_posts_can_be_found_and_undone_later(client):
    brief = client.get("/p/demo-mealwise").text
    assert "Show done" not in brief
    post = re.search(r'data-post="(\d+)"', brief).group(1)
    client.post(f"/api/posts/{post}/done", headers=ask("/p/demo-mealwise"))
    brief = client.get("/p/demo-mealwise").text
    assert "Show done (1)" in brief
    shown = client.get("/p/demo-mealwise?show=done").text
    assert "Done today" in shown
    assert f'hx-post="/api/posts/{post}/undo"' in shown
    r = client.post(f"/api/posts/{post}/undo", headers=ask("/p/demo-mealwise?show=done"))
    assert f'data-post="{post}"' in r.text and "Show done (1)" not in r.text


def test_out_of_band_fragments_only_go_where_their_target_is(client):
    from overheard.web.context import oob_target_on

    r = client.post("/api/schedule/time", data={"time": "08:00"}, headers=ask("/p/demo-mealwise"))
    assert 'id="bar" hx-swap-oob="true"' in r.text and 'id="timepick"' not in r.text
    r = client.post("/api/schedule/time", data={"time": "07:00"}, headers=ask("/settings"))
    assert 'id="timepick" hx-swap-oob="true"' in r.text
    assert not oob_target_on("_runs_main.html", "/p/demo-mealwise")
    assert not oob_target_on("_bar.html", "/setup")
    assert oob_target_on("_brief_main.html", "/p/demo-mealwise")
    assert not oob_target_on("_brief_main.html", "/p/demo-mealwise/review")


def test_tab_caret_is_a_visible_labelled_triangle(client):
    bar = client.get("/p/demo-mealwise").text.split("</header>")[0]
    caret = re.search(r'<summary class="caret"[^>]*>(.*?)</summary>', bar, re.S)
    assert caret and "<svg" in caret.group(1) and 'aria-label="Mealwise menu"' in caret.group(0)


def test_timezone_names_are_not_squeezed_into_time_cells(client):
    settings = client.get("/settings").text
    assert 'class="pop timegrid"' in settings and 'class="tzlist"' in settings
    css = client.get("/static/app.css").text
    rules = {sel.strip(): body for sel, body in re.findall(r"([^{}/]+)\{([^}]*)\}", css)}
    assert "width:66px" in rules[".timegrid .opt"]
    assert "width:auto" in rules.get(".timegrid .tzlist .opt", "")


def test_setup_says_what_is_off(client, monkeypatch):
    async def all_up(found):
        return [{"name": "Postgres + pgvector", "ok": True, "fix": ""}]

    page = client.get("/setup").text
    assert "all systems ready" not in page
    from overheard.web.views import Views

    monkeypatch.setattr(Views, "_system_checks", lambda self, found: all_up(found))
    page = client.get("/setup").text
    assert "✓ ready · Reddit off · email to file" in page


def test_send_a_test_says_where_it_went(client, settings, tmp_path):
    r = client.post("/api/email/test", headers=ask("/settings"))
    message = json.loads(r.headers["HX-Trigger"])["toast"]["text"]
    assert message.startswith("Test email saved to ") and "-test-test.html" in message
    assert str(settings.digests_dir) in message
    off = settings_for(tmp_path / "off", email_mode="off")
    with TestClient(create_app(off), base_url="http://127.0.0.1:8000") as c:
        r = c.post("/api/email/test", headers=ask("/settings"))
        assert "Email is off" in json.loads(r.headers["HX-Trigger"])["toast"]["text"]


def test_the_first_check_badge_follows_the_phrases(tmp_path):
    s = settings_for(tmp_path, serpapi_api_key="test-key-not-real")
    _reset_schema()
    asyncio.run(_prepare(s))
    with TestClient(create_app(s), base_url="http://127.0.0.1:8000") as c:
        assert "first check now · 0 searches" in c.get("/products/new").text
        data = {"phrases": ["supabase auth"], "phrases_new": "supabase alternative"}
        r = c.post("/api/chips/phrases", data=data, headers=ask("/products/new"))
        assert 'id="first-cost" hx-swap-oob="true"' in r.text
        assert "first check now · 2 searches" in r.text
        hn = c.post(
            "/api/chips/phrases", data={**data, "sources": "hn"}, headers=ask("/products/new")
        )
        assert "first check now · Hacker News only" in hn.text
        edit = c.post("/api/chips/phrases", data=data, headers=ask("/products/1/edit"))
        assert 'id="first-cost"' not in edit.text


def test_compare_models_says_the_labels_are_proposed(client):
    async def save():
        async with Database(TEST_DSN, SCHEMA) as db:
            await db.save_eval(
                kind="checked",
                model="gemma4:e4b",
                items=90,
                right_label=80,
                quote_ok=88,
                ask_total=8,
                ask_found=8,
                seconds_per_post=2.0,
                labels_source="proposed, not yet checked by a person",
                details={},
            )

    asyncio.run(save())
    page = client.get("/settings").text
    assert "Labels proposed, not yet checked by a person · 90 posts" in page
