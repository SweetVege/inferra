"""Credentials at rest: Fernet encryption keyed from the service secret."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from inferra.config import get_settings
from inferra.datasources.base import QueryError


def _fernet() -> Fernet:
    digest = hashlib.sha256((get_settings().secret_key + ":connections").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise QueryError("Stored credentials can no longer be decrypted; re-enter them.") from exc
