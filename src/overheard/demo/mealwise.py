from overheard.demo.types import DemoPost, DemoTheme, _p

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
