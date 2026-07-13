"""Tests for weekly runtime: lock, logs, summaries, source exit status."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from findjobs.weekly_runtime import (
    CollectResult,
    LockHeldError,
    ProcessLock,
    SourceFailure,
    TeeEmitter,
    build_summary_dict,
    resolve_lock_path,
    resolve_logs_dir,
    set_pid_alive_fn,
    update_latest_summary,
    validate_pid,
    write_summary_file,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pid_probe() -> None:
    """Reset the injectable PID probe after every test."""
    yield
    set_pid_alive_fn(None)


def _write_lock(
    lock_path: Path,
    pid: int = os.getpid(),
    token: str = "existing-token",
    start_time: str | None = None,
    hostname: str | None = None,
    **extra: object,
) -> dict:
    """Write a lock file with the given metadata and return it."""
    from datetime import datetime, timezone

    data = {
        "token": token,
        "pid": pid,
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "start_time": start_time or datetime.now(timezone.utc).isoformat(),
        "db_path": "/tmp/test.db",
        "reports_path": "/tmp/reports",
        "log_path": "/tmp/test.log",
    }
    data.update(extra)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(data), encoding="utf-8")
    return data


# ===================================================================
# ProcessLock: exclusive acquire / release
# ===================================================================


class TestProcessLockBasic:
    """Basic acquire/release and token ownership."""

    def test_acquire_creates_lock_file(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        assert not lock_path.exists()
        lock.acquire()
        assert lock_path.exists()
        assert lock.owned
        # File contains valid JSON with expected keys.
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        assert "token" in content
        assert "pid" in content
        assert content["pid"] == os.getpid()
        lock.release()

    def test_release_removes_lock_file(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        assert lock_path.exists()
        lock.release()
        assert not lock_path.exists()
        assert not lock.owned

    def test_release_only_removes_owned_token(self, tmp_path: Path) -> None:
        """release() does not remove a lock owned by a different token."""
        lock_path = tmp_path / "test.lock"
        # Create lock owned by someone else.
        _write_lock(lock_path, pid=9999, token="other-token")
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.release()  # Should not remove other's lock.
        assert lock_path.exists()

    def test_double_release_is_harmless(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        lock.release()
        lock.release()  # Must not raise.

    def test_acquire_release_twice(self, tmp_path: Path) -> None:
        """Same instance can acquire again after release."""
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        lock.release()
        lock.acquire()
        assert lock.owned
        assert lock_path.exists()
        lock.release()


# ===================================================================
# ProcessLock: concurrent / held lock
# ===================================================================


class TestProcessLockHeld:
    """Lock held by another instance."""

    def test_pre_created_lock_raises(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)  # Simulate alive owner.

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError) as exc_info:
            lock.acquire()
        assert not lock.owned
        assert "already running" in str(exc_info.value)

    def test_held_lock_causes_cli_exit_2(self, tmp_path: Path) -> None:
        """CLI exits 2 when lock is held."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)  # Alive owner.

        # DB must exist (init_db will open it, but lock should be checked first).
        from findjobs.db import init_db

        init_db(db_path).close()

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(tmp_path / "reports"),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 2
        assert "already running" in result.output

    def test_blocked_does_not_remove_owner_lock(self, tmp_path: Path) -> None:
        """A blocked contender must not remove the owner's lock."""
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()
        assert not lock.owned

    def test_blocked_db_and_artifacts_unchanged(self, tmp_path: Path) -> None:
        """On exit 2, database and previous output artifacts are unchanged."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        lock_path = db_path.with_name(db_path.name + ".weekly.lock")

        # Pre-create a lock with an alive owner.
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)

        # Pre-create a report to verify it survives.
        reports_dir = tmp_path / "reports"
        existing_report = reports_dir / "weekly" / "existing.md"
        existing_report.parent.mkdir(parents=True, exist_ok=True)
        existing_report.write_text("previous content", encoding="utf-8")
        db_hash_before = db_path.read_bytes()[:64] if db_path.exists() else b""

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 2
        # Lock file must still exist (not removed by blocked contender).
        assert lock_path.exists()
        # Existing artifacts unchanged.
        assert existing_report.read_text(encoding="utf-8") == "previous content"
        if db_path.exists():
            assert db_path.read_bytes()[:64] == db_hash_before


# ===================================================================
# ProcessLock: stale recovery
# ===================================================================


class TestProcessLockStale:
    """Stale lock recovery and fresh malformed-lock blocking."""

    def test_dead_owner_stale_recovery(self, tmp_path: Path) -> None:
        """Lock with a dead PID is recovered."""
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: False)  # Simulate dead owner.

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        assert lock.owned
        # Old lock should have been replaced.
        content = json.loads(lock_path.read_text(encoding="utf-8"))
        assert content["pid"] == os.getpid()
        lock.release()

    def test_fresh_malformed_lock_blocked(self, tmp_path: Path) -> None:
        """A malformed lock younger than the stale threshold is not stolen."""
        lock_path = tmp_path / "test.lock"
        # Write an invalid JSON file.
        lock_path.write_text("not-json", encoding="utf-8")

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=2.0,
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()  # Not removed.
        assert not lock.owned

    def test_old_malformed_lock_recovered(self, tmp_path: Path) -> None:
        """A malformed lock older than the threshold is removed."""
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("not-json", encoding="utf-8")
        # Set mtime far in the past.
        old_mtime = time.time() - 7200  # 2 hours ago
        os.utime(str(lock_path), (old_mtime, old_mtime))

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=1.0,  # The file is older than this threshold.
        )
        lock.acquire()
        assert lock.owned
        lock.release()

    def test_old_malformed_lock_with_custom_threshold(self, tmp_path: Path) -> None:
        """Custom stale threshold works for recovery."""
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("garbage", encoding="utf-8")
        old_mtime = time.time() - 3600  # 1 hour ago
        os.utime(str(lock_path), (old_mtime, old_mtime))

        # A one-hour-old file is stale at this threshold.
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=0.5,
        )
        lock.acquire()
        assert lock.owned
        lock.release()

    def test_dead_pid_but_malformed_lock_not_recovered_below_threshold(
        self, tmp_path: Path
    ) -> None:
        """Malformed lock with readable-but-invalid JSON below threshold blocks."""
        lock_path = tmp_path / "test.lock"
        # Write valid JSON but missing required fields.
        lock_path.write_text(json.dumps({"some": "data"}), encoding="utf-8")

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=2.0,
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()

    def test_stale_delete_acquire_race(self, tmp_path: Path) -> None:
        """Retry exclusive creation after stale deletion handles race."""
        lock_path = tmp_path / "test.lock"
        # Start with a stale lock from dead PID.
        _write_lock(lock_path, pid=99999, token="stale-token")
        set_pid_alive_fn(lambda p: p != 99999)  # Only 99999 is dead.

        # First acquire should remove the stale lock and retry.
        lock1 = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock1.acquire()
        assert lock1.owned

        # Second acquire on same path should fail (now held by lock1).
        lock2 = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError):
            lock2.acquire()
        assert not lock2.owned

        lock1.release()

    def test_lock_disappearing_before_inspection_is_retried(
        self, tmp_path: Path
    ) -> None:
        """A lock released after exclusive-create failure is not blocking."""
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        real_open = os.open
        calls = 0

        def racing_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise FileExistsError
            return real_open(*args, **kwargs)

        with patch("findjobs.weekly_runtime.os.open", side_effect=racing_open):
            lock.acquire()

        assert lock.owned
        assert calls == 2
        lock.release()


# ===================================================================
# NEW tests: old live lock, PID safety, foreign-host age, race
# ===================================================================


class TestProcessLockPidSafety:
    """PID validation and safe probe behavior."""

    def test_old_live_lock_never_stolen(self, tmp_path: Path) -> None:
        """An old lock with a confirmed live owner always blocks."""
        lock_path = tmp_path / "test.lock"
        _write_lock(
            lock_path,
            pid=os.getpid() + 1_000_000,
            hostname=socket.gethostname(),
        )
        set_pid_alive_fn(lambda _: True)

        # Make the lock appear ancient.
        ancient = time.time() - 999999
        os.utime(str(lock_path), (ancient, ancient))

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()
        assert not lock.owned

    def test_negative_pid_blocks_fresh_lock(self, tmp_path: Path) -> None:
        """Negative PID metadata uses age-check; a fresh lock blocks."""
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, pid=-5)
        # Even though pid is "dead", negative pid must not be probed.
        set_pid_alive_fn(lambda _: False)

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()

    def test_bool_pid_never_probed(self, tmp_path: Path) -> None:
        """Bool PID metadata is never passed to the OS probe."""
        lock_path = tmp_path / "test.lock"
        # Write a lock with bool PID (JSON serializes True/False).
        _write_lock(lock_path, pid=True)

        # Track whether _pid_is_alive would be called for a bool pid.
        call_log: list = []

        def _tracking_probe(pid: int) -> bool:
            call_log.append(pid)
            return True

        set_pid_alive_fn(_tracking_probe)
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        with pytest.raises(LockHeldError):
            lock.acquire()
        # The probe should not have been called with the bool pid.
        # (It might have been called for a different pid, but not for True/1.)
        for called_pid in call_log:
            assert isinstance(called_pid, int) and not isinstance(called_pid, bool)

    def test_zero_pid_never_probed(self, tmp_path: Path) -> None:
        """PID 0 is never probed; fresh lock blocks."""
        lock_path = tmp_path / "test.lock"
        _write_lock(lock_path, pid=0)
        set_pid_alive_fn(lambda _: True)

        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        # pid=0 is same-host (hostname matches), but invalid, so it falls
        # to age-check. Since lock is fresh (<24h default), it blocks.
        with pytest.raises(LockHeldError):
            lock.acquire()
        assert lock_path.exists()

    def test_validate_pid_rejects_non_positive(self) -> None:
        """validate_pid rejects zero, negatives, bools, floats, None."""
        assert validate_pid(0) is None
        assert validate_pid(-1) is None
        assert validate_pid(-999) is None
        assert validate_pid(True) is None  # bool is not int for PID
        assert validate_pid(False) is None
        assert validate_pid(1.0) is None  # float
        assert validate_pid("42") is None  # string
        assert validate_pid(None) is None
        # Valid
        assert validate_pid(1) == 1
        assert validate_pid(42) == 42
        assert validate_pid(999999) == 999999

    def test_foreign_host_lock_age_based(self, tmp_path: Path) -> None:
        """A fresh foreign-host lock blocks; an old one is recovered."""
        lock_path = tmp_path / "test.lock"
        _write_lock(
            lock_path,
            pid=os.getpid() + 1_000_000,
            hostname="some-other-host",
        )
        set_pid_alive_fn(lambda _: True)

        # Fresh foreign-host lock should block (not old enough).
        lock_fresh = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=24.0,
        )
        with pytest.raises(LockHeldError):
            lock_fresh.acquire()
        assert lock_path.exists()

        # Make it old, then it should be recoverable.
        old_mtime = time.time() - 25 * 3600
        os.utime(str(lock_path), (old_mtime, old_mtime))

        # Also write a stale (dead PID) foreign lock to verify recovery.
        _write_lock(
            lock_path,
            pid=99999,
            hostname="some-other-host",
        )
        set_pid_alive_fn(lambda p: p != 99999)
        os.utime(str(lock_path), (old_mtime, old_mtime))

        lock_old = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
            stale_hours=24.0,
        )
        lock_old.acquire()
        assert lock_old.owned
        lock_old.release()

    def test_windows_probe_still_active(self) -> None:
        """Windows probe returns True for STILL_ACTIVE exit code.

        Uses monkeypatched ctypes/kernel calls and no real processes.
        """
        import ctypes
        from unittest.mock import MagicMock

        class _MockKernel32:
            def __init__(self) -> None:
                self.handle = ctypes.c_void_p(1)

            def OpenProcess(
                self, access: int, inherit: bool, pid: int
            ) -> ctypes.c_void_p:
                return self.handle

            def GetExitCodeProcess(self, handle: int, ec: object) -> bool:
                # byref is mocked to return the c_ulong directly.
                ec.value = 259  # STILL_ACTIVE
                return True

            def CloseHandle(self, handle: int) -> bool:
                return True

        mock_wintypes = MagicMock()
        mock_wintypes.DWORD = ctypes.c_ulong

        with (
            patch("platform.system", return_value="Windows"),
            patch("ctypes.WinDLL", return_value=_MockKernel32(), create=True),
            patch("ctypes.wintypes", mock_wintypes, create=True),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("ctypes.GetLastError", return_value=0, create=True),
        ):
            from findjobs.weekly_runtime import _default_pid_alive

            assert _default_pid_alive(42) is True

    def test_windows_probe_access_denied(self) -> None:
        """Windows probe treats ERROR_ACCESS_DENIED as alive."""

        class _MockKernel32:
            def OpenProcess(self, access: int, inherit: bool, pid: int) -> None:
                return None  # null handle

            def CloseHandle(self, handle: int) -> bool:
                return True

        with (
            patch("platform.system", return_value="Windows"),
            patch("ctypes.WinDLL", return_value=_MockKernel32(), create=True),
            patch("ctypes.GetLastError", return_value=5, create=True),
        ):
            from findjobs.weekly_runtime import _default_pid_alive

            assert _default_pid_alive(42) is True

    def test_windows_probe_dead_pid(self) -> None:
        """Windows probe returns False for non-existent PID."""

        class _MockKernel32:
            def OpenProcess(self, access: int, inherit: bool, pid: int) -> None:
                return None  # null handle

            def CloseHandle(self, handle: int) -> bool:
                return True

        with (
            patch("platform.system", return_value="Windows"),
            patch("ctypes.WinDLL", return_value=_MockKernel32(), create=True),
            patch("ctypes.GetLastError", return_value=87, create=True),
        ):
            from findjobs.weekly_runtime import _default_pid_alive

            assert _default_pid_alive(999999) is False

    def test_posix_probe_permission_error_alive(self) -> None:
        """POSIX probe treats PermissionError as alive."""
        with patch("platform.system", return_value="Linux"):
            from findjobs.weekly_runtime import _default_pid_alive

            with patch("os.kill", side_effect=PermissionError("denied")):
                assert _default_pid_alive(1234) is True

    def test_posix_probe_process_lookup_dead(self) -> None:
        """POSIX probe treats ProcessLookupError as dead."""
        with patch("platform.system", return_value="Linux"):
            from findjobs.weekly_runtime import _default_pid_alive

            with patch(
                "os.kill",
                side_effect=ProcessLookupError("no process"),
            ):
                assert _default_pid_alive(99999) is False

    def test_windows_probe_getexitcode_failure_conservative(
        self,
    ) -> None:
        """Windows probe treats GetExitCodeProcess failure as alive."""
        import ctypes
        from unittest.mock import MagicMock as _MM

        class _MockKernel32:
            def __init__(self) -> None:
                self.handle = ctypes.c_void_p(1)

            def OpenProcess(
                self, access: int, inherit: bool, pid: int
            ) -> ctypes.c_void_p:
                return self.handle

            def GetExitCodeProcess(self, handle: int, ec: object) -> bool:
                return False  # Failed

            def CloseHandle(self, handle: int) -> bool:
                return True

        mock_wintypes = _MM()
        mock_wintypes.DWORD = ctypes.c_ulong

        with (
            patch("platform.system", return_value="Windows"),
            patch("ctypes.WinDLL", return_value=_MockKernel32(), create=True),
            patch("ctypes.wintypes", mock_wintypes, create=True),
            patch("ctypes.byref", side_effect=lambda x: x),
            patch("ctypes.GetLastError", return_value=0, create=True),
        ):
            from findjobs.weekly_runtime import _default_pid_alive

            assert _default_pid_alive(42) is True


# ===================================================================
# ProcessLock: lock cleanup on success / failure
# ===================================================================


class TestProcessLockCleanup:
    """Lock is released on success and unexpected failure."""

    def test_lock_cleanup_on_success(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        lock.release()
        assert not lock_path.exists()

    def test_lock_not_left_behind_after_weekly_cli_success(
        self, tmp_path: Path
    ) -> None:
        """Weekly CLI removes the lock file on success."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        # Seed a job so export/analysis can proceed.
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="TestCo", slug="testco")
        session.add(company)
        session.flush()
        source = Source(
            name="Test Source",
            slug="test-source",
            company_id=company.id,
        )
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="ext-1",
            company_id=company.id,
            source_id=source.id,
            title="Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "missing.md"

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(profile_path),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 0, result.output
        assert not lock_path.exists(), "Lock file was not removed"

    def test_lock_cleanup_on_cli_failure(self, tmp_path: Path) -> None:
        """Weekly CLI removes the lock file even after an unexpected failure."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "missing.md"
        lock_path = db_path.with_name(db_path.name + ".weekly.lock")

        # Create a minimal DB.
        from findjobs.db import init_db

        init_db(db_path).close()

        # Patch _export_file to raise an unexpected error.
        with patch(
            "findjobs.cli._export_file",
            side_effect=RuntimeError("unexpected export crash"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(profile_path),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1
        # Lock should have been cleaned up.
        assert not lock_path.exists(), "Lock file was not removed on failure"


# ===================================================================
# ProcessLock: metadata write / release failure cleanup
# ===================================================================


class TestProcessLockFailureCleanup:
    """Lock file cleaned up on metadata write failure; release is
    lossless."""

    def test_metadata_write_failure_cleans_up_lock(self, tmp_path: Path) -> None:
        """Lock file is removed if json.dump fails after O_EXCL."""
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )

        with patch(
            "findjobs.weekly_runtime.json.dump",
            side_effect=ValueError("bad data"),
        ):
            with pytest.raises(ValueError):
                lock.acquire()

        assert not lock_path.exists(), "Lock was not cleaned up"
        assert not lock.owned

    def test_release_token_mismatch_observable(self, tmp_path: Path) -> None:
        """release() returns False when lock file has different token."""
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()
        assert lock_path.exists()

        # Externally replace lock with a different token
        _write_lock(
            lock_path,
            pid=os.getpid(),
            token="imposter-token",
        )

        result = lock.release()
        assert result is False, "Expected False for token mismatch"
        assert lock_path.exists(), "Other owner's lock was removed"
        assert not lock.owned

    def test_release_malformed_raises(self, tmp_path: Path) -> None:
        """release() raises on malformed lock file."""
        lock_path = tmp_path / "test.lock"
        lock = ProcessLock(
            lock_path,
            db_path=tmp_path / "test.db",
            reports_dir=tmp_path / "reports",
            log_path=tmp_path / "test.log",
        )
        lock.acquire()

        # Corrupt the lock file
        lock_path.write_text("not-json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            lock.release()


# ===================================================================
# Summary JSON schema
# ===================================================================


class TestRunSummary:
    """Success, failure, and blocked JSON schemas."""

    def _check_summary(self, path: Path, *, status: str, exit_code: int) -> dict:
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["status"] == status
        assert data["exit_code"] == exit_code
        for key in (
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
        ):
            assert key in data, f"Missing summary key: {key}"
        assert isinstance(data["errors"], list)
        return data

    def test_success_summary_schema(self, tmp_path: Path) -> None:
        """Weekly --no-live creates a succeeded summary."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="TestCo", slug="testco")
        session.add(company)
        session.flush()
        source = Source(name="Test", slug="test-source", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="x1",
            company_id=company.id,
            source_id=source.id,
            title="Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 0, result.output

        # Locate the summary file in logs dir.
        logs_dir = reports_dir / "logs"
        summaries = list(logs_dir.glob("*.summary.json"))
        assert len(summaries) >= 1
        # Find the one with succeeded status.
        succeeded = [
            s
            for s in summaries
            if json.loads(s.read_text(encoding="utf-8"))["status"] == "succeeded"
        ]
        assert len(succeeded) >= 1
        self._check_summary(succeeded[0], status="succeeded", exit_code=0)

    def test_failure_summary_schema(self, tmp_path: Path) -> None:
        """An unexpected failure produces a failed summary."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db

        init_db(db_path).close()

        with patch(
            "findjobs.cli._export_file",
            side_effect=RuntimeError("crash"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1
        logs_dir = reports_dir / "logs"
        summaries = list(logs_dir.glob("*.summary.json"))
        failed = [
            s
            for s in summaries
            if json.loads(s.read_text(encoding="utf-8"))["status"] == "failed"
        ]
        assert len(failed) >= 1
        data = self._check_summary(failed[0], status="failed", exit_code=1)
        assert len(data["errors"]) > 0

    def test_blocked_summary_schema(self, tmp_path: Path) -> None:
        """A blocked attempt produces a blocked summary."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        from findjobs.db import init_db

        init_db(db_path).close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)

        reports_dir = tmp_path / "reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 2

        logs_dir = reports_dir / "logs"
        summaries = list(logs_dir.glob("*.summary.json"))
        blocked = [
            s
            for s in summaries
            if json.loads(s.read_text(encoding="utf-8"))["status"] == "blocked"
        ]
        assert len(blocked) >= 1
        self._check_summary(blocked[0], status="blocked", exit_code=2)

    def test_latest_summary_not_replaced_by_blocked(self, tmp_path: Path) -> None:
        """blocked contender does not replace the latest summary."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        logs_dir = reports_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Write a fake "owner succeeded" latest summary.
        fake_latest = logs_dir / "weekly-latest.json"
        fake_latest.write_text(
            json.dumps({"status": "succeeded", "exit_code": 0}),
            encoding="utf-8",
        )
        fake_mtime = fake_latest.stat().st_mtime

        # Block a contender.
        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        _write_lock(lock_path, pid=os.getpid() + 1_000_000)
        set_pid_alive_fn(lambda _: True)

        from findjobs.db import init_db

        init_db(db_path).close()

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 2

        # Latest must still be the fake succeeded one (content unchanged).
        assert fake_latest.exists()
        assert (
            json.loads(fake_latest.read_text(encoding="utf-8"))["status"] == "succeeded"
        )


# ===================================================================
# Logging: no-live weekly creates logs and summary
# ===================================================================


class TestWeeklyLogging:
    """No-live weekly creates log and summary files."""

    def test_no_live_weekly_creates_logs_and_summary(self, tmp_path: Path) -> None:
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="TestCo", slug="testco")
        session.add(company)
        session.flush()
        source = Source(name="Test", slug="test-source", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="x1",
            company_id=company.id,
            source_id=source.id,
            title="Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 0, result.output

        logs_dir = reports_dir / "logs"
        assert logs_dir.exists()

        log_files = list(logs_dir.glob("*.log"))
        assert len(log_files) >= 1

        summary_files = list(logs_dir.glob("*.summary.json"))
        assert len(summary_files) >= 1

        # Log contains expected console lines.
        log_content = log_files[0].read_text(encoding="utf-8")
        assert "Skipping live collection." in log_content
        assert "Exporting job facts..." in log_content
        assert "Running local analysis..." in log_content

    def test_source_lines_appear_in_weekly_log(self, tmp_path: Path) -> None:
        """Source collection lines appear in the weekly log file."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="LiveCo", slug="liveco")
        session.add(company)
        session.flush()
        source = Source(
            name="Live Source",
            slug="live-source",
            company_id=company.id,
            is_active=True,
        )
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="l1",
            company_id=company.id,
            source_id=source.id,
            title="Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        # Patch load_sources so _run_live_collect finds a source.
        from findjobs.config import (
            CompanyConfig,
            SourceConfig,
            SourcesConfig,
        )

        live_config = SourcesConfig(
            companies=[CompanyConfig(slug="liveco", name="LiveCo")],
            sources=[
                SourceConfig(
                    slug="live-source",
                    name="Live Source",
                    company_slug="liveco",
                    is_active=True,
                    adapter="test_log_adapter",
                    collection_completeness="partial",
                )
            ],
        )

        from findjobs.adapters.registry import register
        from findjobs.collection import CollectedJob

        class _LogTestAdapter:
            def collect(self, context: object) -> list[CollectedJob]:
                return [
                    CollectedJob(
                        external_id="log-ext-1",
                        title="LogTest Engineer",
                        matched_tags=["Test"],
                    )
                ]

        register("test_log_adapter", _LogTestAdapter())

        with patch("findjobs.cli.load_sources", return_value=live_config):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 0, result.output
        logs_dir = reports_dir / "logs"
        log_files = list(logs_dir.glob("*.log"))
        assert len(log_files) >= 1
        log_content = log_files[0].read_text(encoding="utf-8")
        assert "Collecting live jobs..." in log_content
        assert "Live Source" in log_content or "collecting" in log_content


# ===================================================================
# CollectResult / Source failure propagation
# ===================================================================


class TestCollectResult:
    """collect exit code and weekly continuation on partial failures."""

    def test_collect_live_exits_1_after_partial_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from findjobs.cli import app

        db_path = tmp_path / "test.db"

        from findjobs.adapters.registry import register
        from findjobs.collection import CollectedJob
        from findjobs.config import (
            CompanyConfig,
            SourceConfig,
            SourcesConfig,
        )

        class _TestFailAdapter:
            def collect(self, context: object) -> list[CollectedJob]:
                msg = "network timeout"
                raise RuntimeError(msg)

        class _TestOkAdapter:
            def collect(self, context: object) -> list[CollectedJob]:
                return [
                    CollectedJob(
                        external_id="ok-1",
                        title="OK Engineer",
                        matched_tags=["OK"],
                    )
                ]

        register("collect_fail_adapter", _TestFailAdapter())
        register("collect_ok_adapter", _TestOkAdapter())

        config = SourcesConfig(
            companies=[CompanyConfig(slug="testco", name="Test Co")],
            sources=[
                SourceConfig(
                    slug="source-fail",
                    name="Failing Source",
                    company_slug="testco",
                    is_active=True,
                    adapter="collect_fail_adapter",
                    collection_completeness="partial",
                ),
                SourceConfig(
                    slug="source-ok",
                    name="OK Source",
                    company_slug="testco",
                    is_active=True,
                    adapter="collect_ok_adapter",
                    collection_completeness="partial",
                ),
            ],
        )

        monkeypatch.setattr("findjobs.cli.load_sources", lambda: config)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["collect", "--live", "--db-path", str(db_path)],
        )
        assert result.exit_code == 1
        assert "failed" in result.output
        assert "Failing Source" in result.output

    def test_weekly_live_continues_after_partial_failures(self, tmp_path: Path) -> None:
        """Weekly continues with export/analysis after partial source failure."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        # Seed DB with at least one job for export/analysis.
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="SeedCo", slug="seedco")
        session.add(company)
        session.flush()
        source = Source(
            name="Seed Source",
            slug="seed-source",
            company_id=company.id,
        )
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="seed-1",
            company_id=company.id,
            source_id=source.id,
            title="Seed Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        # Mock _run_live_collect to return a partial-failure result.
        from findjobs.weekly_runtime import CollectResult, SourceFailure

        mock_result = CollectResult(
            total=3,
            succeeded=2,
            failed=1,
            failures=[SourceFailure("Broken Source", "connection error")],
        )

        with patch(
            "findjobs.cli._run_live_collect",
            return_value=mock_result,
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1, result.output
        assert "1 failed" in result.output
        assert "Broken Source" in result.output
        # Export files should still exist.
        assert (reports_dir / "weekly" / "jobs.jsonl").exists(), "Weekly export missing"
        assert (reports_dir / "weekly" / "jobs.csv").exists(), "CSV export missing"

    def test_weekly_live_fully_successful_exit_0(self, tmp_path: Path) -> None:
        """Fully successful live collection in weekly exits 0."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        session = init_db(db_path)
        company = Company(name="AllGood", slug="allgood")
        session.add(company)
        session.flush()
        source = Source(
            name="All Good Source",
            slug="allgood-source",
            company_id=company.id,
        )
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="ag-1",
            company_id=company.id,
            source_id=source.id,
            title="Good Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        mock_result = CollectResult(total=2, succeeded=2, failed=0)

        with patch(
            "findjobs.cli._run_live_collect",
            return_value=mock_result,
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 0, result.output


# ===================================================================
# Collision resistance: same-second invocations produce distinct
# artifacts
# ===================================================================


class TestArtifactCollision:
    """Two same-second invocations produce distinct runs, no overwrite."""

    def test_collision_same_timestamp_two_invocations(self, tmp_path: Path) -> None:
        """Two weekly invocations with same wall-clock timestamp produce
        distinct logs and summaries with different run IDs and no
        overwrite.
        """
        from datetime import datetime, timezone

        from findjobs.cli import app
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        company = Company(name="CollisionCo", slug="collisionco")
        session.add(company)
        session.flush()
        source = Source(name="Collision", slug="collision", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="c1",
            company_id=company.id,
            source_id=source.id,
            title="Collision Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        reports_dir = tmp_path / "reports"
        logs_dir = reports_dir / "logs"
        fixed_dt = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)

        runner = CliRunner()

        with patch("findjobs.cli._utc_now", return_value=fixed_dt):
            result1 = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )
        assert result1.exit_code == 0, result1.output

        with patch("findjobs.cli._utc_now", return_value=fixed_dt):
            result2 = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )
        assert result2.exit_code == 0, result2.output

        log_files = sorted(logs_dir.glob("*.log"))
        summary_files = sorted(logs_dir.glob("*.summary.json"))

        assert len(log_files) >= 2, f"Expected >=2 log files, got {len(log_files)}"
        assert len(summary_files) >= 2, (
            f"Expected >=2 summary files, got {len(summary_files)}"
        )

        # Two most-recent summaries: different run IDs
        summaries = [
            json.loads(f.read_text(encoding="utf-8")) for f in summary_files[-2:]
        ]
        assert summaries[0]["run_id"] != summaries[1]["run_id"], "Run IDs must differ"

        # Filenames differ (no overwrite)
        log_names = [f.name for f in log_files[-2:]]
        assert log_names[0] != log_names[1], "Log files overwritten"


# ===================================================================
# NEW tests: summary failure and latest failure
# ===================================================================


class TestSummaryFailureCleanup:
    """Summary and latest-summary failures release the lock and exit 1."""

    def test_summary_failure_releases_lock_and_exits_1(self, tmp_path: Path) -> None:
        """Summary write failure does not leave lock behind and exits 1."""
        from findjobs.cli import app
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        company = Company(name="S", slug="s")
        session.add(company)
        session.flush()
        source = Source(name="S", slug="s", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="s1",
            company_id=company.id,
            source_id=source.id,
            title="S",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"

        with patch(
            "findjobs.weekly_runtime.write_summary_file",
            side_effect=RuntimeError("summary write failed"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        # Summary failure should produce exit 1.
        assert result.exit_code == 1, result.output
        assert "warning" in result.output.lower() or "summary" in result.output.lower()
        # Lock must not be left behind.
        assert not lock_path.exists(), "Lock file was not released"

    def test_latest_failure_releases_lock_and_exits_1(self, tmp_path: Path) -> None:
        """Latest-summary update failure releases lock and exits 1."""
        from findjobs.cli import app
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        company = Company(name="L", slug="l")
        session.add(company)
        session.flush()
        source = Source(name="L", slug="l", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="l1",
            company_id=company.id,
            source_id=source.id,
            title="L",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"

        with patch(
            "findjobs.weekly_runtime.update_latest_summary",
            side_effect=RuntimeError("latest update failed"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1, result.output
        assert "warning" in result.output.lower()
        # Lock released.
        assert not lock_path.exists()

        # Per-attempt summary must show failed/1 + reporting error
        logs_dir = reports_dir / "logs"
        summaries = sorted(logs_dir.glob("*.summary.json"))
        assert len(summaries) >= 1
        latest = json.loads(summaries[-1].read_text(encoding="utf-8"))
        assert latest["status"] == "failed", f"Expected failed, got {latest['status']}"
        assert latest["exit_code"] == 1, (
            f"Expected exit_code 1, got {latest['exit_code']}"
        )
        assert any("latest update failed" in e for e in latest.get("errors", [])), (
            f"Missing 'latest update failed' in errors: {latest.get('errors')}"
        )


# ===================================================================
# NEW tests: workflow error preserved when reporting also fails
# ===================================================================


class TestWorkflowErrorPreserved:
    """Original workflow error remains visible if reporting also fails."""

    def test_workflow_error_plus_summary_failure_preserves_workflow_error(
        self, tmp_path: Path
    ) -> None:
        """Workflow error is in console/log even when summary also fails."""
        from findjobs.cli import app
        from findjobs.db import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path).close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"

        # First patch: _export_file fails (workflow error).
        # Second patch: write_summary_file also fails (reporting error).
        with (
            patch(
                "findjobs.cli._export_file",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "findjobs.weekly_runtime.write_summary_file",
                side_effect=RuntimeError("summary write failed"),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        # Exit 1.
        assert result.exit_code == 1

        # The workflow error "boom" must be visible.
        assert "boom" in result.output or "boom" in str(result.exception)

        # Lock released.
        assert not lock_path.exists()


# ===================================================================
# Emit failure: initial path emit and final path emit
# ===================================================================


class TestEmitFailure:
    """Initial/final emit failures release lock and exit 1."""

    def test_initial_emit_failure_releases_lock_and_exits_1(
        self, tmp_path: Path
    ) -> None:
        """Initial 'logs:' emit failure releases lock and exits 1."""
        from findjobs.cli import app
        from findjobs.db import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path).close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"
        logs_dir = reports_dir / "logs"

        # Patch TeeEmitter.emit so the initial "  logs:" call fails.
        # The initial emit inside the inner try raises before any
        # workflow code runs.
        with patch(
            "findjobs.weekly_runtime.TeeEmitter.emit",
            side_effect=RuntimeError("initial emit failed"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1, result.output
        assert not lock_path.exists(), "Lock was not released"

    def test_final_emit_failure_releases_lock_and_exits_1(self, tmp_path: Path) -> None:
        """Final 'log:'/'summary:' emit failure sets exit 1 and
        releases lock."""
        from findjobs.cli import app
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        company = Company(name="FEmit", slug="femit")
        session.add(company)
        session.flush()
        source = Source(name="FEmit", slug="femit", company_id=company.id)
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="f1",
            company_id=company.id,
            source_id=source.id,
            title="F",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        reports_dir = tmp_path / "reports"

        # Patch emit so that the "  log:" final path (singular,
        # not "logs:") fails.  Workflow emits still work.
        saved_emit = None

        def _selective_emit(msg: str) -> None:
            if msg.startswith("  log:"):
                raise RuntimeError("final emit failed")

        with patch(
            "findjobs.weekly_runtime.TeeEmitter.emit",
            side_effect=_selective_emit,
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1, result.output
        assert not lock_path.exists(), "Lock was not released"

        summaries = sorted((reports_dir / "logs").glob("*.summary.json"))
        assert summaries
        summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
        assert summary["status"] == "failed"
        assert summary["exit_code"] == 1
        assert any("final path emit failed" in error for error in summary["errors"])

    def test_release_failure_rewrites_summary_and_exits_1(self, tmp_path: Path) -> None:
        """A lock token mismatch is visible in the final run summary."""
        from findjobs.cli import app
        from findjobs.db import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path).close()
        reports_dir = tmp_path / "reports"

        with patch(
            "findjobs.weekly_runtime.ProcessLock.release",
            return_value=False,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                ],
            )

        assert result.exit_code == 1, result.output
        summaries = sorted((reports_dir / "logs").glob("*.summary.json"))
        assert summaries
        summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
        assert summary["status"] == "failed"
        assert summary["exit_code"] == 1
        assert any(
            "process lock release failed" in error for error in summary["errors"]
        )

    def test_blocked_summary_failure_still_closes_emitter(self, tmp_path: Path) -> None:
        """A blocked contender exits 2 and closes its log on report failure."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        lock_path = db_path.with_name(db_path.name + ".weekly.lock")
        owner = ProcessLock(
            lock_path,
            db_path=db_path,
            reports_dir=tmp_path / "owner-reports",
            log_path=tmp_path / "owner.log",
        )
        owner.acquire()
        emitter = MagicMock()

        try:
            with (
                patch(
                    "findjobs.weekly_runtime.TeeEmitter",
                    return_value=emitter,
                ),
                patch(
                    "findjobs.weekly_runtime.write_summary_file",
                    side_effect=OSError("disk full"),
                ),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "weekly",
                        "--no-live",
                        "--db-path",
                        str(db_path),
                        "--reports-dir",
                        str(tmp_path / "contender-reports"),
                    ],
                )
        finally:
            owner.release()

        assert result.exit_code == 2, result.output
        emitter.close.assert_called_once()

    def test_workflow_summary_errors_are_bounded_single_line(
        self, tmp_path: Path
    ) -> None:
        """Unexpected workflow exception text is safe for JSON summaries."""
        from findjobs.cli import app
        from findjobs.db import init_db

        db_path = tmp_path / "test.db"
        init_db(db_path).close()
        reports_dir = tmp_path / "reports"
        message = "first line\n" + ("response-body " * 80)

        with patch(
            "findjobs.cli._export_file",
            side_effect=RuntimeError(message),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                ],
            )

        assert result.exit_code == 1
        summary_path = next((reports_dir / "logs").glob("*.summary.json"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["errors"]
        assert all("\n" not in error for error in summary["errors"])
        assert all(len(error) <= 240 for error in summary["errors"])


# ===================================================================
# sync-config failure closes session
# ===================================================================


class TestSyncConfigFailure:
    """sync_config failure closes the database session."""

    def test_sync_config_failure_closes_session(self, tmp_path: Path) -> None:
        """When sync_config raises, session.close() is called in finally."""
        mock_session = MagicMock()

        with (
            patch("findjobs.db.init_db", return_value=mock_session),
            patch(
                "findjobs.repository.sync_config",
                side_effect=RuntimeError("sync config failed"),
            ),
        ):
            from findjobs.cli import _run_live_collect

            with pytest.raises(RuntimeError, match="sync config failed"):
                _run_live_collect(str(tmp_path / "test.db"), lambda x: None)

        mock_session.close.assert_called_once()


# ===================================================================
# Export / analysis failure records traceback and exit 1
# ===================================================================


class TestWeeklyFailure:
    """Unexpected failures are logged and produce exit 1."""

    def test_export_failure_records_traceback_and_exit_1(self, tmp_path: Path) -> None:
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"

        from findjobs.db import init_db

        init_db(db_path).close()

        with patch(
            "findjobs.cli._export_file",
            side_effect=RuntimeError("export crashed"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "weekly",
                    "--no-live",
                    "--db-path",
                    str(db_path),
                    "--reports-dir",
                    str(reports_dir),
                    "--profile",
                    str(tmp_path / "missing.md"),
                    "--since",
                    "365",
                ],
            )

        assert result.exit_code == 1
        # Log file should contain the traceback.
        logs_dir = reports_dir / "logs"
        log_files = list(logs_dir.glob("*.log"))
        assert len(log_files) >= 1
        log_content = log_files[0].read_text(encoding="utf-8")
        assert "Traceback" in log_content or "traceback" in log_content


# ===================================================================
# Path helpers
# ===================================================================


class TestResolvePaths:
    def test_resolve_lock_path_default(self) -> None:
        p = resolve_lock_path("/tmp/test.db")
        assert p == Path("/tmp/test.db.weekly.lock")

    def test_resolve_lock_path_explicit(self) -> None:
        p = resolve_lock_path("/tmp/test.db", explicit_lock="/custom/lock")
        assert p == Path("/custom/lock")

    def test_resolve_lock_path_no_db(self) -> None:
        p = resolve_lock_path(None)
        assert p.name.endswith(".weekly.lock")
        assert p.suffixes[-2:] == [".weekly", ".lock"]

    def test_resolve_logs_dir_default(self) -> None:
        p = resolve_logs_dir(Path("/reports"))
        assert p == Path("/reports/logs")

    def test_resolve_logs_dir_explicit(self) -> None:
        p = resolve_logs_dir(Path("/reports"), explicit_logs="/custom/logs")
        assert p == Path("/custom/logs")


# ===================================================================
# TeeEmitter
# ===================================================================


class TestTeeEmitter:
    def test_emitter_writes_to_both(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test.log"
        captured: list[str] = []

        def _echo(msg: str) -> None:
            captured.append(msg)

        emitter = TeeEmitter(log_path, _echo)
        emitter.emit("hello")
        emitter.emit("world")
        emitter.close()

        assert log_path.read_text(encoding="utf-8") == "hello\nworld\n"
        assert captured == ["hello", "world"]

    def test_write_log_only(self, tmp_path: Path) -> None:
        log_path = tmp_path / "test.log"
        captured: list[str] = []

        def _echo(msg: str) -> None:
            captured.append(msg)

        emitter = TeeEmitter(log_path, _echo)
        emitter.emit("console")
        emitter.write_log("log-only")
        emitter.close()

        assert "log-only" in log_path.read_text(encoding="utf-8")
        assert "log-only" not in captured


# ===================================================================
# build_summary_dict / write_summary_file / update_latest_summary
# ===================================================================


class TestSummaryHelpers:
    def test_build_summary_dict_all_keys(self) -> None:
        s = build_summary_dict(
            run_id="test-id",
            status="succeeded",
            exit_code=0,
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T01:00:00+00:00",
            duration_seconds=3600.0,
            live=False,
            db_path="/db/test.db",
            reports_dir="/reports",
            lock_path="/lock",
            log_path="/log",
            errors=[],
        )
        for key in (
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
        ):
            assert key in s

    def test_write_summary_file_atomic(self, tmp_path: Path) -> None:
        path = tmp_path / "summary.json"
        summary = build_summary_dict(
            run_id="test",
            status="succeeded",
            exit_code=0,
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.0,
            live=False,
            db_path="/db",
            reports_dir="/reports",
            lock_path="/lock",
            log_path="/log",
        )
        write_summary_file(summary, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "test"

    def test_update_latest_summary(self, tmp_path: Path) -> None:
        summary_path = tmp_path / "run.summary.json"
        latest_path = tmp_path / "weekly-latest.json"

        summary = build_summary_dict(
            run_id="latest-test",
            status="succeeded",
            exit_code=0,
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=100.0,
            live=False,
            db_path="/db",
            reports_dir="/reports",
            lock_path="/lock",
            log_path="/log",
        )
        write_summary_file(summary, summary_path)
        update_latest_summary(summary_path, latest_path)
        assert latest_path.exists()
        data = json.loads(latest_path.read_text(encoding="utf-8"))
        assert data["run_id"] == "latest-test"

    def test_write_summary_file_cleans_up_temp_on_failure(self, tmp_path: Path) -> None:
        """write_summary_file removes temp file and preserves destination
        when os.replace fails."""
        path = tmp_path / "summary.json"
        path.write_text('{"old": true}', encoding="utf-8")
        old_content = path.read_text(encoding="utf-8")

        summary = build_summary_dict(
            run_id="test",
            status="succeeded",
            exit_code=0,
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.0,
            live=False,
            db_path="/db",
            reports_dir="/reports",
            lock_path="/lock",
            log_path="/log",
        )

        with patch(
            "findjobs.weekly_runtime.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with pytest.raises(OSError):
                write_summary_file(summary, path)

        assert len(list(tmp_path.glob("*.tmp*"))) == 0
        assert path.read_text(encoding="utf-8") == old_content, (
            "Existing destination was modified"
        )

    def test_update_latest_summary_cleans_up_temp_on_failure(
        self, tmp_path: Path
    ) -> None:
        """update_latest_summary removes temp file and preserves latest
        when os.replace fails."""
        summary_path = tmp_path / "source.json"
        latest_path = tmp_path / "weekly-latest.json"
        latest_path.write_text('{"old": true}', encoding="utf-8")
        old_content = latest_path.read_text(encoding="utf-8")

        summary = build_summary_dict(
            run_id="test",
            status="succeeded",
            exit_code=0,
            started_at="2026-01-01T00:00:00+00:00",
            duration_seconds=0.0,
            live=False,
            db_path="/db",
            reports_dir="/reports",
            lock_path="/lock",
            log_path="/log",
        )
        write_summary_file(summary, summary_path)

        with patch(
            "findjobs.weekly_runtime.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with pytest.raises(OSError):
                update_latest_summary(summary_path, latest_path)

        assert len(list(tmp_path.glob("*.tmp*"))) == 0
        assert latest_path.read_text(encoding="utf-8") == old_content, (
            "Existing latest was modified"
        )


# ===================================================================
# Existing weekly/report/recommendation behavior unchanged
# ===================================================================


class TestExistingBehaviorUnchanged:
    """Existing weekly, report, and recommendation behavior is preserved."""

    def test_weekly_no_live_still_produces_reports(self, tmp_path: Path) -> None:
        """The --no-live weekly still generates all expected report files."""
        from findjobs.cli import app
        from findjobs.db import init_db
        from findjobs.models import Company, Source, Job, CollectRun

        db_path = tmp_path / "test.db"
        session = init_db(db_path)
        company = Company(name="TestCo", slug="testco")
        session.add(company)
        session.flush()
        source = Source(
            name="Test Source",
            slug="test-source",
            company_id=company.id,
        )
        session.add(source)
        session.flush()
        run = CollectRun(source_id=source.id)
        session.add(run)
        job = Job(
            external_id="ext-keep",
            company_id=company.id,
            source_id=source.id,
            title="Keep Engineer",
            status="active",
        )
        session.add(job)
        session.commit()
        session.close()

        reports_dir = tmp_path / "reports"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--db-path",
                str(db_path),
                "--reports-dir",
                str(reports_dir),
                "--profile",
                str(tmp_path / "missing.md"),
                "--since",
                "365",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Weekly workflow complete" in result.output
        assert (reports_dir / "weekly" / "jobs.jsonl").exists()
        assert (reports_dir / "weekly" / "jobs.csv").exists()


# ===================================================================
# NEW tests: stale lock CLI validation
# ===================================================================


class TestStaleLockValidation:
    """CLI validation for --stale-lock-hours."""

    def test_stale_lock_hours_zero_rejected(self) -> None:
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--stale-lock-hours",
                "0",
                "--db-path",
                "/tmp/test.db",
                "--reports-dir",
                "/tmp/reports",
                "--profile",
                "/tmp/missing.md",
                "--since",
                "365",
            ],
        )
        assert result.exit_code != 0
        assert "positive" in result.output.lower()

    def test_stale_lock_hours_negative_rejected(self) -> None:
        from findjobs.cli import app

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "weekly",
                "--no-live",
                "--stale-lock-hours",
                "-1",
                "--db-path",
                "/tmp/test.db",
                "--reports-dir",
                "/tmp/reports",
                "--profile",
                "/tmp/missing.md",
                "--since",
                "365",
            ],
        )
        assert result.exit_code != 0
        assert "positive" in result.output.lower()

    def test_stale_lock_hours_default_is_24(self) -> None:
        """The default stale-lock-hours is 24 (hours)."""
        from findjobs.weekly_runtime import ProcessLock

        lock = ProcessLock(
            Path("/tmp/test.lock"),
            db_path=Path("/tmp/test.db"),
            reports_dir=Path("/tmp/reports"),
            log_path=Path("/tmp/test.log"),
        )
        # Default stale_hours should be 24.0
        assert lock._stale_seconds == 24.0 * 3600


# ===================================================================
# NEW tests: _shorten_error behavior for source errors
# ===================================================================


class TestErrorBounding:
    """Source errors are shortened and single-line."""

    def test_long_multiline_error_bounded(self) -> None:
        """A long multiline error is shortened to a single line of ~240 chars."""
        from findjobs.cli import _shorten_error

        long_error = "\n".join(["line " + str(i) for i in range(100)])
        result = _shorten_error(long_error, 240)
        # Single line
        assert "\n" not in result
        # Within limit
        assert len(result) <= 240
        # Actually, _shorten_error with limit 240 on a long string
        # should truncate with "..."
        # The built string has ~100*6 = 600 chars, so it will be truncated.

    def test_error_does_not_contain_response_body(self) -> None:
        """Shortened error omits response bodies."""
        from findjobs.cli import _shorten_error

        bad = "HTTP 500: " + "x" * 1000
        result = _shorten_error(bad, 240)
        assert len(result) <= 240
        assert "\n" not in result
