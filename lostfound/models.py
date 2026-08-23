"""The report model and its validation.

One deliberate choice: validation returns *errors* (reject the write) and
*warnings* (accept, but tell the human something is off). A lost-and-found desk
that refuses a report because the student cannot remember the exact hour is
worse than useless, so vagueness is a warning and only genuinely impossible
input is an error.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .text import tokenize

KINDS = ("lost", "found")
STATUSES = ("open", "resolved", "withdrawn")
PRECISIONS = ("exact", "day", "unknown")

MAX_DESCRIPTION = 2000
MAX_IDENTIFIERS = 5
CLOCK_SKEW = timedelta(minutes=5)
MAX_AGE = timedelta(days=730)

ALLOWED_FIELDS = frozenset({
    "kind", "title", "description", "item_type", "color", "location",
    "occurred_at", "identifiers", "contact", "reporter",
})


class ValidationError(ValueError):
    def __init__(self, fields: dict, message: str = "The report could not be accepted."):
        super().__init__(message)
        self.fields = fields
        self.message = message


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(kind: str) -> str:
    return f"{'L' if kind == 'lost' else 'F'}-{secrets.token_hex(3).upper()}"


def parse_timestamp(value):
    """Returns (datetime|None, precision). Accepts a date, or a datetime with or
    without a timezone; naive input is read as UTC.

    Free-text dates ("yesterday", "Monday afternoon") are intentionally not
    parsed here - see the README. The UI asks for a date instead, which is
    cheaper and far more reliable than guessing.
    """
    if value in (None, "", "unknown"):
        return None, "unknown"
    if isinstance(value, datetime):
        parsed, precision = value, "exact"
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        precision = "day" if len(raw) <= 10 else "exact"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            raise ValidationError({"occurred_at": "Use a date like 2026-08-20 or 2026-08-20T14:30."})
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if precision == "day":
        parsed = parsed.replace(hour=12, minute=0, second=0, microsecond=0)
    now = utcnow()
    if parsed > now + CLOCK_SKEW:
        raise ValidationError({"occurred_at": "That is in the future."})
    if parsed < now - MAX_AGE:
        raise ValidationError({"occurred_at": "That is more than two years ago; check the year."})
    return parsed, precision


@dataclass
class Report:
    kind: str
    description: str
    id: str = ""
    title: str = ""
    item_type: str = ""
    color: str = ""
    location: str = ""
    occurred_at: datetime = None
    time_precision: str = "unknown"
    identifiers: list = field(default_factory=list)
    contact: str = ""
    reporter: str = ""
    status: str = "open"
    created_at: datetime = None
    matched_with: str = ""

    def __post_init__(self):
        self.id = self.id or new_id(self.kind)
        self.created_at = self.created_at or utcnow()
        self.title = self.title or self.derived_title()

    def derived_title(self) -> str:
        """Short label for lists. Truncates on a word boundary, because cutting
        mid-word invents tokens ("libr") that then pollute matching."""
        words, head = " ".join(self.description.split()).split(" "), []
        for word in words:
            if sum(len(w) + 1 for w in head) + len(word) > 64 and head:
                return " ".join(head) + "..."
            head.append(word)
        return " ".join(head)

    def searchable(self) -> str:
        """Everything a matcher may read as prose, structured fields included."""
        return " ".join(p for p in (self.title, self.description, self.item_type,
                                    self.color, self.location) if p)

    def to_dict(self, include_contact: bool = False) -> dict:
        data = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "item_type": self.item_type,
            "color": self.color,
            "location": self.location,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "time_precision": self.time_precision,
            "identifiers": list(self.identifiers),
            "reporter": self.reporter,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "matched_with": self.matched_with or None,
            "has_contact": bool(self.contact),
        }
        # Contact details are withheld until a match is confirmed: publishing a
        # phone number next to "lost: iPhone 15" is a shopping list for thieves.
        if include_contact:
            data["contact"] = self.contact
        return data


def build_report(payload, kind=None):
    """Validate an untrusted payload into a Report. Returns (report, warnings).

    Raises ValidationError with a per-field map so the UI can point at the
    offending input instead of showing a generic "something went wrong".
    """
    if not isinstance(payload, dict):
        raise ValidationError({"_": "Expected a JSON object."})

    errors, warnings = {}, []

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        errors["_"] = f"Unknown field(s): {', '.join(unknown)}"

    kind = (kind or payload.get("kind") or "").strip().lower()
    if kind not in KINDS:
        errors["kind"] = "Must be either 'lost' or 'found'."

    description = str(payload.get("description") or "").strip()
    description = " ".join(description.split())
    if not description:
        errors["description"] = "Tell us what the item is."
    elif len(description) < 3:
        errors["description"] = "That is too short to match against."
    elif len(description) > MAX_DESCRIPTION:
        warnings.append(f"Description was truncated to {MAX_DESCRIPTION} characters.")
        description = description[:MAX_DESCRIPTION]

    meaningful = tokenize(description)
    if description and not meaningful:
        errors["description"] = "No describable detail found. What is the item?"
    elif len(meaningful) == 1:
        warnings.append("Only one describable word: add a colour, brand or place to match better.")

    occurred_at, precision = None, "unknown"
    if "occurred_at" in payload:
        try:
            occurred_at, precision = parse_timestamp(payload.get("occurred_at"))
        except ValidationError as exc:
            errors.update(exc.fields)
    if occurred_at is None and "occurred_at" not in errors:
        warnings.append("No date given, so timing will not be used for matching.")

    identifiers = payload.get("identifiers") or []
    if isinstance(identifiers, str):
        identifiers = [part for part in identifiers.replace(";", ",").split(",") if part.strip()]
    if not isinstance(identifiers, list):
        errors["identifiers"] = "Expected a list of values."
        identifiers = []
    identifiers = [str(v).strip()[:64] for v in identifiers if str(v).strip()][:MAX_IDENTIFIERS]

    contact = str(payload.get("contact") or "").strip()[:120]
    if not contact:
        warnings.append("No contact detail: the desk will not be able to reach you.")

    if errors:
        raise ValidationError(errors)

    report = Report(
        kind=kind,
        description=description,
        title=str(payload.get("title") or "").strip()[:120],
        item_type=str(payload.get("item_type") or "").strip()[:60],
        color=str(payload.get("color") or "").strip()[:60],
        location=str(payload.get("location") or "").strip()[:120],
        occurred_at=occurred_at,
        time_precision=precision,
        identifiers=identifiers,
        contact=contact,
        reporter=str(payload.get("reporter") or "").strip()[:80],
    )
    return report, warnings
