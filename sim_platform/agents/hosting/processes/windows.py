"""Parent-owned Windows Job Object; ordinary child processes inherit membership."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:  # Annotation only.
    from multiprocessing.process import BaseProcess
    from subprocess import Popen

    ManagedProcess = Union[BaseProcess, 'Popen[bytes]']

SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
# The Windows-only ctypes names (WinDLL, WinError, get_last_error) are read through
# this alias; mypy checks with this machine's stubs, which define them only on Windows.
windows: Any = ctypes
# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JobObjectExtendedLimitInformation, and the
# PROCESS_SET_QUOTA | PROCESS_TERMINATE access needed to assign a process.
KILL_ON_JOB_CLOSE = 0x2000
EXTENDED_LIMIT_INFORMATION = 9
ASSIGN_ACCESS = 0x0100 | 0x0001


class BasicLimits(ctypes.Structure):
    """JOBOBJECT_BASIC_LIMIT_INFORMATION."""

    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', SIZE_T),
        ('MaximumWorkingSetSize', SIZE_T),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ULONG_PTR),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    """IO_COUNTERS."""

    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            'ReadOperationCount',
            'WriteOperationCount',
            'OtherOperationCount',
            'ReadTransferCount',
            'WriteTransferCount',
            'OtherTransferCount',
        )
    ]


class ExtendedLimits(ctypes.Structure):
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

    _fields_ = [
        ('BasicLimitInformation', BasicLimits),
        ('IoInfo', IoCounters),
        ('ProcessMemoryLimit', SIZE_T),
        ('JobMemoryLimit', SIZE_T),
        ('PeakProcessMemoryUsed', SIZE_T),
        ('PeakJobMemoryUsed', SIZE_T),
    ]


class WindowsProcessFamily:
    """A Windows Job Object that kills every member process when it is closed.

    The host (or a preparation command) creates one per process tree; close()
    releases the job handle, which ends any remaining members.
    """

    name = 'windows_job_object'

    # Job object handle, set in __init__ and cleared by close().
    _handle: Any

    def close(self) -> None:
        if self._handle:
            if not self._api.CloseHandle(self._handle):
                raise windows.WinError(windows.get_last_error())
            self._handle = None

    def __init__(self) -> None:
        # WinDLL and WinError exist only on Windows; mypy checks against this machine's stubs.
        self._api: Any = windows.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self._api, name)
            function.argtypes, function.restype = arguments, result
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise windows.WinError(windows.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE
        if not self._api.SetInformationJobObject(
            self._handle, EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = windows.WinError(windows.get_last_error())
            self.close()
            raise error

    def attach(self, process: ManagedProcess) -> None:
        """Assign before the child launch gate opens; fail rather than lose ownership."""
        handle = self._api.OpenProcess(ASSIGN_ACCESS, False, process.pid)
        if not handle:
            raise windows.WinError(windows.get_last_error())
        try:
            if not self._api.AssignProcessToJobObject(self._handle, handle):
                raise windows.WinError(windows.get_last_error())
        finally:
            self._api.CloseHandle(handle)

    def confirm(self) -> None:
        """Windows attachment is already complete before the child launch gate opens."""

    def terminate(self, process: BaseProcess, *, force: bool = False) -> None:
        # Cooperative grace is managed by the host, not by a console-control signal.
        if not self._api.TerminateJobObject(self._handle, 1):
            raise windows.WinError(windows.get_last_error())
        if process.is_alive():
            process.kill() if force else process.terminate()
