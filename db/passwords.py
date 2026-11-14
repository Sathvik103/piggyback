"""Password hashing with scrypt from the standard library (no extra dependency).

Stored format: scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>. Parameters are kept in
the string, so they can be raised later without breaking existing hashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2**14, 8, 1, 32  # ~16 MiB, tens of ms per hash
MIN_LENGTH = 10


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"password must be at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        want_b = base64.b64decode(want)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(want_b))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want_b)


# Checked against when the username doesn't exist, so a wrong username takes as long
# as a wrong password and the response time doesn't reveal which accounts exist.
_DUMMY = hash_password(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> bool:
    verify_password(password, _DUMMY)
    return False
