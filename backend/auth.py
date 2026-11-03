"""Server-side sessions: a signed, HttpOnly cookie checked on every /api/* call.

One shared account for the team: LOGIN_USER (default "admin") + LOGIN_PASSWORD from the env.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import Request

COOKIE = "sb_session"
MAX_AGE = 30 * 24 * 3600
PUBLIC_PATHS = ("/api/auth/", "/static/", "/health")


def _secret() -> bytes:
    """SESSION_SECRET from env, else a random one persisted next to the database (survives restarts)."""
    env = os.getenv("SESSION_SECRET")
    if env:
        return env.encode()
    from database import DATABASE_URL
    base = Path(DATABASE_URL.replace("sqlite:///", "")).parent if DATABASE_URL.startswith("sqlite") else Path(".")
    f = base / ".session_secret"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip().encode()


_KEY = None


def _key() -> bytes:
    global _KEY
    if _KEY is None:
        _KEY = _secret()
    return _KEY


def issue(name: str) -> str:
    payload = {"email": name, "name": name, "ae": name, "exp": int(time.time()) + MAX_AGE}
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    sig = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read(token: Optional[str]) -> Optional[Dict]:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    return data if data.get("exp", 0) > time.time() else None


def current_user(request: Request) -> Optional[Dict]:
    return read(request.cookies.get(COOKIE))


def set_cookie(response, token: str, request: Request):
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, token, max_age=MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")


def check_login(username: str, password: str) -> bool:
    user = os.getenv("LOGIN_USER", "admin")
    expected = os.getenv("LOGIN_PASSWORD")
    if not expected:
        return False
    ok_user = hmac.compare_digest((username or "").strip().lower().encode(), user.lower().encode())
    ok_pw = hmac.compare_digest((password or "").encode(), expected.encode())
    return ok_user and ok_pw


# tiny per-IP throttle for the login form (nginx rate-limits too)
_attempts: Dict[str, list] = {}


def too_many_attempts(ip: str, limit: int = 8, window: int = 300) -> bool:
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < window]
    _attempts[ip] = hits
    return len(hits) >= limit


def record_failure(ip: str):
    _attempts.setdefault(ip, []).append(time.time())
