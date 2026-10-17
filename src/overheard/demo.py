from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from overheard import mail, pipeline
from overheard.db import Database, Product
from overheard.settings import Settings, local_timezone

DEMO_PREFIX = "demo-"
REDDIT = "https://www.reddit.com/r/{sub}/comments/demo{n}/{slug}/"
HN = "https://news.ycombinator.com/item?id=9{n:07d}"


@dataclass
class DemoPost:
    """One made-up post. `quote` must be a sentence of `body` (checked on load)."""

    label: str
    community: str
    title: str
    body: str
    quote: str
    days: float = 0.0
    posted_hours: float = 6.0
    competitor: str | None = None
    open: bool = False
    reply: str | None = None


@dataclass
class DemoTheme:
    """A made-up theme: its posts are spread over weeks by `weekly` (week 0 first)."""

    name: str
    first_seen_days: int
    weekly: list[int]
    posts: list[DemoPost] = field(default_factory=list)


def _p(community: str, title: str, body: str, quote: str, **kw: object) -> DemoPost:
    return DemoPost("pain", community, title, body, quote, **kw)  # type: ignore[arg-type]


MEALWISE = {
    "slug": "demo-mealwise",
    "name": "Mealwise",
    "description": (
        "Plans a week of dinners from what's already in your fridge, leftovers first, so less "
        "food gets thrown away."
    ),
    "phrases": [
        "meal plan from fridge",
        "use up leftovers",
        "meal planning app",
        "food waste at home",
        "pantry recipes",
    ],
    "competitors": ["PlateUp"],
    "recipients": ["you@example.com"],
}
MEALWISE_OPEN = [
    DemoPost(
        "ask",
        "r/MealPrepSunday",
        "Is there an app that plans meals from what's already in my fridge?",
        "I keep buying the same things and throwing half of it away. Is there an app that plans "
        "the week around what I already have? Bonus if it uses leftovers first…",
        "an app that plans the week around what I already have",
        posted_hours=9,
        open=True,
        reply=(
            "Same problem here, which is why I built Mealwise: you tell it what's in the fridge "
            "and it plans the week around that, leftovers first, so the spinach actually gets "
            "eaten.\n\nDisclosure: I made it. There's a free plan; I'd love to hear what's missing."
        ),
    ),
    DemoPost(
        "ask",
        "Hacker News",
        "Ask HN: How do you plan weekly meals?",
        "Anything that works from my pantry instead of a recipe database? Every app assumes I'll "
        "buy everything fresh.",
        "Anything that works from my pantry instead of a recipe database?",
        posted_hours=14,
        open=True,
        reply=(
            "Recipe-first apps had the same problem for me, so I built Mealwise to start from "
            "what's already in the pantry and fridge and plan the week around it. It never "
            "assumes a fresh shop.\n\nDisclosure: I built Mealwise."
        ),
    ),
    DemoPost(
        "you",
        "r/MealPrepSunday",
        "Weekly meal prep check-in",
        "Tried Mealwise last week. The leftovers-first plan is genuinely useful. Still working out "
        "portions for two.",
        "Tried Mealwise last week. The leftovers-first plan is genuinely useful.",
        posted_hours=5,
        open=True,
        reply=(
            "Thanks for giving it a go! Portions for two are next on my list; if you tell me what "
            "felt off, I'll use it.\n\nDisclosure: I built Mealwise."
        ),
    ),
    DemoPost(
        "rival",
        "r/EatCheapAndHealthy",
        "Meal plan apps that don't make you shop?",
        "PlateUp's plans look great, but none of them use what I already bought. I end up with "
        "two fridges' worth of food.",
        "PlateUp's plans look great, but none of them use what I already bought.",
        competitor="PlateUp",
        posted_hours=11,
        open=True,
        reply=(
            "That exact gap is why I made Mealwise: it plans from what you already bought before "
            "it suggests anything new. Curious how it compares for you.\n\nDisclosure: I built "
            "Mealwise."
        ),
    ),
]
MEALWISE_THEMES = [
    DemoTheme(
        "Food goes bad before it's used",
        52,
        [5, 3, 3, 3, 2, 1, 1, 0],
        [
            _p(
                "r/EatCheapAndHealthy",
                "How do you stop produce from going bad?",
                "I throw out a bag of spinach every single week and I hate it. I buy it with the "
                "best intentions.",
                "I throw out a bag of spinach every single week and I hate it.",
                open=True,
                posted_hours=7,
                reply=(
                    "Freezing it the day you buy it helped me a lot. Planning dinners around what "
                    "will go off first helped more, which is why I built Mealwise.\n\nDisclosure: "
                    "I built Mealwise."
                ),
            ),
            _p(
                "r/MealPrepSunday",
                "Is it just me or does everything rot by Friday?",
                "Half my groceries are compost by Friday. It's embarrassing, honestly.",
                "Half my groceries are compost by Friday.",
            ),
            _p(
                "r/Cooking",
                "Herbs always go bad",
                "The cilantro is slime before I remember I bought it. Same with parsley.",
                "The cilantro is slime before I remember I bought it.",
            ),
            _p(
                "r/povertyfinance",
                "Cutting grocery waste",
                "We waste about a third of what we buy and it's mostly vegetables. Any tips?",
                "We waste about a third of what we buy and it's mostly vegetables.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Good intentions, rotten zucchini",
                "I bought zucchini with good intentions and threw it out a week later. Again.",
                "I bought zucchini with good intentions and threw it out a week later.",
            ),
            _p(
                "r/Cooking",
                "Fridge archaeology",
                "Every week I find something fuzzy in the back of the fridge. I need a system.",
                "Every week I find something fuzzy in the back of the fridge.",
            ),
            _p(
                "r/MealPrepSunday",
                "How do you keep berries fresh?",
                "Berries last two days in my fridge, tops. Washing them makes it worse.",
                "Berries last two days in my fridge, tops.",
            ),
            _p(
                "r/Cooking",
                "Half onions everywhere",
                "I keep forgetting the half onion until it's too late. Then I buy another one.",
                "I keep forgetting the half onion until it's too late.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Bagged salad",
                "Bagged salad goes bad before we get through it. Should we stop buying it?",
                "Bagged salad goes bad before we get through it.",
            ),
            _p(
                "r/MealPrepSunday",
                "Leftovers graveyard",
                "Leftovers sit there until they have to be thrown away. Nobody wants them on day "
                "three.",
                "Leftovers sit there until they have to be thrown away.",
            ),
            _p(
                "r/povertyfinance",
                "Food waste is killing my budget",
                "I hate throwing away food I paid for. It feels like burning money.",
                "I hate throwing away food I paid for.",
            ),
            _p(
                "r/Frugal",
                "Small household, big waste",
                "The bread goes stale and the milk turns before we finish them. Two of us, normal "
                "sizes.",
                "The bread goes stale and the milk turns before we finish them.",
            ),
            _p(
                "r/Cooking",
                "Celery problem",
                "Two people can't finish a bunch of celery before it goes limp. Recipes need two "
                "sticks.",
                "Two people can't finish a bunch of celery before it goes limp.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Avocado timing",
                "My avocados go from rock hard to rotten overnight. There is no ripe day.",
                "My avocados go from rock hard to rotten overnight.",
            ),
            _p(
                "r/MealPrepSunday",
                "Prep lasts till Wednesday",
                "I meal prep on Sunday and by Thursday half of it is bad. What am I doing wrong?",
                "I meal prep on Sunday and by Thursday half of it is bad.",
            ),
            _p(
                "Hacker News",
                "Ask HN: Cooking for one without waste?",
                "Whatever I buy for one recipe rots before I find a second use for it. How do "
                "single people shop?",
                "Whatever I buy for one recipe rots before I find a second use for it.",
            ),
            _p(
                "r/Frugal",
                "Weekly food waste",
                "We throw out more food than we eat some weeks. I'd like to fix that this year.",
                "We throw out more food than we eat some weeks.",
            ),
            _p(
                "r/Cooking",
                "The crisper drawer",
                "The vegetable drawer is where good food goes to die. I've stopped opening it.",
                "The vegetable drawer is where good food goes to die.",
            ),
        ],
    ),
    DemoTheme(
        "Recipes ignore what's at home",
        5,
        [5],
        [
            _p(
                "r/MealPrepSunday",
                "Meal planners that work with what you have?",
                "Every planner wants me to buy twelve new ingredients. I just want to cook what's "
                "in the fridge.",
                "Every planner wants me to buy twelve new ingredients.",
                open=True,
                posted_hours=10,
                reply=(
                    "Same. Mealwise starts from what's in your fridge and only adds what's "
                    "missing, if anything. It might fit.\n\nDisclosure: I built Mealwise."
                ),
            ),
            _p(
                "r/Cooking",
                "Recipe sites and shopping lists",
                "Every recipe site wants a trip to the store first. I have food, just no ideas.",
                "Every recipe site wants a trip to the store first.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Fridge-first recipes",
                "I want recipes that start from my fridge, not from a shopping list. Does that "
                "exist?",
                "I want recipes that start from my fridge, not from a shopping list.",
            ),
            _p(
                "Hacker News",
                "Recipe apps are built backwards",
                "The apps never ask what I already have. They start from a dish and send you "
                "shopping.",
                "The apps never ask what I already have.",
            ),
            _p(
                "r/Cooking",
                "Always one thing missing",
                "Recipes always call for one thing I don't have. Usually fresh herbs.",
                "Recipes always call for one thing I don't have.",
            ),
        ],
    ),
    DemoTheme(
        "Planning takes longer than cooking",
        55,
        [2, 2, 3, 2, 1, 1, 1],
        [
            _p(
                "r/Cooking",
                "Meal planning burnout",
                "I spend Sunday evening planning and still order takeout by Wednesday. What's "
                "the point?",
                "I spend Sunday evening planning and still order takeout by Wednesday.",
                open=True,
                posted_hours=12,
                reply=(
                    "Planning fewer dinners helped me: three planned, the rest from leftovers. "
                    "That's the idea behind Mealwise, which plans from what you have in a minute."
                    "\n\nDisclosure: I built Mealwise."
                ),
            ),
            _p(
                "r/MealPrepSunday",
                "Planning vs cooking",
                "Planning the week takes me longer than the cooking does. Something is off.",
                "Planning the week takes me longer than the cooking does.",
            ),
            _p(
                "r/Cooking",
                "Decision fatigue",
                "Deciding what to cook is the hardest part of my day. The cooking is fine.",
                "Deciding what to cook is the hardest part of my day.",
            ),
            _p(
                "r/MealPrepSunday",
                "Spreadsheet dinners",
                "I spend an hour on Sunday with spreadsheets for dinners. My partner laughs at me.",
                "I spend an hour on Sunday with spreadsheets for dinners.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Too tired after planning",
                "By the time I've planned meals I'm too tired to cook them. Tips?",
                "By the time I've planned meals I'm too tired to cook them.",
            ),
            _p(
                "r/Frugal",
                "Meal planning as a job",
                "Meal planning feels like a second job. A job that doesn't pay.",
                "Meal planning feels like a second job.",
            ),
            _p(
                "r/Cooking",
                "The fridge stare",
                "I open the fridge, stare, and close it again. Then I make toast.",
                "I open the fridge, stare, and close it again.",
            ),
            _p(
                "r/MealPrepSunday",
                "Seven planned, three cooked",
                "Every Sunday I plan seven dinners and cook three. The rest go in the bin.",
                "Every Sunday I plan seven dinners and cook three.",
            ),
            _p(
                "Hacker News",
                "Dinner as a daily decision",
                "The mental load of deciding dinner every night is real. I'd pay to outsource it.",
                "The mental load of deciding dinner every night is real.",
            ),
            _p(
                "r/Cooking",
                "Gave up planning",
                "I gave up planning because it took all evening. Now we wing it.",
                "I gave up planning because it took all evening.",
            ),
            _p(
                "r/MealPrepSunday",
                "Family of four",
                "Planning dinners for a family of four eats my whole Sunday. Every single week.",
                "Planning dinners for a family of four eats my whole Sunday.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Just tell me what to make",
                "I'd cook more if someone just told me what to make. With what I have.",
                "I'd cook more if someone just told me what to make.",
            ),
        ],
    ),
    DemoTheme(
        "A pantry tracker that isn't a chore",
        40,
        [1, 1, 1, 1, 0, 1],
        [
            _p(
                "Hacker News",
                "Pantry inventory apps",
                "Every inventory app wants me to scan every can. I won't. There has to be a "
                "lazier way.",
                "Every inventory app wants me to scan every can.",
            ),
            _p(
                "r/Frugal",
                "Pantry spreadsheet",
                "I tried tracking my pantry in a spreadsheet and gave up in a week. Too much "
                "typing.",
                "I tried tracking my pantry in a spreadsheet and gave up in a week.",
            ),
            _p(
                "r/MealPrepSunday",
                "Logging groceries",
                "Typing in every grocery item is more work than just checking the shelf. So I "
                "don't.",
                "Typing in every grocery item is more work than just checking the shelf.",
            ),
            _p(
                "r/Cooking",
                "Barcodes for everything",
                "Pantry apps want barcodes for everything. My carrots don't have one.",
                "Pantry apps want barcodes for everything.",
            ),
            _p(
                "r/EatCheapAndHealthy",
                "Knowing what you have",
                "I want to know what I have without logging it all. Is that too much to ask?",
                "I want to know what I have without logging it all.",
            ),
        ],
    ),
]
MEALWISE_OLDER = [
    DemoPost(
        "ask",
        "r/MealPrepSunday",
        "Leftover dinners",
        "Any app that suggests dinners from leftovers? I have rice and half a chicken most nights.",
        "Any app that suggests dinners from leftovers?",
        days=10,
    ),
    DemoPost(
        "ask",
        "Hacker News",
        "Ask HN: Tools for reducing household food waste?",
        "Are there tools that help a household waste less food? Not recipes, planning.",
        "Are there tools that help a household waste less food?",
        days=17,
    ),
    DemoPost(
        "you",
        "r/EatCheapAndHealthy",
        "Week three of fridge-first cooking",
        "Mealwise picked three dinners from what I had and none of them were weird. Small win.",
        "Mealwise picked three dinners from what I had and none of them were weird.",
        days=12,
    ),
    DemoPost(
        "rival",
        "r/Cooking",
        "Meal planner churn",
        "I liked PlateUp until it kept adding things to my shopping list. Back to paper.",
        "I liked PlateUp until it kept adding things to my shopping list.",
        competitor="PlateUp",
        days=20,
    ),
    DemoPost(
        "ask",
        "r/Frugal",
        "Pantry-first planner?",
        "Is there something like a meal planner that starts from my pantry? Asking for a "
        "tight month.",
        "Is there something like a meal planner that starts from my pantry?",
        days=24,
    ),
]
MEALWISE_NOISE = [
    DemoPost(
        "noise",
        "r/Cooking",
        "Best knives under $50?",
        "Looking for a chef's knife that stays sharp. Budget is tight.",
        "Looking for a chef's knife that stays sharp.",
        posted_hours=8,
    ),
    DemoPost(
        "noise",
        "Hacker News",
        "Show HN: A fridge magnet poetry generator",
        "I built a toy that writes poems from fridge magnet words. It runs in the browser.",
        "I built a toy that writes poems from fridge magnet words.",
        posted_hours=13,
    ),
    DemoPost(
        "noise",
        "r/appliances",
        "Fridge making a buzzing noise",
        "My fridge started buzzing last night. Is the compressor dying?",
        "My fridge started buzzing last night.",
        posted_hours=4,
    ),
    DemoPost(
        "noise",
        "r/MealPrepSunday",
        "Meal prep containers that don't leak?",
        "Which containers actually stay sealed in a backpack? Mine all leak soup.",
        "Which containers actually stay sealed in a backpack?",
        posted_hours=15,
    ),
    DemoPost(
        "noise",
        "r/Baking",
        "Leftover Halloween candy ideas",
        "What do you do with leftover candy? Thinking of cookies.",
        "What do you do with leftover candy?",
        posted_hours=17,
    ),
]

SHIPNOTE = {
    "slug": "demo-shipnote",
    "name": "Shipnote",
    "description": "Turns merged pull requests into release notes customers actually read.",
    "phrases": ["release notes from pull requests", "changelog generator"],
    "competitors": [],
    "recipients": ["you@example.com"],
}
SHIPNOTE_OPEN = [
    DemoPost(
        "ask",
        "Hacker News",
        "Ask HN: Tool that writes release notes from merged PRs?",
        "Our release notes are always late. Is there a tool that turns merged pull requests into "
        "release notes people can read?",
        "Is there a tool that turns merged pull requests into release notes people can read?",
        posted_hours=6,
        open=True,
        reply=(
            "We had the same lag, which is why I built Shipnote: it reads merged PRs and drafts "
            "notes in plain language you can edit before sending.\n\nDisclosure: I built Shipnote."
        ),
    ),
    DemoPost(
        "you",
        "r/SaaS",
        "What do you use for release communication?",
        "We moved to Shipnote last month. Shipnote saved me an hour every release and the notes "
        "are actually readable.",
        "Shipnote saved me an hour every release",
        posted_hours=20,
        open=True,
        reply="Thank you, that's great to hear.\n\nDisclosure: I built Shipnote.",
    ),
]
SHIPNOTE_THEMES = [
    DemoTheme(
        "Changelogs nobody reads",
        22,
        [1, 1, 0, 1],
        [
            _p(
                "r/devops",
                "Nobody reads our changelog",
                "Nobody reads our changelog because it's just a list of commit messages. "
                "Customers keep asking what changed.",
                "Nobody reads our changelog because it's just a list of commit messages.",
                open=True,
                posted_hours=11,
                reply=(
                    "Grouping changes by what customers can do now (not by commit) made ours "
                    "readable. Shipnote does that from merged PRs.\n\nDisclosure: I built Shipnote."
                ),
            ),
            _p(
                "r/ExperiencedDevs",
                "Ticket-number changelogs",
                "Our changelog is just a wall of ticket numbers. Sales hates it.",
                "Our changelog is just a wall of ticket numbers.",
            ),
            _p(
                "r/SaaS",
                "Release notes engagement",
                "Customers never read the release notes we write. Open rates are tiny.",
                "Customers never read the release notes we write.",
            ),
        ],
    )
]
SHIPNOTE_NOISE = [
    DemoPost(
        "noise",
        "r/programming",
        "Semantic versioning debate",
        "Is a new optional field a minor or a patch version? We argue about this every week.",
        "Is a new optional field a minor or a patch version?",
        posted_hours=9,
    ),
]

PLANTPAL = {
    "slug": "demo-plantpal",
    "name": "Plantpal",
    "description": "Reminds you when each houseplant needs water, light or a new pot.",
    "phrases": ["plant watering reminder", "houseplant care app"],
    "competitors": [],
    "recipients": ["you@example.com"],
    "status": "paused",
    "quiet_day_email": True,
}
PLANTPAL_THEMES = [
    DemoTheme(
        "Forgetting to water",
        30,
        [0, 1, 1, 0, 1],
        [
            _p(
                "r/houseplants",
                "Droopy again",
                "I forget to water my plants until the leaves droop. Then I overdo it.",
                "I forget to water my plants until the leaves droop.",
            ),
            _p(
                "r/houseplants",
                "RIP monstera",
                "My monstera died because I watered it on random days. Lesson learned.",
                "My monstera died because I watered it on random days.",
            ),
            _p(
                "r/plantclinic",
                "One schedule for every plant",
                "Every reminder app treats all plants the same. My cactus disagrees.",
                "Every reminder app treats all plants the same.",
            ),
        ],
    )
]

STEPS_TODAY = {
    "demo-mealwise": [
        ("5 Reddit searches planned", "ok", 0.1),
        ("Reddit · meal plan from fridge · 9 posts", "ok", 1.8),
        ("Reddit · use up leftovers · timed out, retried · 7 posts", "retried", 12.4),
        ("Reddit · 3 more phrases · 15 posts", "ok", 4.6),
        ("Hacker News · 5 phrases · 5 posts", "ok", 2.2),
        ("12 new of 36", "ok", 0.4),
        ("Labelled 12 · 1 quote re-checked", "retried", 58.0),
        ("Themes · 1 new", "ok", 6.1),
        ("Email sent", "ok", 1.9),
    ],
    "demo-shipnote": [
        ("2 Reddit searches planned", "ok", 0.1),
        ("Reddit · release notes from pull requests · 4 posts", "ok", 1.6),
        ("Reddit · changelog generator · 2 posts", "ok", 1.4),
        ("Hacker News · 2 phrases · 3 posts", "ok", 1.1),
        ("3 new of 9", "ok", 0.2),
        ("Labelled 3", "ok", 14.0),
        ("Themes · 0 new", "ok", 2.3),
        ("Email sent", "ok", 1.7),
    ],
}
STEPS_YESTERDAY = {
    "demo-mealwise": [
        ("5 Reddit searches planned", "ok", 0.1),
        ("Reddit · 5 phrases · 21 posts", "ok", 7.9),
        ("Resumed after sleep · no search repeated", "retried", 0.3),
        ("Hacker News · 5 phrases · 3 posts", "ok", 2.0),
        ("6 new of 24", "ok", 0.3),
        ("Labelled 6", "ok", 31.0),
        ("Themes · 0 new", "ok", 3.2),
        ("Email sent", "ok", 1.8),
    ],
    "demo-shipnote": [
        ("2 Reddit searches planned", "ok", 0.1),
        ("Reddit · 2 phrases · 5 posts", "ok", 3.1),
        ("Hacker News · 2 phrases · 1 post", "ok", 0.9),
        ("2 new of 6", "ok", 0.2),
        ("Labelled 2", "ok", 9.4),
        ("Themes · 0 new", "ok", 1.9),
        ("Quiet morning · no email", "ok", 0.1),
    ],
}
STEPS_HN_ONLY = {
    "demo-mealwise": [
        ("Hacker News only · search plan used up", "warn", 0.1),
        ("Hacker News · 5 phrases · 4 posts", "ok", 2.4),
        ("2 new of 4", "ok", 0.2),
        ("Labelled 2", "ok", 11.0),
        ("Themes · 0 new", "ok", 2.0),
        ("Email sent", "ok", 1.6),
    ],
    "demo-shipnote": [
        ("Hacker News only · search plan used up", "warn", 0.1),
        ("Hacker News · 2 phrases · 1 post", "ok", 1.0),
        ("1 new of 1", "ok", 0.1),
        ("Labelled 1", "ok", 5.2),
        ("Themes · 0 new", "ok", 1.2),
        ("Quiet morning · no email", "ok", 0.1),
    ],
}


def all_demo_posts() -> list[tuple[str, DemoPost]]:
    """Every demo post with its product slug (for the quote check test).

    Returns:
        (slug, post) pairs.
    """
    out = []
    for slug, groups in (
        ("demo-mealwise", [MEALWISE_OPEN, MEALWISE_OLDER, MEALWISE_NOISE]),
        ("demo-shipnote", [SHIPNOTE_OPEN, SHIPNOTE_NOISE]),
    ):
        for g in groups:
            out += [(slug, p) for p in g]
    for slug, themes in (
        ("demo-mealwise", MEALWISE_THEMES),
        ("demo-shipnote", SHIPNOTE_THEMES),
        ("demo-plantpal", PLANTPAL_THEMES),
    ):
        for t in themes:
            out += [(slug, p) for p in t.posts]
    return out


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
    await db.delete_demo()
    zone = ZoneInfo(await db.get_setting("timezone") or local_timezone())
    hhmm = await db.get_setting("morning_time") or "07:00"
    base = _last_morning(datetime.now(UTC), zone, hhmm)
    loader = _Loader(db, base)
    products: dict[str, Product] = {}
    for spec in (MEALWISE, SHIPNOTE, PLANTPAL):
        fields = {**spec, "demo": True}
        products[spec["slug"]] = await db.create_product(**fields)

    mealwise, shipnote, plantpal = (products[s] for s in ("demo-mealwise", "demo-shipnote",
                                                         "demo-plantpal"))  # fmt: skip
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
    await _runs(db, base, products)

    for product in products.values():
        brief = await pipeline.brief_for(db, product, str(zone))
        if brief.total == 0:
            subject, html, _ = mail.render_quiet(settings, product)
        else:
            subject, html, _ = mail.render_digest(settings, product, brief)
        mail.write_file(settings, product.slug, subject, html)
    return products


async def clear(db: Database, settings: Settings) -> int:
    """Remove demo products (and everything that hangs off them) and demo digests.

    Args:
        db: Database.
        settings: App settings.

    Returns:
        Demo products removed.
    """
    n = await db.delete_demo()
    folder = settings.digests_dir
    if folder.exists():
        for f in folder.glob(f"*-{DEMO_PREFIX}*.html"):
            f.unlink()
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
