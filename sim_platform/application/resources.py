"""Root ownership and execution exclusivity; contains no trading behavior."""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Dict, Optional, Protocol, Tuple, TypeVar
from sim_platform.domain.models import ContractError
from sim_platform.resources import cleanup, cleanup_after_error

if TYPE_CHECKING:  # Annotation only.
    from sim_platform.execution.state import ExecutionState


class Closeable(Protocol):
    """Anything Platform can register as a root resource."""

    def close(self) -> None: ...


R = TypeVar('R', bound=Closeable)


class PlatformResources:
    """Platform's registry of open root resources and of runs with an execution owner.

    Platform owns one for its lifetime. close() closes 'shutdown first' roots
    (controllers), then the rest in reverse order, and keeps them registered if an
    execution owner is still alive so close can be retried.
    """

    def __init__(self) -> None:
        # Root resources by id(), with their shutdown-first flag.
        self._roots: Dict[int, Tuple[Closeable, bool]] = {}
        self._executions: Dict[str, object] = {}
        self._runtimes: Dict[str, Optional[ExecutionState]] = {}
        self._lock = RLock()
        self.closed = False

    def check_open(self) -> None:
        if self.closed:
            raise ContractError('platform is closed')

    @property
    def has_live_execution(self) -> bool:
        with self._lock:
            return bool(self._executions)

    @property
    def has_roots(self) -> bool:
        with self._lock:
            return bool(self._roots)

    def add(self, resource: R, *, shutdown_first: bool = False) -> R:
        """Register a root (returned unchanged); close it and raise if Platform is closed."""
        with self._lock:
            if not self.closed:
                self._roots[id(resource)] = (resource, shutdown_first)
                return resource
        error = ContractError('platform is closed')
        cleanup_after_error(error, resource.close)
        raise error

    def remove(self, resource: Closeable) -> None:
        with self._lock:
            self._roots.pop(id(resource), None)

    def has_execution(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._executions

    def claim_execution(self, run_id: str, owner: object) -> None:
        with self._lock:
            self.check_open()
            if run_id in self._executions:
                raise ContractError('run already has an execution owner')
            self._executions[run_id] = owner

    def release_execution(self, run_id: str, owner: object) -> None:
        with self._lock:
            if self._executions.get(run_id) is owner:
                del self._executions[run_id]

    def runtime(self, run_id: str) -> Optional[ExecutionState]:
        """The run's resident ExecutionState, if it has one."""
        with self._lock:
            return self._runtimes.get(run_id)

    def register_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        with self._lock:
            self.check_open()
            if run_id in self._runtimes:
                raise ContractError('run already has a runtime')
            self._runtimes[run_id] = runtime

    def unregister_runtime(self, run_id: str, runtime: Optional[ExecutionState]) -> None:
        with self._lock:
            if self._runtimes.get(run_id) is runtime:
                self._runtimes.pop(run_id, None)

    def close(self) -> None:
        with self._lock:
            self.closed = True
            first = tuple(resource for resource, priority in self._roots.values() if priority)

        def close_resource(resource: Closeable) -> None:
            resource.close()
            self.remove(resource)

        def close_roots() -> None:
            # Jobs may finish and unregister child controllers while being joined.
            with self._lock:
                if self._executions and any(priority for _, priority in self._roots.values()):
                    raise ContractError(
                        'execution owner is still alive; retain its resources for close retry'
                    )
                roots = tuple(
                    resource
                    for resource, priority in reversed(tuple(self._roots.values()))
                    if not priority
                )
            cleanup(*(lambda resource=resource: close_resource(resource) for resource in roots))

        cleanup(
            lambda: cleanup(
                *(lambda resource=resource: close_resource(resource) for resource in first)
            ),
            close_roots,
        )
