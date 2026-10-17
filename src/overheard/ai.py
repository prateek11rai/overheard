import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

CHAT_ROLES = ("label", "theme_name", "reply", "phrases")
EMBED_ROLE = "embed"
ROLES = (*CHAT_ROLES, EMBED_ROLE)

M = TypeVar("M", bound=BaseModel)


class ModelUnavailableError(RuntimeError):
    """The model server didn't answer, or answered with an error."""


class BadModelOutputError(RuntimeError):
    """The model's reply didn't match the expected JSON shape."""


@dataclass
class ModelInfo:
    """One model the server offers."""

    name: str
    kind: Literal["chat", "embedding"]
    dim: int | None = None
    loaded: bool = False
    size: int | None = None


@dataclass
class Discovery:
    """What the model server has, split into chat and embedding models."""

    reachable: bool
    server: Literal["ollama", "openai", "none"]
    chat: list[ModelInfo] = field(default_factory=list)
    embedding: list[ModelInfo] = field(default_factory=list)
    error: str | None = None

    def find(self, name: str) -> ModelInfo | None:
        """Look up a model by name.

        Args:
            name: Model name.

        Returns:
            The model, or None if the server doesn't have it.
        """
        for m in (*self.chat, *self.embedding):
            if m.name == name:
                return m
        return None


def model_status(name: str, in_use: str, found: Discovery) -> str:
    """The badge text for a model card in Settings.

    Args:
        name: The model on the card.
        in_use: The model picked for this kind.
        found: What the server reported.

    Returns:
        A short status such as "in use · loaded", "installed" or "unreachable".
    """
    info = found.find(name)
    if not found.reachable or info is None:
        return "unreachable"
    parts = []
    if name == in_use:
        parts.append("in use")
    if info.loaded:
        parts.append("loaded")
    if not parts:
        parts.append("installed")
    if info.kind == "embedding" and info.dim:
        parts.append(f"{info.dim}-d")
    return " · ".join(parts)


class AI:
    """Chat and embedding calls over the OpenAI-compatible API, with models picked by role."""

    def __init__(
        self,
        base_url: str,
        models: dict[str, str],
        api_key: str | None = None,
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client.

        Args:
            base_url: Ollama or any OpenAI-compatible server (with or without `/v1`).
            models: Model name per role (`label`, `theme_name`, `reply`, `phrases`, `embed`).
            api_key: Bearer token, when the server needs one.
            timeout: Seconds per request.
            transport: Test transport for httpx.
        """
        root = base_url.rstrip("/")
        self.root = root[:-3] if root.endswith("/v1") else root
        self.models = dict(models)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers, transport=transport)
        self._is_ollama: bool | None = None

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "AI":
        """Use the client in an `async with` block."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Close the client at the end of an `async with` block."""
        await self.aclose()

    def model_for(self, role: str) -> str:
        """The model that plays a role.

        Args:
            role: A chat role or `embed`.

        Returns:
            The model name.

        Raises:
            ValueError: If the role is unknown.
        """
        if role not in ROLES:
            raise ValueError(f"unknown model role: {role}")
        return self.models[role]

    async def _get(self, path: str) -> httpx.Response:
        return await self._client.get(self.root + path)

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self._client.post(self.root + path, json=body)

    async def is_ollama(self) -> bool:
        """Whether the server speaks Ollama's native API."""
        if self._is_ollama is None:
            try:
                r = await self._get("/api/tags")
                self._is_ollama = r.status_code == 200 and "models" in r.json()
            except (httpx.HTTPError, ValueError):
                self._is_ollama = False
        return self._is_ollama

    async def discover(self) -> Discovery:
        """Find installed and loaded models and tell chat from embedding models.

        Returns:
            What the server has. Never raises; an unreachable server is reported.
        """
        try:
            if await self.is_ollama():
                return await self._discover_ollama()
            return await self._discover_openai()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self._is_ollama = None
            return Discovery(reachable=False, server="none", error=type(exc).__name__)

    async def _discover_ollama(self) -> Discovery:
        tags = (await self._get("/api/tags")).json().get("models", [])
        ps = await self._get("/api/ps")
        loaded = set()
        if ps.status_code == 200:
            loaded = {m["name"] for m in ps.json().get("models", [])}
        found = Discovery(reachable=True, server="ollama")

        async def show(name: str) -> dict:
            r = await self._post("/api/show", {"model": name})
            r.raise_for_status()
            return r.json()

        details = await asyncio.gather(*(show(m["name"]) for m in tags))
        for tag, info in zip(tags, details, strict=True):
            name = tag["name"]
            caps = info.get("capabilities") or []
            mi = info.get("model_info") or {}
            arch = mi.get("general.architecture", "")
            if "embedding" in caps:
                dim = mi.get(f"{arch}.embedding_length")
                found.embedding.append(
                    ModelInfo(name, "embedding", dim, name in loaded, tag.get("size"))
                )
            elif "completion" in caps:
                found.chat.append(ModelInfo(name, "chat", None, name in loaded, tag.get("size")))
        found.chat.sort(key=lambda m: m.name)
        found.embedding.sort(key=lambda m: m.name)
        return found

    async def _discover_openai(self) -> Discovery:
        r = await self._get("/v1/models")
        r.raise_for_status()
        found = Discovery(reachable=True, server="openai")
        for item in r.json().get("data", []):
            name = item["id"]
            try:
                vectors = await self._embed_with(name, ["ping"])
                found.embedding.append(ModelInfo(name, "embedding", len(vectors[0])))
            except (ModelUnavailableError, httpx.HTTPError, ValueError, KeyError, IndexError):
                found.chat.append(ModelInfo(name, "chat"))
        return found

    async def chat(
        self,
        role: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> str:
        """Ask a chat model and return its text.

        Args:
            role: Which role's model to use.
            messages: OpenAI-style messages.
            schema: JSON schema the reply must follow (structured output).
            temperature: Sampling temperature.
            model: Use this model instead of the role's.

        Returns:
            The reply text.

        Raises:
            ModelUnavailableError: If the server fails or isn't reachable.
        """
        body: dict[str, Any] = {
            "model": model or self.model_for(role),
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema, "strict": True},
            }
        if await self.is_ollama():
            body["reasoning_effort"] = "none"
        try:
            r = await self._post("/v1/chat/completions", body)
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(f"model server unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise ModelUnavailableError(f"model server answered {r.status_code}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError) as exc:
            raise ModelUnavailableError("model server sent an unexpected reply") from exc

    async def chat_json(
        self,
        role: str,
        messages: list[dict[str, str]],
        shape: type[M],
        model: str | None = None,
    ) -> M:
        """Ask for JSON that matches a pydantic model, and validate it.

        Args:
            role: Which role's model to use.
            messages: OpenAI-style messages.
            shape: The pydantic model the reply must match.
            model: Use this model instead of the role's.

        Returns:
            The validated reply.

        Raises:
            BadModelOutputError: If the reply isn't valid JSON of that shape.
        """
        text = await self.chat(role, messages, schema=shape.model_json_schema(), model=model)
        try:
            return shape.model_validate_json(_strip_fences(text))
        except (ValidationError, ValueError) as exc:
            raise BadModelOutputError("reply did not match the expected shape") from exc

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Embed texts with the embedding model.

        Args:
            texts: Texts to embed.
            model: Use this model instead of the one picked in Settings.

        Returns:
            One vector per text.
        """
        return await self._embed_with(model or self.model_for(EMBED_ROLE), texts)

    async def _embed_with(self, model: str, texts: list[str]) -> list[list[float]]:
        try:
            r = await self._post("/v1/embeddings", {"model": model, "input": texts})
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(f"model server unreachable ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise ModelUnavailableError(f"model server answered {r.status_code}")
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        t = t[start : end + 1]
    json.loads(t)
    return t


def role_models(stored: dict[str, Any] | None, chat_default: str, embed_default: str) -> dict:
    """The model for each role: what Settings picked, else the environment defaults.

    Args:
        stored: The `models` app setting (role to model name).
        chat_default: `CHAT_MODEL`.
        embed_default: `EMBED_MODEL`.

    Returns:
        Role to model name for every role.
    """
    stored = stored or {}
    out = {}
    for role in ROLES:
        default = embed_default if role == EMBED_ROLE else chat_default
        out[role] = (
            stored.get(role) or stored.get("chat" if role != EMBED_ROLE else "embed") or default
        )
    return out
