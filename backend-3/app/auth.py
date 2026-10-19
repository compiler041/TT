"""
auth.py – Simple token-based authentication for the multi-department system.

Tokens are in-memory (dict). Sufficient for single-instance deployment.
Token format: "{dept_id}:{timestamp}:{hmac_signature}"
"""

import hmac
import hashlib
import time
import secrets

# Secret key for signing tokens. Regenerated on every server restart (by design).
_SECRET = secrets.token_hex(32)

# Active sessions: token -> dept_id
_sessions: dict[str, str] = {}


def create_token(dept_id: str) -> str:
    ts = str(int(time.time()))
    data = f"{dept_id}:{ts}"
    sig = hmac.new(_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()[:16]
    token = f"{data}:{sig}"
    _sessions[token] = dept_id
    return token


def verify_token(token: str) -> str | None:
    """Return dept_id if token is valid, else None."""
    return _sessions.get(token)


def revoke_token(token: str) -> None:
    _sessions.pop(token, None)


def get_token_from_request(request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None
