"""Transcription history: SQLite store + a styled HTML viewer."""

import datetime
import html
import sqlite3
import subprocess
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
    words INTEGER NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    config.CONFIG_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(config.HISTORY_DB)
    conn.execute(_SCHEMA)
    return conn


def add(raw: str, text: str, app: str | None, mode: str = "dictate") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO transcripts (ts, app, mode, raw, text, words) VALUES (?,?,?,?,?,?)",
            (time.time(), app, mode, raw, text, len(text.split())),
        )


def recent(n: int = 5) -> list[tuple[float, str, str]]:
    """Returns [(ts, app, text)] newest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ts, app, text FROM transcripts ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
    return rows


def words_this_week() -> int:
    now = datetime.datetime.now()
    week_start = (now - datetime.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    with _conn() as conn:
        (total,) = conn.execute(
            "SELECT COALESCE(SUM(words), 0) FROM transcripts WHERE ts >= ?",
            (week_start.timestamp(),),
        ).fetchone()
    return total


_VIEWER_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Quill — History</title>
<style>
  :root {{ --paper:#f7f2ea; --ink:#17171c; --muted:#8a8378; --accent:#ff7340; }}
  * {{ box-sizing:border-box; margin:0; }}
  body {{ background:var(--paper); color:var(--ink);
         font:15px/1.55 -apple-system, "SF Pro Text", Helvetica, sans-serif; padding:48px 24px; }}
  main {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:28px; letter-spacing:-0.02em; margin-bottom:4px; }}
  h1 span {{ color:var(--accent); }}
  .sub {{ color:var(--muted); margin-bottom:28px; }}
  input {{ width:100%; padding:12px 16px; font-size:15px; border:1px solid #e0d8cb;
           border-radius:12px; background:#fffdf9; margin-bottom:24px; outline:none; }}
  input:focus {{ border-color:var(--accent); }}
  .entry {{ background:#fffdf9; border:1px solid #e8e1d4; border-radius:14px;
            padding:16px 18px; margin-bottom:12px; }}
  .meta {{ display:flex; gap:10px; align-items:center; color:var(--muted);
           font-size:12px; margin-bottom:8px; }}
  .badge {{ background:var(--ink); color:var(--paper); border-radius:99px;
            padding:1px 9px; font-size:11px; }}
  .badge.command {{ background:var(--accent); }}
  .text {{ white-space:pre-wrap; }}
  button {{ margin-left:auto; border:1px solid #e0d8cb; background:transparent;
            border-radius:8px; padding:3px 10px; font-size:12px; cursor:pointer; color:var(--muted); }}
  button:hover {{ color:var(--ink); border-color:var(--ink); }}
</style></head>
<body><main>
  <h1>🪶 Quill <span>history</span></h1>
  <div class="sub">{count} transcriptions · {words:,} words all-time</div>
  <input id="q" placeholder="Search transcripts…" oninput="filter()">
  <div id="list">{entries}</div>
</main>
<script>
  function filter() {{
    const q = document.getElementById('q').value.toLowerCase();
    for (const el of document.querySelectorAll('.entry'))
      el.style.display = el.innerText.toLowerCase().includes(q) ? '' : 'none';
  }}
  function copyText(btn) {{
    navigator.clipboard.writeText(btn.closest('.entry').querySelector('.text').innerText);
    btn.textContent = 'Copied ✓'; setTimeout(() => btn.textContent = 'Copy', 1200);
  }}
</script></body></html>
"""

_ENTRY_TEMPLATE = """<div class="entry">
  <div class="meta"><span class="badge{cmd}">{mode}</span><span>{when}</span><span>{app}</span>
  <button onclick="copyText(this)">Copy</button></div>
  <div class="text">{text}</div>
</div>"""


def open_viewer() -> None:
    """Render all history to a styled HTML page and open it."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT ts, app, mode, text, words FROM transcripts ORDER BY ts DESC"
        ).fetchall()
    entries = "".join(
        _ENTRY_TEMPLATE.format(
            cmd=" command" if mode == "command" else "",
            mode=mode,
            when=datetime.datetime.fromtimestamp(ts).strftime("%b %-d, %-I:%M %p"),
            app=html.escape(app or ""),
            text=html.escape(text),
        )
        for ts, app, mode, text, words in rows
    ) or '<div class="sub">Nothing yet — hold Right ⌥ and say something.</div>'
    page = _VIEWER_TEMPLATE.format(
        count=len(rows), words=sum(r[4] for r in rows), entries=entries
    )
    out = config.CONFIG_DIR / "history.html"
    out.write_text(page)
    subprocess.run(["open", str(out)])
