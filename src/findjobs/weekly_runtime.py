"""Weekly workflow runtime: process lock, tee logging, collect result, summary.

Standard-library only.  No adapters, models, or database logic.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LockHeldError(Exception):
    """The process lock is held by another running instance."""

    def __init__(self, owner_info: dict) -> None:
        self.owner_info = owner_info
        pid = owner_info.get("pid", "?")
        start = owner_info.get("start_time", "?")
        log = owner_info.get("log_path", "?")
        msg = (
            f"Weekly workflow already running (PID {pid}, started at {start})"
            f"\n  log: {log}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# PID validation
# ---------------------------------------------------------------------------

_VALID_TYPES = (int,)


def validate_pid(pid: object) -> int | None:
    """Return *pid* if it is a positive integer, else None.

    Rejects ``bool``, negative/zero values, floats, strings and ``None``.
    """
    if isinstance(pid, bool):
        return None
    if isinstance(pid, _VALID_TYPES) and pid > 0:
        return pid
    return None


# ---------------------------------------------------------------------------
# Injectable PID probe (for cross-platform tests)
# ---------------------------------------------------------------------------

_pid_alive_fn: Callable[[int], bool] | None = None


def _windows_pid_alive(pid: int) -> bool:
    """Windows PID probe via ctypes kernel32.

    Uses ``OpenProcess`` / ``GetExitCodeProcess`` / ``CloseHandle``.
    ``ERROR_ACCESS_DENIED`` (5) is treated as alive.
    An invalid or non-existent PID returns False.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.GetLastError()
        # ERROR_ACCESS_DENIED means alive but cannot query detail.
        if err == 5:
            return True
        return False

    try:
        exit_code = wintypes.DWORD()
        success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not success:
            # GetExitCodeProcess itself failed, so the PID is unprobeable.
            # Treat as alive (conservative).
            return True
        # STILL_ACTIVE (259) means the process is running
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def _posix_pid_alive(pid: int) -> bool:
    """POSIX PID probe via ``os.kill(pid, 0)``.

    ``PermissionError`` is treated as alive (exists but not owned).
    ``ProcessLookupError`` means the process does not exist.
    """
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _default_pid_alive(pid: int) -> bool:
    """Return True iff *pid* refers to a live process on this host."""
    if platform.system() == "Windows":
        return _windows_pid_alive(pid)
    return _posix_pid_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    fn = _pid_alive_fn if _pid_alive_fn is not None else _default_pid_alive
    return fn(pid)


def set_pid_alive_fn(fn: Callable[[int], bool] | None) -> None:
    """Replace the PID-liveness probe (for tests).  Pass *None* to reset."""
    global _pid_alive_fn
    _pid_alive_fn = fn


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def resolve_lock_path(db_path: str | None, explicit_lock: str | None = None) -> Path:
    """Derive the lock file path.

    Prefers an explicit ``--lock-path`` when given, otherwise appends
    ``.weekly.lock`` to the full database filename.
    """
    if explicit_lock:
        return Path(explicit_lock)
    if db_path:
        p = Path(db_path)
        return p.with_name(p.name + ".weekly.lock")
    from findjobs.paths import get_default_db_path

    p = get_default_db_path()
    return p.with_name(p.name + ".weekly.lock")


def resolve_logs_dir(reports_dir: Path, explicit_logs: str | None = None) -> Path:
    """Derive the per-attempt logs directory."""
    if explicit_logs:
        return Path(explicit_logs)
    return reports_dir / "logs"


# ---------------------------------------------------------------------------
# ProcessLock
# ---------------------------------------------------------------------------


class ProcessLock:
    """Exclusive process lock via atomic file creation.

    Acquires by creating a unique lock file with ``O_CREAT | O_EXCL``.
    A stale lock (owner PID dead, or malformed file older than the
    threshold) is cleaned up before a single retry of exclusive creation.

    Thread-safe within a single process (only one OS-level file creation
    race at a time).
    """

    def __init__(
        self,
        lock_path: Path,
        db_path: Path,
        reports_dir: Path,
        log_path: Path,
        stale_hours: float = 24.0,
    ) -> None:
        self._lock_path = lock_path
        self._db_path = db_path
        self._reports_dir = reports_dir
        self._log_path = log_path
        self._stale_seconds = stale_hours * 3600.0
        self._token = str(uuid.uuid4())
        self._owned = False

    # -- public ------------------------------------------------

    @property
    def owned(self) -> bool:
        """Whether this instance currently holds the lock."""
        return self._owned

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> None:
        """Acquire the exclusive lock.

        Raises *LockHeldError* when the lock is held by a live owner or
        by a fresh malformed file.
        """
        lock_path = self._lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            self._handle_existing()
            # Retry exclusive creation after stale removal.
            try:
                fd = os.open(
                    str(lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
            except FileExistsError:
                try:
                    info = self._read_lock()
                except (json.JSONDecodeError, OSError):
                    info = {}
                raise LockHeldError(info)

        metadata = self._build_metadata()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False)
        except BaseException as write_exc:
            try:
                self._lock_path.unlink(missing_ok=True)
            except BaseException as cleanup_exc:
                raise RuntimeError(
                    "Lock metadata write failed and the incomplete lock "
                    f"could not be removed: {cleanup_exc}"
                ) from write_exc
            raise
        self._owned = True

    def release(self) -> bool:
        """Remove the lock file when it still belongs to this owner.

        Returns True when the lock was released or this instance did
        not hold it (no-op).  Returns False when the lock is held by a
        different token (preserved).

        Raises ``json.JSONDecodeError`` if the lock exists but is
        malformed.  Raises ``OSError`` if reading or unlinking fails.
        """
        if not self._owned:
            self._owned = False
            return True

        if self._lock_path.exists():
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
            if data.get("token") != self._token:
                self._owned = False
                return False
            self._lock_path.unlink(missing_ok=True)

        self._owned = False
        return True

    @staticmethod
    def read_lock_info(lock_path: Path) -> dict | None:
        """Read lock metadata without acquiring.

        Returns *None* when the file is absent or contains invalid JSON.
        """
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            if all(k in data for k in ("token", "pid", "start_time", "hostname")):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return None

    # -- internal ----------------------------------------------

    def _build_metadata(self) -> dict:
        return {
            "token": self._token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "start_time": datetime.now(timezone.utc).isoformat(),
            "db_path": str(self._db_path),
            "reports_path": str(self._reports_dir),
            "log_path": str(self._log_path),
        }

    def _read_lock(self) -> dict:
        """Read the on-disk lock, raising OSError if absent."""
        return json.loads(self._lock_path.read_text(encoding="utf-8"))

    @staticmethod
    def _is_same_host(hostname: str) -> bool:
        return hostname == socket.gethostname()

    def _handle_existing(self) -> None:
        """Check whether the existing lock is stale and remove it if so.

        Returns without raising when the lock was removed (caller should
        retry).  Raises *LockHeldError* when the owner is still alive or
        the lock is a fresh malformed file.

        Rules (per Phase 4C1 spec):
        - Valid same-host + alive PID: always block (regardless of age).
        - Valid same-host + dead PID: recover immediately.
        - Malformed, foreign-host, or unprobeable: check stale age.
        - Never pass zero/negative/bool/non-int PID to ``_pid_is_alive``.
        """
        data = None
        try:
            data = json.loads(self._lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

        # Try to parse with and without hostname for backwards compat.
        if data is not None:
            has_all_keys = all(
                k in data for k in ("token", "pid", "start_time", "hostname")
            )
            if has_all_keys:
                pid_raw = data.get("pid")
                validated = validate_pid(pid_raw)
                is_same = self._is_same_host(data.get("hostname", ""))

                if is_same and validated is not None:
                    if _pid_is_alive(validated):
                        raise LockHeldError(data)  # always block
                    try:
                        self._lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return  # immediate recovery

                # Foreign-host or unprobeable: fall through to age check.
            elif all(k in data for k in ("token", "pid", "start_time")):
                # A legacy lock without hostname is unprobeable.
                pass
            # Otherwise malformed: fall through to age check.

        # Malformed, foreign, or unprobeable: check age.
        try:
            mtime = self._lock_path.stat().st_mtime
            age = time.time() - mtime
            if age > self._stale_seconds:
                try:
                    self._lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return  # old malformed/foreign lock removed
        except FileNotFoundError:
            # The previous owner may have released the lock after our
            # exclusive create failed. Let the caller retry acquisition.
            return
        except OSError:
            pass

        owner = data if data else {}
        raise LockHeldError(owner)


# ---------------------------------------------------------------------------
# TeeEmitter
# ---------------------------------------------------------------------------


class TeeEmitter:
    """Dual writer: console echo and per-attempt log file."""

    def __init__(self, log_path: Path, echo: Callable[[str], None]) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = log_path.open("a", encoding="utf-8")
        self._echo = echo

    def emit(self, message: str) -> None:
        """Write *message* plus newline to both the log and console."""
        self._file.write(message + "\n")
        self._file.flush()
        self._echo(message)

    def write_log(self, line: str) -> None:
        """Write a line to the log without echoing to the console."""
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


# ---------------------------------------------------------------------------
# CollectResult
# ---------------------------------------------------------------------------


@dataclass
class SourceFailure:
    """A single source-level collection failure."""

    source_name: str
    error: str


@dataclass
class CollectResult:
    """Structured result returned by ``_run_live_collect``."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    failures: list[SourceFailure] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

SUMMARY_KEYS = [
    "run_id",
    "status",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_seconds",
    "live",
    "db_path",
    "reports_dir",
    "lock_path",
    "log_path",
    "errors",
]


def build_summary_dict(
    *,
    run_id: str,
    status: str,
    exit_code: int,
    started_at: str,
    finished_at: str | None = None,
    duration_seconds: float,
    live: bool,
    db_path: str,
    reports_dir: str,
    lock_path: str,
    log_path: str,
    errors: list[str] | None = None,
) -> dict:
    """Build a serialisable summary dictionary."""
    return {
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": finished_at or datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "live": live,
        "db_path": db_path,
        "reports_dir": reports_dir,
        "lock_path": lock_path,
        "log_path": log_path,
        "errors": errors or [],
    }


def write_summary_file(summary: dict, path: Path) -> None:
    """Atomically write a summary JSON file via temp + os.replace.

    Cleans up the temporary file if the write or replace fails.
    Preserves an existing destination on failure.
    """
    tmp = path.with_name(path.name + f".tmp{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def update_latest_summary(summary_path: Path, latest_path: Path) -> None:
    """Atomically copy the given summary to *latest_path*.

    Cleans up the temporary file if the write or replace fails.
    Preserves an existing latest on failure.
    """
    tmp = latest_path.with_name(latest_path.name + f".tmp{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(summary_path.read_bytes())
        os.replace(str(tmp), str(latest_path))
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
