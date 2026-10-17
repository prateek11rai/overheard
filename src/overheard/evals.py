import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from overheard import pipeline, sources
from overheard.ai import AI, ModelUnavailableError, role_models
from overheard.db import Database, Product
from overheard.settings import Settings

EVAL_DIR = Path("data/eval")
HN_ITEM_API = "https://hn.algolia.com/api/v1/items/"
CONTEXTS = {
    "mealwise": (
        "Mealwise",
        "Plans a week of dinners from what's already in your fridge, leftovers first, so less "
        "food gets thrown away.",
        ["PlateUp"],
    ),
    "shipnote": (
        "Shipnote",
        "Turns merged pull requests into release notes customers actually read.",
        [],
    ),
    "plantpal": (
        "Plantpal",
        "Reminds you when each houseplant needs water, light or a new pot.",
        [],
    ),
}


@dataclass
class Item:
    """One checked post with its agreed label."""

    id: str
    product: str
    label: str
    title: str = ""
    text: str = ""
    community: str = ""
    competitor: str | None = None
    synthetic: bool = False
    hn_id: int | None = None
    is_comment: bool = False


@dataclass
class ModelResult:
    """How one model did on the checked set."""

    model: str
    items: int = 0
    right: int = 0
    quote_ok: int = 0
    ask_total: int = 0
    ask_found: int = 0
    seconds: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def seconds_per_post(self) -> float:
        """Average seconds per post."""
        return self.seconds / self.items if self.items else 0.0


def context_product(key: str) -> Product:
    """A product built from one of the sample contexts.

    Args:
        key: mealwise, shipnote or plantpal.

    Returns:
        A product (not saved anywhere).
    """
    name, description, competitors = CONTEXTS[key]
    return Product(
        id=0,
        slug=key,
        name=name,
        description=description,
        phrases=[],
        competitors=competitors,
        subreddits=[],
        sources="reddit+hn",
        recipients=[],
        quiet_day_email=False,
        status="active",
        demo=True,
        created_at=datetime.now(UTC),
    )


def load_items(folder: Path = EVAL_DIR) -> tuple[list[Item], str]:
    """Read the checked set, preferring labels a person has checked.

    Args:
        folder: The eval folder.

    Returns:
        (items, where the labels came from).

    Raises:
        FileNotFoundError: If neither labels file exists.
    """
    checked = folder / "labels.jsonl"
    path = checked if checked.exists() else folder / "labels.proposed.jsonl"
    source = "checked by a person" if path == checked else "proposed, not yet checked by a person"
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            items.append(
                Item(
                    id=d["id"],
                    product=d["product"],
                    label=d["label"],
                    title=d.get("title", ""),
                    text=d.get("text", ""),
                    community=d.get("community", ""),
                    competitor=d.get("competitor"),
                    synthetic=bool(d.get("synthetic")),
                    hn_id=d.get("hn_id"),
                )
            )
    return items, source


async def fetch_hn_item(client: httpx.AsyncClient, item: Item) -> Item:
    """Fill a Hacker News item's title and text from its id.

    Args:
        client: HTTP client.
        item: An item with `hn_id`.

    Returns:
        The item with title, text and community filled in.
    """
    r = await client.get(f"{HN_ITEM_API}{item.hn_id}", timeout=20)
    r.raise_for_status()
    d = r.json()
    hit = {
        "objectID": str(d.get("id")),
        "title": d.get("title"),
        "story_text": d.get("text") if d.get("type") == "story" else None,
        "comment_text": d.get("text") if d.get("type") == "comment" else None,
        "story_title": d.get("story_title") or d.get("title"),
        "created_at_i": d.get("created_at_i"),
    }
    post = sources.parse_hn({"hits": [hit]})[0]
    item.title, item.text, item.community = post.title, post.body, post.community
    item.is_comment = post.is_comment
    return item


async def run_model(
    ai: AI, model: str, items: list[Item], progress: Callable[[int], None] | None = None
) -> ModelResult:
    """Label every item with one model, the same way the morning job does.

    Args:
        ai: Model client.
        model: The chat model.
        items: The checked items.
        progress: Called after each item.

    Returns:
        The model's numbers.
    """
    res = ModelResult(model)
    for i, item in enumerate(items):
        product = context_product(item.product)
        started = time.monotonic()
        out = await pipeline.label_post(
            ai,
            product,
            item.title,
            item.text,
            item.community,
            model=model,
            is_comment=item.is_comment,
        )
        res.seconds += time.monotonic() - started
        res.items += 1
        res.labels[item.id] = out.label
        res.right += out.label == item.label
        res.quote_ok += out.label != "unverified"
        if item.label == "ask":
            res.ask_total += 1
            res.ask_found += out.label == "ask"
        if progress:
            progress(i + 1)
    return res


def _pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "—"


def table(results: list[ModelResult], markdown: bool) -> str:
    """The comparison table.

    Args:
        results: One result per model.
        markdown: Markdown table instead of plain text.

    Returns:
        The table text.
    """
    rows = [
        ("right label", [f"{r.right}/{r.items}" for r in results]),
        ("quote checks out", [_pct(r.quote_ok, r.items) for r in results]),
        ("asking-for-a-tool recall", [_pct(r.ask_found, r.ask_total) for r in results]),
        ("seconds per post", [f"{r.seconds_per_post:.1f}" for r in results]),
    ]
    head = ["", *[r.model for r in results]]
    if markdown:
        lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        lines += ["| " + " | ".join([k, *v]) + " |" for k, v in rows]
        return "\n".join(lines)
    width = max(len(k) for k, _ in rows) + 2
    cols = [max(len(m), 8) + 2 for m in head[1:]]
    out = ["".ljust(width) + "".join(m.ljust(c) for m, c in zip(head[1:], cols, strict=True))]
    for k, v in rows:
        out.append(k.ljust(width) + "".join(x.ljust(c) for x, c in zip(v, cols, strict=True)))
    return "\n".join(out)


def disagreements(results: list[ModelResult], items: list[Item]) -> list[str]:
    """Items where the models' labels differ.

    Args:
        results: One result per model.
        items: The checked items.

    Returns:
        One line per item.
    """
    if len(results) < 2:
        return []
    lines = []
    for item in items:
        labels = [r.labels.get(item.id, "") for r in results]
        if len(set(labels)) > 1:
            got = ", ".join(f"{r.model}={lab}" for r, lab in zip(results, labels, strict=True))
            lines.append(f"{item.id} (agreed: {item.label}): {got}")
    return lines


def save_result(res: ModelResult, source: str, folder: Path = EVAL_DIR) -> Path:
    """Write one model's result to `data/eval/results/<date>-<model>.json`.

    Args:
        res: The result.
        source: Where the labels came from.
        folder: The eval folder.

    Returns:
        The file written.
    """
    out = folder / "results"
    out.mkdir(parents=True, exist_ok=True)
    safe = pipeline.safe_filename(res.model.replace(":", "-"))
    path = out / f"{datetime.now(UTC):%Y-%m-%d}-{safe}.json"
    path.write_text(
        json.dumps(
            {
                "model": res.model,
                "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "labels_source": source,
                "items": res.items,
                "right_label": res.right,
                "quote_checks_out": res.quote_ok,
                "ask_total": res.ask_total,
                "ask_found": res.ask_found,
                "seconds_per_post": round(res.seconds_per_post, 2),
                "labels": res.labels,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


async def main(
    settings: Settings,
    models: list[str],
    markdown: bool = False,
    synthetic_only: bool = False,
    limit: int | None = None,
) -> int:
    """`overheard eval --model A --model B`: compare models on the checked set.

    Args:
        settings: App settings.
        models: Chat models to compare.
        markdown: Print the table as Markdown.
        synthetic_only: Skip Hacker News items fetched by id.
        limit: Use at most this many items.

    Returns:
        Exit code.
    """
    items, source = load_items()
    if synthetic_only:
        items = [i for i in items if i.synthetic]
    if limit:
        items = items[:limit]
    hn = [i for i in items if i.hn_id]
    if hn:
        async with httpx.AsyncClient() as client:
            for item in hn:
                await fetch_hn_item(client, item)
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    roles = role_models(None, settings.chat_model, settings.embed_model)
    results = []
    async with AI(settings.llm_base_url, roles, key) as ai:
        for model in models:
            print(f"{model}: labelling {len(items)} posts…", flush=True)
            try:
                res = await run_model(
                    ai, model, items, lambda n: print(f"  {n}/{len(items)}", end="\r", flush=True)
                )
            except ModelUnavailableError as exc:
                print(f"\n{model}: {exc}. Is it installed? ollama pull {model}")
                return 1
            print()
            results.append(res)
            print(f"  saved {save_result(res, source)}")
    print(
        f"\nLabels: {source}. Items: {len(items)}" + (" (synthetic only)" if synthetic_only else "")
    )
    print(table(results, markdown))
    diff = disagreements(results, items)
    if diff:
        print("\nWhere the models disagree:")
        print("\n".join(f"- {d}" for d in diff))
    try:
        async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
            for r in results:
                await db.save_eval(
                    kind="checked",
                    model=r.model,
                    items=r.items,
                    right_label=r.right,
                    quote_ok=r.quote_ok,
                    ask_total=r.ask_total,
                    ask_found=r.ask_found,
                    seconds_per_post=r.seconds_per_post,
                    labels_source=source,
                    details={"labels": r.labels, "synthetic_only": synthetic_only},
                )
    except Exception as exc:
        print(f"(not saved to the database: {type(exc).__name__})")
    return 0


async def compare_recent(
    db: Database,
    ai: AI,
    model: str,
    limit: int = 50,
    heartbeat: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Label the last real posts with another model without changing any label.

    Args:
        db: Database.
        ai: Model client.
        model: The model to compare.
        limit: How many recent posts.
        heartbeat: Called after each post (Temporal heartbeat).

    Returns:
        A dict with agree, items, seconds and the disagreements.
    """
    posts = await db.recent_labelled(limit)
    products = {p.id: p for p in await db.products()}
    agree, seconds, diffs = 0, 0.0, []
    for i, post in enumerate(posts):
        product = products.get(post.product_id)
        if product is None:
            continue
        started = time.monotonic()
        out = await pipeline.label_post(
            ai,
            product,
            post.title,
            post.body,
            post.community,
            model=model,
            is_comment=post.is_comment,
        )
        seconds += time.monotonic() - started
        if out.label == post.label:
            agree += 1
        else:
            diffs.append({"post_id": post.id, "stored": post.label, "model": out.label})
        if heartbeat:
            heartbeat(i)
    await db.save_eval(
        kind="recent",
        model=model,
        items=len(posts),
        right_label=agree,
        quote_ok=sum(1 for _ in posts) - sum(1 for d in diffs if d["model"] == "unverified"),
        ask_total=0,
        ask_found=0,
        seconds_per_post=seconds / len(posts) if posts else 0.0,
        labels_source="your recent posts (labels from the model in use)",
        details={"disagreements": diffs},
    )
    return {"agree": agree, "items": len(posts), "seconds": seconds, "disagreements": diffs}
