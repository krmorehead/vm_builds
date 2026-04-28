"""Callhome HMAC-based authentication — key generation and validation.

Pure crypto with no external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

CALLHOME_HMAC_MSG = b"vm_builds_callhome"


def generate_callhome_keys() -> tuple[str, str]:
    """Generate a (private_key, public_key) pair for call-home auth.

    The private key stays on the management server. The public key
    (derived via HMAC-SHA256) is distributed to fleet nodes.
    """
    private_key = secrets.token_hex(32)
    public_key = derive_public_key(private_key)
    return private_key, public_key


def derive_public_key(private_key: str) -> str:
    """Derive the public key from a private key."""
    return hmac.new(
        private_key.encode(), CALLHOME_HMAC_MSG, hashlib.sha256,
    ).hexdigest()


def validate_callhome_token(token: str, private_key: str) -> bool:
    """Check whether a presented token matches the server's private key."""
    if not token or not private_key:
        return False
    expected = derive_public_key(private_key)
    return hmac.compare_digest(token, expected)
