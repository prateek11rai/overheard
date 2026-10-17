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
    assert "Nothing new about Plantpal" in client.get("/p/demo-plantpal").text


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


def test_review_done_moves_to_the_next_post(client):
    page = client.get("/p/demo-mealwise/review").text
    first = re.search(r"/api/posts/(\d+)/done", page).group(1)
    r = client.post(
        f"/api/posts/{first}/done", data={"pos": "0"}, headers=ask("/p/demo-mealwise/review")
    )
    assert "1 of 6" in r.text and f"/api/posts/{first}/done" not in r.text
    assert "Ask HN: How do you plan weekly meals?" in r.text


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
    assert "Nothing new about Mealwise since yesterday." in page
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
    assert 'value="e f"' in r.text and "3 phrases · 0 searches a day" in r.text
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
