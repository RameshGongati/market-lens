"""Tests for the settings page's storage stats and monitor probe.

Both modules read the REAL app directory (``~/.market-lens``) and the real
single-instance lock, so every test here redirects them at ``tmp_path`` first
— the same discipline the bhavcopy tests use (Gotcha 19). Without it a test
would report on, or delete from, the user's own cache.
"""

from __future__ import annotations

import os
import sys

import pytest

from alerts import monitor_control
from utils import system_info


# ---------------------------------------------------------------------------
# format_bytes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "size,expected",
    [
        (0, "0 B"),
        (1, "1 B"),
        (1023, "1023 B"),          # one below the KB boundary
        (1024, "1.0 KB"),          # exactly at it
        (1536, "1.5 KB"),
        (1024 * 1024 - 1, "1024.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (28_705_000, "27.4 MB"),
        (1024 ** 3, "1.0 GB"),
        (5 * 1024 ** 4, "5120.0 GB"),   # past GB it keeps scaling, not wraps
    ],
)
def test_format_bytes(size, expected):
    assert system_info.format_bytes(size) == expected


# ---------------------------------------------------------------------------
# dir_size / storage_stats
# ---------------------------------------------------------------------------

def test_dir_size_missing_directory_is_zero(tmp_path):
    assert system_info.dir_size(tmp_path / "nope") == 0


def test_dir_size_counts_nested_files(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "b.txt").write_bytes(b"y" * 250)
    assert system_info.dir_size(tmp_path) == 350


def test_storage_stats_separates_db_cache_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "APP_DIR", tmp_path)
    (tmp_path / "market_lens.db").write_bytes(b"d" * 500)
    for name in ("bhavcopy", "earnings"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.json").write_bytes(b"c" * 100)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_bytes(b"l" * 700)

    stats = system_info.storage_stats()

    assert stats["db_bytes"] == 500
    assert stats["cache_bytes"] == 200          # both cache dirs, not the logs
    assert stats["log_bytes"] == 700
    assert stats["total_bytes"] == 1400


def test_storage_stats_on_empty_dir_reports_zero_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "APP_DIR", tmp_path)
    stats = system_info.storage_stats()
    assert stats["total_bytes"] == 0
    assert stats["db_modified"] is None


# ---------------------------------------------------------------------------
# clear_caches
# ---------------------------------------------------------------------------

def test_clear_caches_removes_only_cache_files(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "APP_DIR", tmp_path)
    (tmp_path / "market_lens.db").write_bytes(b"keep me")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_bytes(b"keep me too")
    for name in ("bhavcopy", "earnings"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "one.json").write_bytes(b"{}")
        (tmp_path / name / "two.json").write_bytes(b"{}")

    removed = system_info.clear_caches()

    assert removed == 4
    assert (tmp_path / "market_lens.db").exists()       # database untouched
    assert (tmp_path / "logs" / "app.log").exists()     # logs untouched
    # Directories survive so the next fetch does not have to recreate them.
    assert (tmp_path / "bhavcopy").is_dir()
    assert system_info.dir_size(tmp_path / "earnings") == 0


def test_clear_caches_with_nothing_cached_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "APP_DIR", tmp_path)
    assert system_info.clear_caches() == 0


# ---------------------------------------------------------------------------
# Monitor lock probe
# ---------------------------------------------------------------------------

pytestmark_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="flock is POSIX-only"
)


@pytestmark_posix
def test_no_lock_file_means_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", tmp_path / "absent.lock")
    assert monitor_control.is_running() is False
    assert monitor_control.monitor_pid() is None


@pytestmark_posix
def test_unheld_lock_file_means_not_running(tmp_path, monkeypatch):
    """A lock file left behind by a dead monitor must not read as running.

    This is the whole reason the probe uses flock rather than the PID inside
    the file: the PID is still there, and still parses, after the process it
    named has gone.
    """
    lock = tmp_path / "alert_monitor.lock"
    lock.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", lock)

    assert monitor_control.is_running() is False
    assert monitor_control.monitor_pid() is None


@pytestmark_posix
def test_held_lock_reports_running_and_returns_pid(tmp_path, monkeypatch):
    import fcntl

    lock = tmp_path / "alert_monitor.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", lock)

    holder = open(lock, "r+", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert monitor_control.is_running() is True
        assert monitor_control.monitor_pid() == os.getpid()
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()

    # Released — and the probe must notice, not cache the earlier answer.
    assert monitor_control.is_running() is False


@pytestmark_posix
def test_probe_does_not_keep_the_lock(tmp_path, monkeypatch):
    """Probing must leave the lock free.

    The probe takes the lock to find out whether it was free. Failing to
    release it would make the very next ``start_monitor`` see a duplicate and
    refuse to start — the page's own status check would break the button
    beside it.
    """
    import fcntl

    lock = tmp_path / "alert_monitor.lock"
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", lock)

    monitor_control.is_running()

    handle = open(lock, "r", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)   # must not raise
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


@pytestmark_posix
def test_garbage_pid_in_lock_file_is_not_signalled(tmp_path, monkeypatch):
    """A non-numeric lock file yields no PID, so nothing gets killed."""
    import fcntl

    lock = tmp_path / "alert_monitor.lock"
    lock.write_text("not-a-pid", encoding="utf-8")
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", lock)

    holder = open(lock, "r+", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert monitor_control.is_running() is True
        assert monitor_control.monitor_pid() is None
        ok, message = monitor_control.stop_monitor()
        assert ok is False
        assert "No monitor" in message
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


@pytestmark_posix
def test_start_is_refused_while_a_monitor_holds_the_lock(tmp_path, monkeypatch):
    import fcntl

    lock = tmp_path / "alert_monitor.lock"
    lock.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(monitor_control, "_LOCK_FILE", lock)

    holder = open(lock, "r+", encoding="utf-8")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        ok, message = monitor_control.start_monitor()
        assert ok is False
        assert "already running" in message
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
