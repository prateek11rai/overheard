from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from overheard import mail, pipeline
from overheard.db import Database, Product
from overheard.demo.mealwise import (
    MEALWISE,
    MEALWISE_NOISE,
    MEALWISE_OLDER,
    MEALWISE_OPEN,
    MEALWISE_THEMES,
)
from overheard.demo.samples import (
    PLANTPAL,
    PLANTPAL_THEMES,
    SHIPNOTE,
    SHIPNOTE_NOISE,
    SHIPNOTE_OPEN,
    SHIPNOTE_THEMES,
    STEPS_HN_ONLY,
    STEPS_TODAY,
    STEPS_YESTERDAY,
    all_demo_posts,
)
from overheard.demo.types import DemoPost, DemoTheme
from overheard.settings import Settings

DEMO_PREFIX = "demo-"
DEMO_SLUGS = ("demo-mealwise", "demo-shipnote", "demo-plantpal")
REDDIT = "https://www.reddit.com/r/{sub}/comments/demo{n}/{slug}/"
HN = "https://news.ycombinator.com/item?id=9{n:07d}"


def _check_quotes() -> None:
    for slug, p in all_demo_posts():
        if not pipeline.verify_quote(p.quote, pipeline.post_text(p.title, p.body)):
            raise ValueError(f"demo quote not in its post ({slug}): {p.quote}")


def _last_morning(now: datetime, zone: ZoneInfo, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    local = now.astimezone(zone)
    run = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if run > local:
        run -= timedelta(days=1)
    return run.astimezone(UTC)


class _Loader:
    def __init__(self, db: Database, base: datetime) -> None:
        self.db = db
        self.base = base
        self.n = 0

    def url(self, p: DemoPost) -> str:
        self.n += 1
        slug = "-".join(p.title.lower().split()[:6])
        slug = "".join(c for c in slug if c.isalnum() or c == "-")[:50]
        if p.community.startswith("r/"):
            return REDDIT.format(sub=p.community[2:], n=self.n, slug=slug)
        return HN.format(n=self.n)

    async def add(
        self, product: Product, p: DemoPost, found: datetime, theme_id: int | None = None
    ) -> int:
        source = "reddit" if p.community.startswith("r/") else "hn"
        row = await self.db._one(
            "INSERT INTO posts (product_id, source, url, title, body, community, posted_at, "
            "found_at, label, quote, competitor, label_model, label_attempts, reply_draft, "
            "status, theme_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'demo', 1, "
            "%s, %s, %s) RETURNING id",
            (
                product.id,
                source,
                self.url(p),
                p.title,
                p.body,
                p.community,
                found - timedelta(hours=p.posted_hours),
                found,
                p.label,
                p.quote,
                p.competitor,
                p.reply,
                "new" if p.open else "done",
                theme_id,
            ),
        )
        if row is None:
            raise RuntimeError("demo insert failed")
        return row["id"]

    async def themes(self, product: Product, themes: list[DemoTheme]) -> None:
        for t in themes:
            row = await self.db._one(
                "INSERT INTO themes (product_id, name, first_seen, post_count) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (product.id, t.name, self.base - timedelta(days=t.first_seen_days), len(t.posts)),
            )
            if row is None:
                raise RuntimeError("demo insert failed")
            posts = iter(t.posts)
            for week, count in enumerate(t.weekly):
                for i in range(count):
                    p = next(posts)
                    if p.open:
                        found = self.base + timedelta(minutes=2)
                    else:
                        spread = (i + 1) * 6.0 / (count + 1)
                        found = self.base - timedelta(days=week * 7 + spread)
                        if t.first_seen_days < 7:
                            found = min(found, self.base - timedelta(hours=6))
                    await self.add(product, p, found, row["id"])


async def _runs(db: Database, base: datetime, products: dict[str, Product]) -> None:
    runs = [
        ("demo-morning-0", base, 192, "done", "", 7, 15, STEPS_TODAY),
        ("demo-morning-1", base - timedelta(days=1), 2460, "done", "resumed after sleep", 7, 8,
         STEPS_YESTERDAY),
        ("demo-morning-2", base - timedelta(days=2), 75, "hn_only", "search plan used up", 0, 3,
         STEPS_HN_ONLY),
    ]  # fmt: skip
    for wid, started, secs, status, note, searches, new, steps in runs:
        row = await db._one(
            "INSERT INTO runs (workflow_id, kind, status, note, reddit_planned, searches_used, "
            "new_posts, demo, started_at, finished_at) VALUES (%s, 'morning', %s, %s, %s, %s, %s,"
            " true, %s, %s) RETURNING id",
            (
                wid,
                status,
                note,
                searches,
                searches,
                new,
                started,
                started + timedelta(seconds=secs),
            ),  # fmt: skip
        )
        if row is None:
            raise RuntimeError("demo insert failed")
        for slug, items in steps.items():
            product = products[slug]
            for i, (text, st, seconds) in enumerate(items):
                await db._exec(
                    "INSERT INTO run_steps (run_id, product_id, step_key, text, status, seconds, "
                    "attempts, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row["id"],
                        product.id,
                        f"{slug}:{i}",
                        text,
                        st,
                        seconds,
                        2 if st == "retried" else 1,
                        started + timedelta(seconds=i + (0 if slug == "demo-mealwise" else 60)),
                    ),
                )


async def load(db: Database, settings: Settings) -> dict[str, Product]:
    """Add the sample products with posts, themes, runs and digests (all flagged demo).

    Args:
        db: Database.
        settings: App settings (for writing digests).

    Returns:
        The demo products by slug.
    """
    _check_quotes()
    zone = ZoneInfo(await db.get_setting("timezone") or settings.default_timezone())
    hhmm = await db.get_setting("morning_time") or "07:00"
    base = _last_morning(datetime.now(UTC), zone, hhmm)
    products: dict[str, Product] = {}
    # All or nothing: if anything fails, the previous demo data stays as it was.
    async with db.transaction() as tx:
        await tx.delete_demo()
        loader = _Loader(tx, base)
        for spec in (MEALWISE, SHIPNOTE, PLANTPAL):
            fields = {**spec, "demo": True}
            products[spec["slug"]] = await tx.create_product(**fields)
        mealwise, shipnote, plantpal = (products[s] for s in DEMO_SLUGS)
        for p in MEALWISE_OPEN:
            await loader.add(mealwise, p, base + timedelta(minutes=1))
        for p in MEALWISE_NOISE:
            await loader.add(mealwise, p, base + timedelta(minutes=1))
        for p in MEALWISE_OLDER:
            await loader.add(mealwise, p, base - timedelta(days=p.days))
        await loader.themes(mealwise, MEALWISE_THEMES)
        for p in SHIPNOTE_OPEN + SHIPNOTE_NOISE:
            await loader.add(shipnote, p, base + timedelta(minutes=1))
        await loader.themes(shipnote, SHIPNOTE_THEMES)
        await loader.themes(plantpal, PLANTPAL_THEMES)
        await _runs(tx, base, products)

    _delete_digests(settings)
    for product in products.values():
        brief = await pipeline.brief_for(db, product, str(zone))
        if brief.total == 0:
            subject, html, _ = mail.render_quiet(settings, product)
        else:
            subject, html, _ = mail.render_digest(settings, product, brief)
        mail.write_file(settings, product.slug, subject, html, run="demo")
    return products


def is_demo_digest(name: str) -> bool:
    """Whether a digest file belongs to a demo product (exact slug, never a real product).

    Names are `<date>-<run>-<slug>.html` (the run tag has no dashes); older demo files were
    `<date>-<slug>.html`.

    Args:
        name: The file name.

    Returns:
        True for a demo product's digest.
    """
    if len(name) < 16 or not name.endswith(".html") or name[10:11] != "-":
        return False
    rest = name[11:-5]
    if rest in DEMO_SLUGS:
        return True
    _, _, slug = rest.partition("-")
    return slug in DEMO_SLUGS


def _delete_digests(settings: Settings) -> None:
    folder = settings.digests_dir
    if folder.exists():
        for f in folder.iterdir():
            if f.is_file() and is_demo_digest(f.name):
                f.unlink()


async def clear(db: Database, settings: Settings) -> int:
    """Remove demo products (and everything that hangs off them) and their digest files.

    Args:
        db: Database.
        settings: App settings.

    Returns:
        Demo products removed.
    """
    n = await db.delete_demo()
    _delete_digests(settings)
    return n


async def main(settings: Settings, action: str) -> int:
    """`overheard demo load|clear`.

    Args:
        settings: App settings.
        action: load or clear.

    Returns:
        Exit code.
    """
    async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
        if action == "load":
            products = await load(db, settings)
            counts = await db.open_counts()
            for p in products.values():
                state = "paused" if p.paused else f"{counts.get(p.id, 0)} in the brief"
                print(f"  ✓ {p.name:<10} {state}")
            print(f"Demo data loaded. Digests in {settings.digests_dir}. Open {settings.app_url}")
        else:
            n = await clear(db, settings)
            print(f"Removed {n} demo products and their posts, themes and runs.")
    return 0


async def seed(settings: Settings) -> int:
    """`overheard seed`: add Mealwise and Shipnote as real products, with no posts.

    Args:
        settings: App settings.

    Returns:
        Exit code.
    """
    async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
        for spec in (MEALWISE, SHIPNOTE):
            slug = spec["slug"].removeprefix(DEMO_PREFIX)
            if await db.product_by_slug(slug):
                print(f"  · {spec['name']} already there")
                continue
            fields = {**spec, "slug": slug, "recipients": []}
            await db.create_product(**fields)
            print(f"  ✓ {spec['name']} added")
    return 0
