"""Relay-owned database schema and access password initialization."""

import os
from pathlib import Path
import secrets
import sqlite3


def connect(data_dir):
    connection = sqlite3.connect(Path(data_dir) / "relay.sqlite", timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize(data_dir):
    with connect(data_dir) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, thread_id TEXT, title TEXT,
          cwd TEXT, status TEXT, pid INTEGER, created REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, thread_id TEXT, role TEXT,
          text TEXT, created REAL, kind TEXT);
        CREATE TABLE IF NOT EXISTS pending(id TEXT PRIMARY KEY, thread_id TEXT,
          text TEXT, created REAL, status TEXT);
        CREATE TABLE IF NOT EXISTS command_records(id TEXT PRIMARY KEY, thread_id TEXT,
          command TEXT, input TEXT, status TEXT, message TEXT, job_id TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS input_replies(thread_id TEXT, id TEXT, status TEXT,
          deadline REAL, automatic INTEGER, answered_at REAL, PRIMARY KEY(thread_id,id));
        """)
        for table in ("jobs", "pending"):
            if "model" not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN model TEXT")
            if "effort" not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN effort TEXT")
            if "options" not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                c.execute(f"ALTER TABLE {table} ADD COLUMN options TEXT DEFAULT '{{}}'")


def access_password(data_dir):
    path = Path(data_dir) / "access.txt"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(24) + "\n")
    path.chmod(0o600)
    password = path.read_text().strip()
    if not password or len(password) > 200:
        raise ValueError("Access password must contain 1–200 characters")
    return password
