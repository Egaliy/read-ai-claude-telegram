from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Optional

from app.kv import ProcessedMeetingsStore

__all__ = ["ProcessedMeetingsStore", "verify_readai_signature"]


def _signing_key_bytes(signing_key: str) -> bytes:
    key = signing_key.strip()
    try:
        return base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError):
        return key.encode("utf-8")


def verify_readai_signature(body: bytes, signature: Optional[str], signing_key: str) -> bool:
    if not signing_key:
        return True
    if not signature:
        return False

    key_bytes = _signing_key_bytes(signing_key)
    expected = hmac.new(key_bytes, body, hashlib.sha256).hexdigest()
    received = signature.removeprefix("sha256=").strip()

    if hmac.compare_digest(expected, received):
        return True

    # Fallback: some setups store the raw key string instead of base64.
    fallback = hmac.new(signing_key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(fallback, received)
