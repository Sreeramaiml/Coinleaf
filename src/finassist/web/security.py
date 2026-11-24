"""Local-only access control for the GUI (ARCH §12.3).

- The server binds to 127.0.0.1 only (see web/server.py).
- Host header check: only the exact local host:port is accepted, which defeats DNS
  rebinding (a web page on the internet pointing its own domain at 127.0.0.1).
- Password sign-in: every page needs a signed-in session (an HttpOnly, SameSite=Strict
  cookie with a random id). Sessions end after 15 minutes without use, on "Lock", and when
  the app stops. Wrong passwords are slowed down (web/auth.py).
- First-time setup needs the launch link: creating the password is only allowed in a
  browser that opened the per-launch URL printed in the terminal (a random token exchanged
  for a cookie), so nobody else on the computer can claim the app first.
- CSRF: every POST must carry the page's CSRF token in the X-CSRF-Token header.
- Strict Content-Security-Policy: scripts, styles and requests only from this server.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from finassist.config.password import PasswordStore
from finassist.web.auth import Sessions, Throttle

__all__ = ["COOKIE", "LAUNCH_COOKIE", "Guard", "GuardMiddleware"]

COOKIE = "finassist_session"  # signed-in session
LAUNCH_COOKIE = "finassist_launch"  # this browser opened the launch link (needed for first-time setup)
PUBLIC = ("/static/",)
SIGN_IN_PAGES = ("/login", "/setup")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "font-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'"
)


class Guard:
    def __init__(self, allowed_hosts: set[str], password_file: Path, token: str | None = None) -> None:
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.token = token or secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.passwords = PasswordStore(password_file)
        self.sessions = Sessions()
        self.throttle = Throttle()

    def launch_path(self) -> str:
        return f"/?t={self.token}"

    def launched(self, request: Request) -> bool:
        c = request.cookies.get(LAUNCH_COOKIE, "")
        return bool(c) and secrets.compare_digest(c, self.token)

    def signed_in(self, request: Request, *, touch: bool = True) -> bool:
        return self.sessions.valid(request.cookies.get(COOKIE), touch=touch)

    def entry_after_launch(self) -> str:
        """Where the launch link leads a signed-out browser: setup the first time, then sign-in."""
        return "/login" if self.passwords.is_set() else "/setup"

    def entry(self, request: Request) -> str:
        """Where a signed-out browser should go."""
        if not self.passwords.is_set():
            return "/setup" if self.launched(request) else "/login"
        return "/login"


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, guard: Guard) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.guard = guard

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        g = self.guard
        path = request.url.path
        host = (request.headers.get("host") or "").lower()
        if host not in g.allowed_hosts:
            return PlainTextResponse("Bad host.", status_code=400)
        if path.startswith(PUBLIC):
            return self._headers(await call_next(request))
        if path == "/" and "t" in request.query_params:
            if not secrets.compare_digest(request.query_params["t"], g.token):
                return self._headers(RedirectResponse("/login?expired=1", status_code=303))
            target = "/dashboard" if g.signed_in(request) else g.entry_after_launch()
            resp: Response = RedirectResponse(target, status_code=303)
            resp.set_cookie(LAUNCH_COOKIE, g.token, httponly=True, samesite="strict", path="/")
            return self._headers(resp)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("x-csrf-token", "")
            if not sent or not secrets.compare_digest(sent, g.csrf):
                return self._headers(PlainTextResponse("Missing or wrong CSRF token.", status_code=403))
        if path in SIGN_IN_PAGES or g.signed_in(request):
            return self._headers(await call_next(request))
        target = g.entry(request)
        if request.method == "GET":
            return self._headers(RedirectResponse(target, status_code=303))
        # A form posted from a page that has since locked: tell the page script where to go.
        resp = PlainTextResponse("Coinleaf is locked. Sign in again.", status_code=401)
        resp.headers["X-Redirect"] = target + "?locked=1"
        return self._headers(resp)

    @staticmethod
    def _headers(resp: Response) -> Response:
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp
