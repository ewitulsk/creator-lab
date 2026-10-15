"""Google OAuth 2.0 (web flow with PKCE) for the channel owner, plus token storage.

The refresh token is encrypted with Fernet, keyed from SECRET_KEY, before it touches the
database. Tokens are never logged. Access tokens live in memory only.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import secrets
import threading
import time
import urllib.parse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from ..config import is_set
from ..extensions import db
from ..models import OAuthState, OwnerCredential, utcnow

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]
STATE_TTL = dt.timedelta(minutes=15)

_transport: httpx.BaseTransport | None = None  # tests inject a mock
_tokens: dict[int, tuple[str, float]] = {}  # credential id -> (access token, expiry epoch)
_lock = threading.Lock()


class OAuthError(RuntimeError):
    pass


class ReauthRequired(OAuthError):
    """The refresh token no longer works (expired after 7 days in Testing mode, or revoked)."""


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport
    with _lock:
        _tokens.clear()


def http() -> httpx.Client:
    return httpx.Client(timeout=30, transport=_transport)


def settings() -> tuple[str, str, str]:
    cfg = current_app.config["APP"]
    if not (is_set(cfg.google_client_id) and is_set(cfg.google_client_secret) and is_set(cfg.google_redirect_uri)):
        raise OAuthError(
            "Google OAuth is not configured: set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET and "
            "GOOGLE_OAUTH_REDIRECT_URI in backend/.env (see docs/youtube-oauth.md)"
        )
    return cfg.google_client_id, cfg.google_client_secret, cfg.google_redirect_uri


def is_configured() -> bool:
    try:
        settings()
        return True
    except OAuthError:
        return False


# ------------------------------------------------------------------ encryption


def _fernet() -> Fernet:
    secret = current_app.config["SECRET_KEY"]
    key = base64.urlsafe_b64encode(hashlib.sha256(b"creator-lab-oauth:" + secret.encode()).digest())
    return Fernet(key)


def encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt(blob: str) -> str:
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken as err:
        raise ReauthRequired("stored token can't be decrypted (SECRET_KEY changed?); sign in again") from err


# ------------------------------------------------------------------ the flow


def start(created_by: str, return_to: str) -> str:
    """Create a single-use state (with a PKCE verifier) and return Google's consent URL."""
    client_id, _secret, redirect_uri = settings()
    cutoff = utcnow() - STATE_TTL
    db.session.execute(db.delete(OAuthState).where(OAuthState.created_at < cutoff))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.session.add(OAuthState(state=state, code_verifier=verifier, created_by=created_by, return_to=return_to))
    db.session.commit()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def take_state(state: str) -> OAuthState:
    row = db.session.get(OAuthState, state or "")
    if row is None or utcnow() - row.created_at > STATE_TTL:
        raise OAuthError("sign-in link expired or was already used; start again from Settings")
    db.session.delete(row)
    db.session.commit()
    return row


def exchange_code(code: str, verifier: str) -> dict:
    client_id, client_secret, redirect_uri = settings()
    with http() as h:
        resp = h.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if resp.status_code != 200:
        raise OAuthError(f"Google refused the sign-in code: {_err(resp)}")
    data = resp.json()
    if not data.get("refresh_token"):
        raise OAuthError(
            "Google returned no refresh token; remove creator-lab's access in your Google account and retry"
        )
    granted = set((data.get("scope") or "").split())
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        raise OAuthError("these permissions were not granted: " + ", ".join(m.rsplit("/", 1)[-1] for m in missing))
    return data


def _err(resp: httpx.Response) -> str:
    try:
        d = resp.json()
        return f"{d.get('error')}: {d.get('error_description', '')}".strip(": ")
    except Exception:
        return f"HTTP {resp.status_code}"


def access_token(cred: OwnerCredential) -> str:
    """A valid access token for this credential, refreshing it when needed."""
    with _lock:
        hit = _tokens.get(cred.id)
        if hit and hit[1] - 60 > time.time():
            return hit[0]
    if cred.status != "active":
        raise ReauthRequired(cred.status_detail or "sign in again")
    client_id, client_secret, _ = settings()
    with http() as h:
        resp = h.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": decrypt(cred.refresh_token_enc),
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        err = _err(resp)
        if "invalid_grant" in err:
            cred.status = "expired"
            cred.status_detail = (
                "Google says the sign-in expired or was revoked (Testing-mode tokens last 7 days). Sign in again."
            )
            db.session.commit()
            raise ReauthRequired(cred.status_detail)
        raise OAuthError(f"token refresh failed: {err}")
    data = resp.json()
    token = data["access_token"]
    with _lock:
        _tokens[cred.id] = (token, time.time() + float(data.get("expires_in", 3600)))
    cred.last_used_at = utcnow()
    db.session.commit()
    return token


def revoke(cred: OwnerCredential) -> None:
    try:
        token = decrypt(cred.refresh_token_enc)
        with http() as h:
            h.post(REVOKE_URL, data={"token": token})
    except Exception as err:  # revoking is best effort; the row is deleted either way
        log.warning("revoke failed: %s", type(err).__name__)
    with _lock:
        _tokens.pop(cred.id, None)
