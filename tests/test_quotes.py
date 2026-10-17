from overheard.pipeline import locate_quote, normalize, verify_quote

POST = (
    "I keep buying the same things and throwing half of it away. Is there an app that plans "
    "the week around what I already have? Bonus if it uses leftovers first…"
)


def test_exact_quote():
    assert verify_quote("an app that plans the week around what I already have", POST)


def test_ignores_case_and_spacing():
    assert verify_quote("AN APP that   plans the\nweek around WHAT i already have", POST)


def test_curly_and_straight_quotes_match():
    text = "Every planner wants me to buy “twelve new ingredients” and I can’t afford that."
    assert verify_quote('wants me to buy "twelve new ingredients" and I can\'t afford', text)
    assert verify_quote("I can’t afford that", "honestly i can't afford that anymore")


def test_ellipsis_stands_for_left_out_words():
    assert verify_quote("I keep buying the same things … what I already have", POST)
    assert verify_quote("I keep buying the same things ... what I already have", POST)
    assert verify_quote("Bonus if it uses leftovers first...", POST)
    assert verify_quote("Bonus if it uses leftovers first…", POST)


def test_ellipsis_fragments_must_be_in_order():
    assert not verify_quote("what I already have ... I keep buying the same things", POST)


def test_trailing_punctuation_and_wrapping_quotes_are_ignored():
    assert verify_quote('"Is there an app that plans the week around what I already have."', POST)


def test_words_not_in_the_post_fail():
    assert not verify_quote("an app that plans the month around what I already have", POST)
    assert not verify_quote("I love this app", POST)


def test_too_short_or_empty_fails():
    assert not verify_quote("", POST)
    assert not verify_quote(None, POST)
    assert not verify_quote("app", POST)
    assert not verify_quote("…", POST)


def test_locate_returns_the_original_span():
    span = locate_quote("AN APP that plans the week around what i already have", POST)
    assert span is not None
    assert POST[span[0] : span[1]] == "an app that plans the week around what I already have"


def test_locate_spans_ellipsis():
    text = "Half my groceries are compost by Friday. It's embarrassing, honestly."
    span = locate_quote("Half my groceries … honestly", text)
    assert span is not None
    assert (
        text[span[0] : span[1]]
        == "Half my groceries are compost by Friday. It's embarrassing, honestly"
    )


def test_normalize_dashes_and_nbsp():
    assert normalize("leftovers first — always") == "leftovers first - always"
