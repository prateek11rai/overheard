import base64
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """A URL slug from a product name.

    Args:
        name: Product name.

    Returns:
        Lowercase words joined by dashes (or `product`).
    """
    return SLUG_RE.sub("-", name.lower()).strip("-")[:40] or "product"


def safe_url(url: str | None) -> str:
    """Only http(s) links reach the page; anything else becomes `#`.

    Args:
        url: A link from a post.

    Returns:
        The link, or `#`.
    """
    if not url:
        return "#"
    scheme = urlsplit(url).scheme.lower()
    return url if scheme in ("http", "https") else "#"


def parse_emails(raw: str) -> list[str]:
    """Split and check email addresses typed in a form.

    Args:
        raw: Addresses separated by commas, semicolons or spaces.

    Returns:
        The valid addresses, in order, without duplicates.
    """
    out: list[str] = []
    for part in re.split(r"[\s,;]+", raw or ""):
        if part and EMAIL_RE.match(part) and part not in out:
            out.append(part)
    return out[:10]


class SecurityMiddleware(BaseHTTPMiddleware):
    """Password (when set), same-origin POSTs and security headers."""

    def __init__(self, app: Any, password: str | None) -> None:
        """Wrap the app.

        Args:
            app: The ASGI app.
            password: `OVERHEARD_PASSWORD`, or None on localhost.
        """
        super().__init__(app)
        self.password = password

    def _authorized(self, request: Request) -> bool:
        if not self.password or request.url.path == "/healthz":
            return True
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
        except ValueError:
            return False
        _, _, given = decoded.partition(":")
        return secrets.compare_digest(given.encode(), self.password.encode())

    @staticmethod
    def _same_origin(request: Request) -> bool:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            if not referer:
                return False
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc == host

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Check the request, then add security headers to the response.

        Args:
            request: The request.
            call_next: The next handler.

        Returns:
            The response.
        """
        if not self._authorized(request):
            return Response(
                "Password required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="overheard"'},
            )
        if request.method not in ("GET", "HEAD", "OPTIONS") and not self._same_origin(request):
            return Response("Cross-origin request refused", status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response
