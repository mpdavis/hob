import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    group_key   TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    resolved_at REAL
);
CREATE TABLE IF NOT EXISTS alerts (
    fingerprint TEXT PRIMARY KEY,
    group_key   TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Thread:
    group_key: str
    thread_id: str
    resolved_at: float | None


class Store:
    """Which Discord thread each alert group lives in, and which alerts were
    firing at the last successful poll. Survives restarts so a redeploy does
    not repost every open alert."""

    def __init__(self, path: str):
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def thread(self, group_key: str) -> Thread | None:
        row = self._db.execute(
            "SELECT group_key, thread_id, resolved_at FROM threads WHERE group_key = ?",
            (group_key,),
        ).fetchone()
        return Thread(*row) if row else None

    def open_threads(self) -> list[Thread]:
        rows = self._db.execute(
            "SELECT group_key, thread_id, resolved_at FROM threads WHERE resolved_at IS NULL"
        ).fetchall()
        return [Thread(*row) for row in rows]

    def save_thread(self, group_key: str, thread_id: str, resolved_at: float | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO threads (group_key, thread_id, resolved_at) VALUES (?, ?, ?) "
                "ON CONFLICT (group_key) DO UPDATE SET thread_id = excluded.thread_id, "
                "resolved_at = excluded.resolved_at",
                (group_key, thread_id, resolved_at),
            )

    def firing(self, group_key: str) -> set[str]:
        rows = self._db.execute(
            "SELECT fingerprint FROM alerts WHERE group_key = ?", (group_key,)
        ).fetchall()
        return {row[0] for row in rows}

    def set_firing(self, group_key: str, fingerprints: set[str]) -> None:
        with self._db:
            self._db.execute("DELETE FROM alerts WHERE group_key = ?", (group_key,))
            self._db.executemany(
                "INSERT OR REPLACE INTO alerts (fingerprint, group_key) VALUES (?, ?)",
                [(fp, group_key) for fp in fingerprints],
            )
