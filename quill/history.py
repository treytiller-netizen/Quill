"""Transcription history: SQLite store + aggregate queries for the Hub."""

import datetime
import sqlite3
import time

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    app TEXT,
    mode TEXT NOT NULL DEFAULT 'dictate',
    raw TEXT NOT NULL,
    text TEXT NOT NULL,
    words INTEGER NOT NULL,
    duration REAL
);
"""


def _conn() -> sqlite3.Connection:
    config.CONFIG_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(config.HISTORY_DB)
    conn.execute(_SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(transcripts)")}
    if "duration" not in cols:  # migrate pre-0.3 databases
        conn.execute("ALTER TABLE transcripts ADD COLUMN duration REAL")
    return conn


def add(raw: str, text: str, app: str | None, mode: str = "dictate",
        duration: float | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO transcripts (ts, app, mode, raw, text, words, duration)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), app, mode, raw, text, len(text.split()), duration),
        )


def recent(n: int = 5) -> list[tuple[float, str, str]]:
    """Returns [(ts, app, text)] newest first."""
    with _conn() as conn:
        return conn.execute(
            "SELECT ts, app, text FROM transcripts ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()


def entries(limit: int = 500) -> list[dict]:
    """Full rows for the Hub feed, newest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ts, app, mode, raw, text, words, duration FROM transcripts"
            " ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        dict(ts=ts, app=app or "", mode=mode, raw=raw, text=text, words=words,
             duration=duration or 0)
        for ts, app, mode, raw, text, words, duration in rows
    ]


def _week_start() -> float:
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start.timestamp()


def words_this_week() -> int:
    with _conn() as conn:
        (total,) = conn.execute(
            "SELECT COALESCE(SUM(words), 0) FROM transcripts WHERE ts >= ?",
            (_week_start(),),
        ).fetchone()
    return total


def totals() -> dict:
    with _conn() as conn:
        n, words, spoken = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(words),0), COALESCE(SUM(duration),0)"
            " FROM transcripts"
        ).fetchone()
        wpm_words, wpm_secs = conn.execute(
            "SELECT COALESCE(SUM(words),0), COALESCE(SUM(duration),0) FROM transcripts"
            " WHERE mode='dictate' AND duration > 1"
        ).fetchone()
    return dict(
        count=n,
        words=words,
        spoken_seconds=spoken,
        week_words=words_this_week(),
        wpm=round(wpm_words / (wpm_secs / 60)) if wpm_secs > 30 else None,
    )


def words_by_day(days: int = 182) -> dict[str, int]:
    """{'YYYY-MM-DD': words} for the streak heatmap."""
    since = time.time() - days * 86400
    with _conn() as conn:
        rows = conn.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') AS d, SUM(words)"
            " FROM transcripts WHERE ts >= ? GROUP BY d",
            (since,),
        ).fetchall()
    return dict(rows)


def app_usage() -> list[tuple[str, int]]:
    """[(app, words)] descending."""
    with _conn() as conn:
        return conn.execute(
            "SELECT COALESCE(app,'Unknown'), SUM(words) AS w FROM transcripts"
            " GROUP BY app ORDER BY w DESC LIMIT 8"
        ).fetchall()


def all_text(limit_chars: int = 24_000) -> str:
    """Recent cleaned text, newest first, capped — feed for voice analysis."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT text FROM transcripts WHERE mode='dictate' ORDER BY ts DESC LIMIT 300"
        ).fetchall()
    out: list[str] = []
    size = 0
    for (text,) in rows:
        size += len(text) + 1
        if size > limit_chars:
            break
        out.append(text)
    return "\n".join(out)


def raw_clean_pairs(limit: int = 400) -> list[tuple[str, str]]:
    with _conn() as conn:
        return conn.execute(
            "SELECT raw, text FROM transcripts WHERE mode='dictate'"
            " ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()


def clear_all() -> None:
    """Delete every transcript and reclaim the file space."""
    with _conn() as conn:
        conn.execute("DELETE FROM transcripts")
    conn = sqlite3.connect(config.HISTORY_DB)
    conn.execute("VACUUM")  # must run outside a transaction
    conn.close()
