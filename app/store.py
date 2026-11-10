"""Benchmark results in a local SQLite file (data/results.db).

Each result records which Mac it ran on, so results exported from one Mac and imported on another
can be compared side by side.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import system

DB = Path(os.environ.get("OLLAMA_LAB_DB") or Path(__file__).resolve().parent.parent / "data" / "results.db")
EXPORT_VERSION = 1


def machine() -> dict:
    s = system.snapshot()
    return {"chip": s["chip"], "ram_gb": s["ram_gb"], "gpu_limit_gb": s["gpu_limit_gb"], "macos": s["macos"],
            "label": machine_label(s["chip"], s["ram_gb"])}


def machine_label(chip: str | None, ram_gb: float | None) -> str:
    return f"{chip or 'Unknown'} · {round(ram_gb or 0)} GB"


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
    cols = {r["name"] for r in c.execute("PRAGMA table_info(results)")}
    if "machine" not in cols:
        c.execute("ALTER TABLE results ADD COLUMN machine TEXT")
    # Rows without a Mac were saved locally by an older version (imports always carry one).
    if c.execute("SELECT 1 FROM results WHERE machine IS NULL LIMIT 1").fetchone():
        c.execute("UPDATE results SET machine = ? WHERE machine IS NULL", (json.dumps(machine()),))
        c.commit()
    return c


def save(run_id: str, model: str, settings: dict, metrics: dict, details: dict) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO results (run_id, model, created_at, settings, metrics, details, machine) VALUES (?,?,?,?,?,?,?)",
            (run_id, model, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(settings), json.dumps(metrics), json.dumps(details), json.dumps(machine())))
        return cur.lastrowid


def _row(r: sqlite3.Row, with_details: bool) -> dict:
    out = {"id": r["id"], "run_id": r["run_id"], "model": r["model"], "created_at": r["created_at"],
           "settings": json.loads(r["settings"]), "metrics": json.loads(r["metrics"]),
           "machine": json.loads(r["machine"] or "{}")}
    if with_details:
        out["details"] = json.loads(r["details"])
    return out


def latest_per_model() -> list[dict]:
    """The newest result for each model on each Mac."""
    with _conn() as c:
        rows = c.execute("""SELECT * FROM results WHERE id IN (
            SELECT MAX(id) FROM results GROUP BY model, json_extract(machine, '$.label'))""").fetchall()
    return [_row(r, False) for r in rows]


def history(limit: int = 500) -> list[dict]:
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


def export_all() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT * FROM results ORDER BY id").fetchall()
    return {"format": "ollama-lab-results", "version": EXPORT_VERSION, "exported_by": machine(),
            "results": [{k: v for k, v in _row(r, True).items() if k != "id"} for r in rows]}


def import_all(data: dict) -> dict:
    """Add results from another Mac's export. Results already present (same run, model, time) are skipped."""
    if data.get("format") != "ollama-lab-results":
        raise ValueError("Not an Ollama Lab results file")
    added = skipped = 0
    with _conn() as c:
        for r in data.get("results", []):
            if not all(k in r for k in ("run_id", "model", "created_at", "metrics")):
                skipped += 1
                continue
            if c.execute("SELECT 1 FROM results WHERE run_id = ? AND model = ? AND created_at = ?",
                         (r["run_id"], r["model"], r["created_at"])).fetchone():
                skipped += 1
                continue
            m = r.get("machine") or data.get("exported_by") or {}
            m.setdefault("label", machine_label(m.get("chip"), m.get("ram_gb")))
            c.execute(
                "INSERT INTO results (run_id, model, created_at, settings, metrics, details, machine) VALUES (?,?,?,?,?,?,?)",
                (r["run_id"], r["model"], r["created_at"], json.dumps(r.get("settings", {})),
                 json.dumps(r["metrics"]), json.dumps(r.get("details", {})), json.dumps(m)))
            added += 1
    return {"added": added, "skipped": skipped}
