from overheard.demo.mealwise import MEALWISE_NOISE, MEALWISE_OLDER, MEALWISE_OPEN, MEALWISE_THEMES
from overheard.demo.types import DemoPost, DemoTheme, _p

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
