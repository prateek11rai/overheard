import re
import unicodedata

MIN_QUOTE_CHARS = 8
MIN_FRAGMENT_WORDS = 3
MAX_FRAGMENTS = 3
MAX_GAP = 300
CONTEXT_CHARS = 280

_TRANSLATE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "´": "'",
        "`": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "−": "-",
    }
)
_PUNCT = set(",.;:!?)")
_EDGE = " \"'.,;:!?-"


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise text for quote matching and keep where each character came from."""
    out: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_TRANSLATE).casefold():
            if c.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    where.append(i)
                continue
            if c in _PUNCT and out and out[-1] == " ":
                out.pop()
                where.pop()
            out.append(c)
            where.append(i)
    if out and out[-1] == " ":
        out.pop()
        where.pop()
    return "".join(out), where


def normalize(text: str) -> str:
    """Normalise text the way quote checks compare it.

    Ignores case, spacing, curly versus straight quotes and dash styles; turns `…` into `...`.

    Args:
        text: Any text.

    Returns:
        The normalised text.
    """
    return _normalize_with_map(text)[0]


def _fragments(quote: str) -> list[str]:
    q = normalize(quote).strip(_EDGE)
    return [f.strip(_EDGE) for f in re.split(r"\.{3,}", q) if f.strip(_EDGE)]


def _pattern(fragment: str) -> re.Pattern[str]:
    head = r"(?<!\w)" if re.match(r"\w", fragment) else ""
    tail = r"(?!\w)" if re.search(r"\w$", fragment) else ""
    return re.compile(head + re.escape(fragment) + tail)


def _chain(norm: str, patterns: list[re.Pattern[str]], start: int, first: bool) -> int | None:
    """End offset of the remaining fragments matched in order, close together."""
    if not patterns:
        return start
    pattern, rest = patterns[0], patterns[1:]
    for m in pattern.finditer(norm, start):
        if not first and m.start() - start > MAX_GAP:
            return None
        end = _chain(norm, rest, m.end(), False)
        if end is not None:
            return end
        if not first:
            return None
    return None


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Find a quote in a text, allowing `...` or `…` for left-out words.

    Matches start and end on word boundaries. A quote has at most three fragments, each of
    at least three words, in order and within 300 characters of each other, so a few
    words picked from all over the post never pass.

    Args:
        quote: The quote the model returned.
        text: The post text.

    Returns:
        (start, end) offsets of the quoted span in `text`, or None.
    """
    frags = _fragments(quote)
    if not frags or len(frags) > MAX_FRAGMENTS or sum(len(f) for f in frags) < MIN_QUOTE_CHARS:
        return None
    if any(len(re.findall(r"\w+", f)) < MIN_FRAGMENT_WORDS for f in frags):
        return None
    norm, where = _normalize_with_map(text)
    patterns = [_pattern(f) for f in frags]
    for m in patterns[0].finditer(norm):
        end = _chain(norm, patterns[1:], m.end(), False)
        if end is not None:
            return where[m.start()], where[end - 1] + 1
    return None


def verify_quote(quote: str | None, text: str) -> bool:
    """Check that a quote really is in the post.

    Ignores case, spacing, quote styles and dash styles; `...` or `…` may stand for left-out
    words. Very short quotes never pass.

    Args:
        quote: The quote the model returned.
        text: The post text (title and body).

    Returns:
        Whether the quote is in the text.
    """
    return bool(quote) and locate_quote(quote or "", text) is not None


def post_text(title: str, body: str, is_comment: bool = False) -> str:
    """The text a label's quote must come from.

    A Hacker News comment's title is its thread's title, which the commenter didn't write,
    so a comment's quote must come from the comment itself.

    Args:
        title: Post title (for a comment, the thread's title).
        body: Post body or snippet.
        is_comment: Whether the post is a comment.

    Returns:
        Title and body together, or only the body for a comment.
    """
    return body.strip() if is_comment else f"{title}\n{body}".strip()


def safe_filename(name: str) -> str:
    """A file-name-safe version of a slug.

    Args:
        name: Any text.

    Returns:
        Letters, digits and dashes only.
    """
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "digest"
