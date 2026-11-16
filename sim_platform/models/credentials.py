"""Explicit session or OS-store credentials; no plaintext fallback or logging."""

from __future__ import annotations

from threading import RLock
from typing import Any, Dict, Optional, Protocol

from sim_platform.domain.models import ContractError, label

from .contracts import REFERENCE_MAX_LENGTH

# Longest accepted credential, in characters.
MAX_SECRET_LENGTH = 4096


class CredentialBackend(Protocol):
    """Persistent OS credential storage (for example MacOSKeychain)."""

    def put(self, reference: str, secret: str) -> None: ...

    def get(self, reference: str) -> Any: ...

    def delete(self, reference: str) -> None: ...


def validate_secret(secret: Any) -> str:
    """Return the secret if it is non-empty single-line text within the size bound."""
    if (
        type(secret) is not str
        or not secret
        or len(secret) > MAX_SECRET_LENGTH
        or '\r' in secret
        or '\n' in secret
    ):
        raise ContractError('invalid credential material')
    return secret


class CredentialStore:
    """Credentials by reference: kept in memory for the session, or in the OS backend.

    Owned by Platform for its lifetime; close() forgets session secrets. A session
    entry shadows the backend for the same reference. Thread-safe.
    """

    def __init__(self, backend: Optional[CredentialBackend] = None) -> None:
        self.backend = backend
        self._session: Dict[str, str] = {}
        self._lock = RLock()

    def put(self, reference: str, secret: str, *, persistence: str = 'session') -> None:
        """Store in memory ('session') or the OS backend ('os')."""
        label(reference, 'credential reference', REFERENCE_MAX_LENGTH)
        validate_secret(secret)
        if persistence == 'session':
            with self._lock:
                self._session[reference] = secret
        elif persistence == 'os' and self.backend is not None:
            self.backend.put(reference, secret)
            with self._lock:
                self._session.pop(reference, None)
        else:
            raise ContractError('requested credential backend is unavailable')

    def get(self, reference: str) -> str:
        label(reference, 'credential reference', REFERENCE_MAX_LENGTH)
        with self._lock:
            if reference in self._session:
                return self._session[reference]
        secret = self.backend.get(reference) if self.backend is not None else None
        if secret is None:
            raise ContractError('credential not configured')
        return validate_secret(secret)

    def delete(self, reference: str) -> None:
        label(reference, 'credential reference', REFERENCE_MAX_LENGTH)
        with self._lock:
            if reference in self._session:
                del self._session[reference]
                return
        if self.backend is None:
            raise ContractError('credential backend is unavailable')
        self.backend.delete(reference)

    def close(self) -> None:
        with self._lock:
            self._session.clear()
