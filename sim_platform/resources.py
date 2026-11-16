"""Release every owned resource, retaining the first failure for the caller."""

from __future__ import annotations

from typing import Any, Callable, TypeVar, cast

F = TypeVar('F', bound=Callable[..., Any])


def _note(error: BaseException, message: str) -> None:
    """Attach a cleanup message to ``error`` without replacing it."""
    if hasattr(error, 'add_note'):
        error.add_note(message)
    else:  # Python 3.9/3.10 support, without replacing the original exception.
        cast(Any, error).cleanup_errors = getattr(error, 'cleanup_errors', ()) + (message,)


def cleanup(*actions: Callable[[], Any]) -> None:
    """Run every action; re-raise the first failure with later ones noted on it."""
    failure = None
    for action in actions:
        try:
            action()
        except Exception as exc:
            if failure is None:
                failure = exc
            else:
                _note(failure, f'Additional cleanup failure: {exc}')
    if failure is not None:
        raise failure


def cleanup_after_error(error: BaseException, *actions: Callable[[], Any]) -> None:
    """Run cleanup after ``error``; cleanup failures are noted on ``error``, not raised."""
    try:
        cleanup(*actions)
    except Exception as exc:
        _note(error, f'Cleanup failure: {exc}')


def serialized(method: F) -> F:
    """Controls, decisions and generation-bound handles share the owner's lock."""
    from functools import wraps

    @wraps(method)
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self.control_boundary():
            return method(self, *args, **kwargs)

    return cast(F, call)
