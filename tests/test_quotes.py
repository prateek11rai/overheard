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
    span = locate_quote("Half my groceries … It's embarrassing, honestly", text)
    assert span is not None
    assert (
        text[span[0] : span[1]]
        == "Half my groceries are compost by Friday. It's embarrassing, honestly"
    )


def test_normalize_dashes_and_nbsp():
    assert normalize("leftovers first — always") == "leftovers first - always"


def test_stitched_single_words_fail():
    assert not verify_quote("I...need...app", "I really do not need a new app")
    assert not verify_quote("I … need … a new app", "I really do not need a new app")


def test_every_fragment_needs_three_words():
    text = "I keep buying the same things and throwing half of it away."
    assert verify_quote("I keep buying ... throwing half of", text)
    assert not verify_quote("I keep buying ... away", text)


def test_fragments_must_be_close_and_few():
    filler = " ".join(["word"] * 80)
    text = f"The first part is here. {filler} The second part is here."
    assert not verify_quote("The first part ... The second part", text)
    near = "one two three alpha four five six beta seven eight nine gamma ten eleven twelve"
    assert verify_quote("one two three ... four five six ... seven eight nine", near)
    assert not verify_quote(
        "one two three ... four five six ... seven eight nine ... ten eleven twelve", near
    )


def test_matches_start_and_end_on_word_boundaries():
    text = "We need an application that plans meals for us."
    assert locate_quote("ication that plans meals", text) is None
    assert locate_quote("an application that pla", text) is None
    span = locate_quote("an application that plans", text)
    assert span is not None and text[span[0] : span[1]] == "an application that plans"
    assert text[span[0] - 1] == " "
