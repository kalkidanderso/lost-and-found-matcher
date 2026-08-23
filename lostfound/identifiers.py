"""Extracting and comparing unique identifiers.

This is the highest-signal, lowest-effort feature in the whole system. Colours
and locations are guesses; an IMEI is proof. Two consequences, both implemented
in signals.py:

  * a matching identifier promotes a pair straight to STRONG;
  * two *different* identifiers of the same high-precision kind veto the pair
    outright, no matter how well the prose matches.

Low-precision kinds (a name written on a book) never veto, because "marked with
the name Sara" and "has a name on it" are not comparable claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Kinds where a mismatch is conclusive evidence of two different objects.
HIGH_PRECISION = frozenset({"imei", "phone", "email", "student_id", "serial"})

_PATTERNS = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("imei", re.compile(r"\b(?:imei\s*[:#-]?\s*)?(\d{15})\b", re.I)),
    ("phone", re.compile(r"(?:\+?251|\b0)(9\d{8})\b")),
    ("student_id", re.compile(r"\b(?:ugr|ets|stu|id)\s*[:/#-]?\s*(\d{3,}[\d/-]*)\b", re.I)),
    ("serial", re.compile(r"(?:serial(?:\s*(?:number|no|nr|#))?|s\/?n)\s*[:#-]?\s*([A-Za-z0-9-]{5,24})", re.I)),
    # Bare uppercase alphanumerics, e.g. "C02X1234ABCD" pasted from a device.
    ("serial", re.compile(r"\b(?=[A-Z0-9-]*[0-9])(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{6,24}\b")),
    ("name_tag", re.compile(r"(?:name(?:d|\s*tag)?|labelled|labeled|marked|engraved|written)\s*(?:as|with|is|:)?\s*[\"']?([A-Z][a-z]{2,20})")),
)


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str
    raw: str

    def as_dict(self):
        return {"kind": self.kind, "value": self.value}


def _canonical(kind: str, raw: str) -> str:
    if kind == "email":
        return raw.strip().lower()
    if kind == "name_tag":
        return raw.strip().lower()
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()


def classify(raw: str) -> str:
    """Best-guess kind for a value supplied through the structured field."""
    value = raw.strip()
    if "@" in value:
        return "email"
    digits = re.sub(r"\D", "", value)
    if len(digits) == 15 and digits == re.sub(r"[^A-Za-z0-9]", "", value):
        return "imei"
    if re.fullmatch(r"(?:\+?251|0)?9\d{8}", re.sub(r"[\s-]", "", value)):
        return "phone"
    if re.match(r"^(?:ugr|ets|stu|id)\b", value, re.I):
        return "student_id"
    if re.fullmatch(r"[A-Za-z][A-Za-z'\- ]{2,30}", value):
        return "name_tag"
    return "serial"


def extract(text, declared=()) -> list:
    """Identifiers from the structured field plus any spotted in free text.

    Extraction runs on the *raw* text, not the normalised form: case and
    punctuation are exactly the signal that distinguishes "C02X1234" from a word.
    """
    out, seen = [], set()

    def add(kind, raw):
        value = _canonical(kind, raw)
        if len(value) < 3:
            return
        key = (kind, value)
        if key in seen:
            return
        seen.add(key)
        out.append(Identifier(kind, value, raw.strip()))

    for raw in declared or ():
        if raw and str(raw).strip():
            add(classify(str(raw)), str(raw))

    body = text or ""
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(body):
            add(kind, match.group(1) if match.groups() else match.group(0))
    return out


def compare(left, right):
    """Returns (verdict, detail) where verdict is 'match' | 'conflict' | 'none'.

    Only kinds present on *both* sides are compared: a found report listing a
    phone number and a lost report listing a serial are simply not talking about
    the same attribute, and that is not a conflict.
    """
    if not left or not right:
        return "none", None
    by_kind_left, by_kind_right = {}, {}
    for ident in left:
        by_kind_left.setdefault(ident.kind, set()).add(ident.value)
    for ident in right:
        by_kind_right.setdefault(ident.kind, set()).add(ident.value)

    shared = set(by_kind_left) & set(by_kind_right)
    for kind in sorted(shared):
        overlap = by_kind_left[kind] & by_kind_right[kind]
        if overlap:
            return "match", {"kind": kind, "value": sorted(overlap)[0]}
    for kind in sorted(shared & HIGH_PRECISION):
        return "conflict", {
            "kind": kind,
            "left": sorted(by_kind_left[kind])[0],
            "right": sorted(by_kind_right[kind])[0],
        }
    return "none", None
