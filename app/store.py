"""Benchmark results in a local SQLite file (data/results.db)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "results.db"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TEXT NOT NULL,
        settings TEXT NOT NULL,
        metrics TEXT NOT NULL,
        details TEXT NOT NULL)""")
    return c


def save(run_id: str, model: str, settings: dict, metrics: dict, details: dict) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO results (run_id, model, created_at, settings, metrics, details) VALUES (?,?,?,?,?,?)",
            (run_id, model, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(settings), json.dumps(metrics), json.dumps(details)))
        return cur.lastrowid


def _row(r: sqlite3.Row, with_details: bool) -> dict:
    out = {"id": r["id"], "run_id": r["run_id"], "model": r["model"], "created_at": r["created_at"],
           "settings": json.loads(r["settings"]), "metrics": json.loads(r["metrics"])}
    if with_details:
        out["details"] = json.loads(r["details"])
    return out


def latest_per_model() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""SELECT * FROM results WHERE id IN (SELECT MAX(id) FROM results GROUP BY model)""").fetchall()
    return [_row(r, False) for r in rows]


def history(limit: int = 200) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM results ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r, False) for r in rows]


def get(result_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
    return _row(r, True) if r else None


def delete(result_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM results WHERE id = ?", (result_id,))
