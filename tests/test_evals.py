import json

import httpx

from overheard import evals
from overheard.evals import Item, ModelResult, disagreements, load_items, run_model, table
from tests.conftest import load_fixture
from tests.fakes import fake_ai, ollama_handler


def test_load_items_prefers_checked_labels(tmp_path):
    (tmp_path / "labels.proposed.jsonl").write_text(
        json.dumps({"id": "a", "product": "mealwise", "label": "ask", "synthetic": True}) + "\n"
    )
    items, source = load_items(tmp_path)
    assert [i.id for i in items] == ["a"] and source.startswith("proposed")
    (tmp_path / "labels.jsonl").write_text(
        json.dumps({"id": "b", "product": "shipnote", "label": "pain", "hn_id": 1}) + "\n"
    )
    items, source = load_items(tmp_path)
    assert [i.id for i in items] == ["b"] and source == "checked by a person"
    assert items[0].hn_id == 1 and not items[0].synthetic


def test_the_shipped_set_is_thirty_synthetic_items():
    items, source = load_items()
    assert len(items) == 30 and all(i.synthetic for i in items)
    assert {i.product for i in items} == {"mealwise", "shipnote", "plantpal"}
    assert {i.label for i in items} == {"ask", "pain", "you", "rival", "noise"}


async def test_run_model_scores_labels_and_recall():
    items = [
        Item("1", "mealwise", "ask", "t", "Is there an app that plans my week from my fridge?"),
        Item("2", "mealwise", "pain", "t", "My spinach goes bad every single week."),
    ]
    replies = iter(
        [
            {"label": "ask", "quote": "Is there an app that plans my week from my fridge?"},
            {"label": "noise", "quote": "My spinach goes bad every single week."},
        ]
    )
    async with fake_ai(ollama_handler(chat=lambda body: next(replies))) as ai:
        res = await run_model(ai, "gemma4:e4b", items)
    assert (res.items, res.right, res.quote_ok, res.ask_total, res.ask_found) == (2, 1, 2, 1, 1)
    assert res.labels == {"1": "ask", "2": "noise"}


def test_table_and_disagreements():
    a = ModelResult(
        "m1",
        items=2,
        right=2,
        quote_ok=2,
        ask_total=1,
        ask_found=1,
        seconds=3.0,
        labels={"x": "ask", "y": "pain"},
    )
    b = ModelResult(
        "m2",
        items=2,
        right=1,
        quote_ok=1,
        ask_total=1,
        ask_found=0,
        seconds=1.0,
        labels={"x": "noise", "y": "pain"},
    )
    md = table([a, b], markdown=True)
    assert md.splitlines()[0] == "|  | m1 | m2 |"
    assert "| right label | 2/2 | 1/2 |" in md
    assert "| asking-for-a-tool recall | 100% | 0% |" in md
    assert "| seconds per post | 1.5 | 0.5 |" in md
    items = [Item("x", "mealwise", "ask"), Item("y", "mealwise", "pain")]
    assert disagreements([a, b], items) == ["x (agreed: ask): m1=ask, m2=noise"]


async def test_hacker_news_items_are_fetched_by_id():
    def handler(request):
        assert request.url.path == "/api/v1/items/41000002"
        return httpx.Response(200, json=load_fixture("hn_item.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        item = await evals.fetch_hn_item(client, Item("h", "mealwise", "pain", hn_id=41000002))
    assert item.title == "Ask HN: How do you plan weekly meals?"
    assert item.text.startswith("Every inventory app wants me to scan every can.")
    assert item.community == "Hacker News"
