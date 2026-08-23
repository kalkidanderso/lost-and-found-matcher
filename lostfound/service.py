"""Use cases, kept away from HTTP.

The server is a thin translation layer over this class, which is why the CLI and
the tests can drive the whole product without a socket. Every method here takes
and returns plain data.
"""

from __future__ import annotations

from .config import CONFIG
from .matching import MATCHER, Match
from .models import Report, ValidationError, build_report
from .places import CAMPUS


class Conflict(Exception):
    """The request was understood but contradicts the current state."""


class NotFound(Exception):
    pass


class LostFoundService:
    def __init__(self, store, matcher=MATCHER, config=CONFIG):
        self.store = store
        self.matcher = matcher
        self.config = config

    # -- writes ------------------------------------------------------------
    def create_report(self, payload, kind=None):
        report, warnings = build_report(payload, kind=kind)
        duplicate = self.store.possible_duplicate(report, self.config)
        if duplicate:
            warnings.append(
                f"This looks like a duplicate of {duplicate.id} filed recently. "
                "It was still accepted - check before filing again."
            )
        self.store.add(report)
        return {
            "report": report.to_dict(),
            "warnings": warnings,
            "duplicate_of": duplicate.id if duplicate else None,
            # The point of the product: you find out immediately, at the moment
            # of filing, whether the other half of the pair already exists.
            "matches": self.matches_for(report.id)["matches"],
        }

    def withdraw(self, report_id):
        report = self._require(report_id)
        if report.status == "resolved":
            raise Conflict(f"{report_id} is already resolved and cannot be withdrawn.")
        return self.store.set_status(report_id, "withdrawn").to_dict()

    def decide(self, lost_id, found_id, decision, note=""):
        if decision not in ("confirmed", "rejected"):
            raise ValidationError({"decision": "Must be 'confirmed' or 'rejected'."})
        lost, found = self._require(lost_id), self._require(found_id)
        if lost.kind != "lost" or found.kind != "found":
            raise ValidationError({"_": "Expected one lost report and one found report."})
        if decision == "confirmed":
            for report in (lost, found):
                if report.status != "open":
                    raise Conflict(f"{report.id} is no longer open (status: {report.status}).")

        outcome = self.matcher.explain(lost, found, self.store.open_reports())
        score = round(outcome.score * 100) if isinstance(outcome, Match) else None
        self.store.record_decision(lost_id, found_id, decision, note, score)

        if decision == "confirmed":
            self.store.set_status(lost_id, "resolved", found_id)
            self.store.set_status(found_id, "resolved", lost_id)
        return {
            "lost_id": lost_id,
            "found_id": found_id,
            "decision": decision,
            "score": score,
            # Contact details are released only now, and only for a confirmed
            # pair. Before that the desk has no business publishing them.
            "contact": (
                {"lost": lost.contact or None, "found": found.contact or None}
                if decision == "confirmed" else None
            ),
        }

    # -- reads -------------------------------------------------------------
    def _require(self, report_id) -> Report:
        report = self.store.get(report_id)
        if not report:
            raise NotFound(f"No report with id {report_id}.")
        return report

    def get_report(self, report_id):
        return self._require(report_id).to_dict()

    def list_reports(self, kind=None, status="open", query=None, limit=200, offset=0):
        reports = self.store.list(kind=kind, status=status, query=query,
                                  limit=limit, offset=offset)
        return {"reports": [r.to_dict() for r in reports], "count": len(reports)}

    def matches_for(self, report_id, include_rejections=False):
        report = self._require(report_id)
        pool = self.store.open_reports()
        suppressed = self.store.rejected_pairs()
        if report.status != "open":
            return {"report": report.to_dict(), "matches": [],
                    "note": f"This report is {report.status}, so it is no longer being matched."}
        result = self.matcher.matches_for(report, pool, suppressed, include_rejections)
        matches, rejections = result if include_rejections else (result, [])
        return {
            "report": report.to_dict(),
            "matches": [self._decorate(m) for m in matches],
            "ruled_out": [r.as_dict() for r in rejections],
        }

    def board(self, band=None, limit=60):
        """Every open pair worth a human's attention, best first."""
        matches = self.matcher.all_matches(self.store.open_reports(), self.store.rejected_pairs())
        if band:
            matches = [m for m in matches if m.band == band]
        decorated = [self._decorate(m) for m in matches[:limit]]
        return {
            "matches": decorated,
            "total": len(matches),
            "by_band": {
                b: sum(1 for m in matches if m.band == b)
                for b in ("strong", "possible", "weak")
            },
        }

    def resolved(self):
        out = []
        for (lost_id, found_id), row in sorted(
            self.store.confirmed_pairs().items(), key=lambda kv: kv[1]["decided_at"], reverse=True
        ):
            lost, found = self.store.get(lost_id), self.store.get(found_id)
            if lost and found:
                out.append({"lost": lost.to_dict(), "found": found.to_dict(),
                            "score": row["score"], "decided_at": row["decided_at"],
                            "note": row["note"]})
        return {"reunions": out}

    def stats(self):
        counts = self.store.counts()
        board = self.board()
        return {"reports": counts, "matches": board["by_band"], "open_pairs": board["total"]}

    def meta(self):
        return {"config": self.config.as_dict(), "places": CAMPUS.as_options()}

    # -- helpers -----------------------------------------------------------
    def _decorate(self, match: Match):
        data = match.as_dict()
        lost, found = self.store.get(match.lost_id), self.store.get(match.found_id)
        data["lost"] = lost.to_dict() if lost else None
        data["found"] = found.to_dict() if found else None
        return data
