import json
from collections.abc import Callable

import httpx

from overheard.ai import AI
from tests.conftest import load_fixture

MODELS = {
    "label": "gemma4:e4b",
    "theme_name": "gemma4:e4b",
    "reply": "gemma4:e4b",
    "phrases": "gemma4:e4b",
    "embed": "embeddinggemma:300m",
}


def ollama_handler(
    chat: Callable[[dict], dict | str] | None = None,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
    ps: dict | None = None,
):
    """An httpx handler that answers like the recorded Ollama, with scripted chat replies."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json=load_fixture("ollama_tags.json"))
        if path == "/api/ps":
            return httpx.Response(
                200, json=ps if ps is not None else load_fixture("ollama_ps.json")
            )
        if path == "/api/show":
            name = json.loads(request.content)["model"].replace(":", "_").replace(".", "_")
            return httpx.Response(200, json=load_fixture(f"ollama_show_{name}.json"))
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            calls.append(body)
            reply = chat(body) if chat else {}
            content = reply if isinstance(reply, str) else json.dumps(reply)
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
            )
        if path == "/v1/embeddings":
            body = json.loads(request.content)
            vectors = embed(body["input"]) if embed else [[1.0, 0.0] for _ in body["input"]]
            return httpx.Response(
                200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]}
            )
        return httpx.Response(404)

    handler.calls = calls
    return handler


def fake_ai(handler) -> AI:
    return AI("http://127.0.0.1:11434", MODELS, transport=httpx.MockTransport(handler))
