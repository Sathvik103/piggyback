"""Sign-in, sessions and roles.

- `POST /auth/login` checks the password (scrypt, db/passwords.py) and sets an
  HttpOnly, SameSite=Lax session cookie. Only the token's hash is stored.
- Every route needs a session except health, the published benchmark, the static
  web app and these /auth routes. Viewers can read; changing anything (deciding,
  planning, creating scenarios) needs the operator role.
- Changes must come from the app's own origin (or a local dev server): a request
  that carries a foreign `Origin` is refused, on top of SameSite cookies.
- Failed sign-ins are throttled per client and username.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from db.repo import SESSION_HOURS

COOKIE = "pb_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_EXACT = {"/", "/health", "/benchmark", "/dashboard/", "/openapi.json", "/docs", "/redoc"}
PUBLIC_PREFIXES = ("/auth/", "/app/", "/docs/")
LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
MAX_FAILURES, FAILURE_WINDOW_S = 5, 600


class Login(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=256)


class Throttle:
    """At most `limit` failures per key in `window_s`."""

    def __init__(self, limit: int = MAX_FAILURES, window_s: float = FAILURE_WINDOW_S):
        self.limit, self.window_s = limit, window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and q[0] < now - self.window_s:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until another attempt is allowed (0 = now)."""
        now = time.monotonic()
        with self._lock:
            q = self._recent(key, now)
            return int(q[0] + self.window_s - now) + 1 if len(q) >= self.limit else 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key, time.monotonic()).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:  # not sent by a browser (curl, scripts) or same-origin GET-like navigation
        return True
    return urlsplit(origin).netloc == request.headers.get("host") or bool(LOCAL_ORIGIN.match(origin))


def guard(repo_of, request: Request) -> None:
    """Global dependency: who is asking, and may they do this?"""
    if request.method not in SAFE_METHODS and not _same_origin(request):
        raise HTTPException(403, "cross-site request refused")
    if is_public(request.url.path):
        return
    token = request.cookies.get(COOKIE)
    user = repo_of().session_user(token) if token else None
    if user is None:
        raise HTTPException(401, "sign in required")
    request.state.user = user
    if request.method not in SAFE_METHODS and user["role"] != "operator":
        raise HTTPException(403, "read-only access: only operators can change things")


def router(repo_of) -> APIRouter:
    r = APIRouter(prefix="/auth", tags=["auth"])
    throttle = Throttle()
    secure = os.environ.get("PIGGYBACK_SECURE_COOKIES") == "1"

    @r.post("/login")
    def login(body: Login, request: Request, response: Response):
        key = f"{request.client.host if request.client else '?'}|{body.username.lower()}"
        wait = throttle.retry_after(key)
        if wait:
            raise HTTPException(429, "too many failed sign-ins; try again later", headers={"Retry-After": str(wait)})
        user = repo_of().authenticate(body.username.lower(), body.password)
        if user is None:
            throttle.fail(key)
            raise HTTPException(401, "wrong username or password")
        throttle.clear(key)
        token = repo_of().create_session(user["username"])
        response.set_cookie(COOKIE, token, max_age=int(SESSION_HOURS * 3600), httponly=True, samesite="lax",
                            secure=secure or request.url.scheme == "https", path="/")
        return user

    @r.post("/logout", status_code=204)
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        if token:
            repo_of().end_session(token)
        response.delete_cookie(COOKIE, path="/")

    @r.get("/me")
    def me(request: Request):
        token = request.cookies.get(COOKIE)
        user = repo_of().session_user(token) if token else None
        if user is None:
            raise HTTPException(401, "sign in required")
        return user

    return r


def current_user(request: Request) -> dict:
    return request.state.user


__all__ = ["COOKIE", "Throttle", "guard", "router", "current_user", "is_public"]
