"""SQLite persistence. Chosen because it is in the standard library, needs no
service to run, and gives the reviewer a real database with real constraints
for free. Swapping in Postgres would mean rewriting this one file.

The schema is small on purpose. `match_decisions` is the interesting table: it
remembers what a human decided about a pair so the system never asks twice.
That is also the training data a future ranking model would need, which is why
it is captured from day one even though nothing learns from it yet.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from .config import CONFIG
from .models import Report
from .text import similarity

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('lost', 'found')),
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL,
    item_type      TEXT NOT NULL DEFAULT '',
    color          TEXT NOT NULL DEFAULT '',
    location       TEXT NOT NULL DEFAULT '',
    occurred_at    TEXT,
    time_precision TEXT NOT NULL DEFAULT 'unknown',
    identifiers    TEXT NOT NULL DEFAULT '[]',
    contact        TEXT NOT NULL DEFAULT '',
    reporter       TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'resolved', 'withdrawn')),
    matched_with   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_kind_status ON reports (kind, status);

CREATE TABLE IF NOT EXISTS match_decisions (
    lost_id    TEXT NOT NULL,
    found_id   TEXT NOT NULL,
    decision   TEXT NOT NULL CHECK (decision IN ('confirmed', 'rejected')),
    note       TEXT NOT NULL DEFAULT '',
    score      INTEGER,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (lost_id, found_id)
);
"""


def _to_row(report: Report):
    return (
        report.id, report.kind, report.title, report.description, report.item_type,
        report.color, report.location,
        report.occurred_at.isoformat() if report.occurred_at else None,
        report.time_precision, json.dumps(report.identifiers), report.contact,
        report.reporter, report.status, report.matched_with, report.created_at.isoformat(),
    )


def _from_row(row) -> Report:
    return Report(
        id=row["id"], kind=row["kind"], title=row["title"], description=row["description"],
        item_type=row["item_type"], color=row["color"], location=row["location"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]) if row["occurred_at"] else None,
        time_precision=row["time_precision"], identifiers=json.loads(row["identifiers"]),
        contact=row["contact"], reporter=row["reporter"], status=row["status"],
        matched_with=row["matched_with"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class Store:
    def __init__(self, path=":memory:"):
        self.path = path
        # One connection guarded by a lock: the request volume of a university
        # lost-and-found desk does not justify a pool, and this keeps writes
        # serialised and obviously correct.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # -- reports -----------------------------------------------------------
    def add(self, report: Report) -> Report:
        with self._lock:
            self._db.execute(
                "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _to_row(report)
            )
            self._db.commit()
        return report

    def get(self, report_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _from_row(row) if row else None

    def list(self, kind=None, status="open", query=None, limit=200, offset=0):
        sql = "SELECT * FROM reports WHERE 1=1"
        args = []
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if status and status != "all":
            sql += " AND status = ?"
            args.append(status)
        if query:
            sql += " AND (description LIKE ? OR location LIKE ? OR title LIKE ?)"
            args += [f"%{query}%"] * 3
        sql += " ORDER BY datetime(created_at) DESC LIMIT ? OFFSET ?"
        args += [max(1, min(int(limit), 500)), max(0, int(offset))]
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [_from_row(r) for r in rows]

    def open_reports(self):
        return self.list(status="open", limit=500)

    def set_status(self, report_id, status, matched_with=""):
        with self._lock:
            self._db.execute(
                "UPDATE reports SET status = ?, matched_with = ? WHERE id = ?",
                (status, matched_with, report_id),
            )
            self._db.commit()
        return self.get(report_id)

    def counts(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, status, COUNT(*) AS n FROM reports GROUP BY kind, status"
            ).fetchall()
        out = {"lost_open": 0, "found_open": 0, "resolved": 0, "withdrawn": 0}
        for row in rows:
            if row["status"] == "open":
                out[f"{row['kind']}_open"] += row["n"]
            else:
                out[row["status"]] += row["n"]
        return out

    # -- human decisions ---------------------------------------------------
    def record_decision(self, lost_id, found_id, decision, note="", score=None):
        with self._lock:
            self._db.execute(
                "INSERT INTO match_decisions (lost_id, found_id, decision, note, score, decided_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(lost_id, found_id) DO UPDATE SET"
                " decision=excluded.decision, note=excluded.note, decided_at=excluded.decided_at",
                (lost_id, found_id, decision, note, score,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._db.commit()

    def decisions(self):
        with self._lock:
            rows = self._db.execute("SELECT * FROM match_decisions").fetchall()
        return {(r["lost_id"], r["found_id"]): dict(r) for r in rows}

    def rejected_pairs(self):
        return {pair for pair, row in self.decisions().items() if row["decision"] == "rejected"}

    def confirmed_pairs(self):
        return {pair: row for pair, row in self.decisions().items()
                if row["decision"] == "confirmed"}

    # -- duplicate guard ---------------------------------------------------
    def possible_duplicate(self, report: Report, config=CONFIG):
        """Same person filing the same thing twice, or a double form submit.

        Returns the earlier report or None. This only ever produces a warning:
        two students really can lose two black chargers on the same afternoon.
        """
        cutoff = (report.created_at - timedelta(hours=config.duplicate_window_hours)).isoformat()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM reports WHERE kind = ? AND id != ? AND created_at >= ?",
                (report.kind, report.id, cutoff),
            ).fetchall()
        for row in rows:
            other = _from_row(row)
            if report.reporter and other.reporter and report.reporter != other.reporter:
                continue
            if similarity(report.description, other.description) >= config.duplicate_similarity:
                return other
        return None
