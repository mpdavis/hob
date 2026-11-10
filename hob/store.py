import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id  INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    day TEXT PRIMARY KEY,
    usd REAL NOT NULL
);
"""


class Store:
    """Which agent session belongs to each Discord thread, so a reply resumes
    the investigation with its context, and what each UTC day has cost."""

    def __init__(self, path: str):
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def session(self, thread_id: int) -> str | None:
        row = self._db.execute(
            "SELECT session_id FROM sessions WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row[0] if row else None

    def save_session(self, thread_id: int, session_id: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO sessions (thread_id, session_id) VALUES (?, ?) "
                "ON CONFLICT (thread_id) DO UPDATE SET session_id = excluded.session_id",
                (thread_id, session_id),
            )

    def spent(self, day: str) -> float:
        row = self._db.execute("SELECT usd FROM spend WHERE day = ?", (day,)).fetchone()
        return row[0] if row else 0.0

    def add_spend(self, day: str, usd: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO spend (day, usd) VALUES (?, ?) "
                "ON CONFLICT (day) DO UPDATE SET usd = usd + excluded.usd",
                (day, usd),
            )
