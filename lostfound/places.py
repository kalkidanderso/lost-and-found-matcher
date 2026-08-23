"""Resolving free text to a place on campus, and measuring distance between two.

Design note: a real deployment would use a proper gazetteer plus coordinates
from the estates team. A flat metre grid in a JSON file is the right amount of
model for one campus, is editable by a non-programmer, and makes "how far apart
is this?" a one-line answer instead of a graph search.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .text import best_fuzzy, normalize

DEFAULT_CAMPUS = Path(__file__).with_name("data") / "campus.json"


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    x: float
    y: float
    aliases: tuple


class Campus:
    def __init__(self, places):
        self.places = {p.id: p for p in places}
        # Longest aliases first so "library entrance" wins over "library".
        self._aliases = sorted(
            ((normalize(alias), p.id) for p in places for alias in (p.name,) + p.aliases),
            key=lambda pair: -len(pair[0]),
        )
        self._single = {a: pid for a, pid in self._aliases if " " not in a}

    @classmethod
    def load(cls, path=None):
        raw = json.loads(Path(path or DEFAULT_CAMPUS).read_text(encoding="utf-8"))
        return cls([
            Place(p["id"], p["name"], float(p["x"]), float(p["y"]), tuple(p.get("aliases", ())))
            for p in raw["places"]
        ])

    def get(self, place_id):
        return self.places.get(place_id)

    def resolve(self, *texts):
        """First place mentioned across the given texts, plus the alias that hit.

        Returns (Place|None, matched_text|None). Unresolvable input is *not* an
        error: the location signal simply becomes unavailable for that report.
        """
        for text in texts:
            if not text:
                continue
            norm = normalize(text)
            if not norm:
                continue
            for alias, pid in self._aliases:
                if " " in alias and alias in norm:
                    return self.places[pid], alias
            tokens = norm.split()
            for tok in tokens:
                if tok in self._single:
                    return self.places[self._single[tok]], tok
            for tok in tokens:
                if len(tok) < 5:
                    continue
                match, _ = best_fuzzy(tok, self._single.keys())
                if match:
                    return self.places[self._single[match]], match
        return None, None

    def distance(self, a: Place, b: Place) -> float:
        return math.hypot(a.x - b.x, a.y - b.y)

    def as_options(self):
        return [{"id": p.id, "name": p.name} for p in self.places.values()]


CAMPUS = Campus.load()
