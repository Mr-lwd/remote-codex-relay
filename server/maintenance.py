"""Cross-process maintenance gate shared by HTTP writes and local management."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3


@contextmanager
def maintenance_lock(data_dir, *, exclusive=False):
    path = Path(data_dir) / ".maintenance.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def busy_counts(data_dir):
    path = Path(data_dir) / "relay.sqlite"
    result = {"jobs": 0, "pending": 0, "commands": 0}
    if not path.exists():
        return result
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for key, table, condition in (
            ("jobs", "jobs", "status IN ('starting','running')"),
            ("pending", "pending", "status='queued'"),
            ("commands", "command_records", "status='starting' AND job_id IS NULL"),
        ):
            if table in tables:
                result[key] = db.execute(
                    f"SELECT count(*) FROM {table} WHERE {condition}"
                ).fetchone()[0]
        if "input_replies" in tables:
            result["pending"] += db.execute(
                "SELECT count(*) FROM input_replies WHERE status='sending'"
            ).fetchone()[0]
    return result
