import httpx

from overheard.ai import AI, model_status
from tests.fakes import MODELS, fake_ai, ollama_handler


async def test_discovery_from_recorded_ollama_replies():
    handler = ollama_handler()
    async with fake_ai(handler) as ai:
        found = await ai.discover()
    assert found.reachable and found.server == "ollama"
    assert [m.name for m in found.chat] == ["gemma4:e4b"]
    assert [m.name for m in found.embedding] == ["embeddinggemma:300m"]
    emb = found.embedding[0]
    assert emb.dim == 768
    assert emb.loaded
    assert found.chat[0].loaded


async def test_discovery_without_loaded_models():
    async with fake_ai(ollama_handler(ps={"models": []})) as ai:
        found = await ai.discover()
    assert not found.chat[0].loaded
    assert model_status("gemma4:e4b", "gemma4:e4b", found) == "in use"
    assert model_status("embeddinggemma:300m", "x", found) == "installed · 768-d"


async def test_status_badges():
    async with fake_ai(ollama_handler()) as ai:
        found = await ai.discover()
    assert model_status("gemma4:e4b", "gemma4:e4b", found) == "in use · loaded"
    assert model_status("embeddinggemma:300m", "embeddinggemma:300m", found) == (
        "in use · loaded · 768-d"
    )
    assert model_status("qwen3:4b", "gemma4:e4b", found) == "unreachable"


async def test_unreachable_server():
    def down(request):
        raise httpx.ConnectError("refused")

    ai = AI("http://127.0.0.1:9", MODELS, transport=httpx.MockTransport(down))
    found = await ai.discover()
    await ai.aclose()
    assert not found.reachable
    assert model_status("gemma4:e4b", "gemma4:e4b", found) == "unreachable"


async def test_openai_compatible_server_tells_embedders_apart():
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(404)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "chatty"}, {"id": "embedder"}]})
        if request.url.path == "/v1/embeddings":
            import json

            if json.loads(request.content)["model"] == "embedder":
                return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 384}]})
            return httpx.Response(400, json={"error": "not an embedding model"})
        return httpx.Response(404)

    ai = AI("http://127.0.0.1:1234/v1", MODELS, transport=httpx.MockTransport(handler))
    found = await ai.discover()
    await ai.aclose()
    assert found.server == "openai"
    assert [m.name for m in found.chat] == ["chatty"]
    assert [(m.name, m.dim) for m in found.embedding] == [("embedder", 384)]


async def test_models_are_requested_by_role():
    handler = ollama_handler(chat=lambda body: {"phrases": ["A", "b", "b"]})
    ai = fake_ai(handler)
    ai.models["phrases"] = "small-model"
    await ai.chat("phrases", [{"role": "user", "content": "hi"}])
    await ai.aclose()
    assert handler.calls[0]["model"] == "small-model"
    assert handler.calls[0]["reasoning_effort"] == "none"
