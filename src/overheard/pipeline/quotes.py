import re
import unicodedata

MIN_QUOTE_CHARS = 8
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


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Find a quote in a text, allowing `...` or `…` for left-out words.

    Args:
        quote: The quote the model returned.
        text: The post text.

    Returns:
        (start, end) offsets of the quoted span in `text`, or None.
    """
    frags = _fragments(quote)
    if not frags or sum(len(f) for f in frags) < MIN_QUOTE_CHARS:
        return None
    norm, where = _normalize_with_map(text)
    pos, first = 0, None
    last_end = 0
    for f in frags:
        i = norm.find(f, pos)
        if i < 0:
            return None
        if first is None:
            first = i
        pos = last_end = i + len(f)
    if first is None:
        return None
    return where[first], where[last_end - 1] + 1


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


def post_text(title: str, body: str) -> str:
    """The text a label's quote must come from.

    Args:
        title: Post title.
        body: Post body or snippet.

    Returns:
        Title and body together.
    """
    return f"{title}\n{body}".strip()


def safe_filename(name: str) -> str:
    """A file-name-safe version of a slug.

    Args:
        name: Any text.

    Returns:
        Letters, digits and dashes only.
    """
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "digest"
