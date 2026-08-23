"""Every tunable number in the system lives here.

The matcher is rule-based rather than learned, so the knobs that decide what
counts as a match need to be readable and changeable by a non-engineer (a
lost-and-found desk supervisor, realistically). Keeping them in one dataclass
also lets the API expose them at GET /api/config, so the UI can show why the
system behaves the way it does.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MatchConfig:
    # Signal weights. Only *available* signals count; the denominator is
    # renormalised so a missing field never silently reads as evidence against.
    weights: dict = field(
        default_factory=lambda: {
            "type": 0.26,
            "text": 0.20,
            "location": 0.16,
            "time": 0.16,
            "color": 0.14,
            "brand": 0.08,
        }
    )

    # Time: a report 3 days later is half as convincing as one the same hour.
    time_half_life_hours: float = 72.0
    # Humans are vague ("yesterday"). Allow found-before-lost by this much.
    chronology_grace_hours: float = 6.0
    day_precision_slack_hours: float = 12.0
    max_gap_days: float = 120.0

    # Location: ~150m of campus walking distance halves the score.
    location_half_life_m: float = 150.0

    color_conflict_penalty: float = 0.70

    fuzzy_threshold: float = 0.86
    fuzzy_credit: float = 0.80
    min_text_tokens: int = 2

    # A matching serial / IMEI is near-proof. A matching name written on the
    # item is strong but not proof: two students can both be called Selam.
    identifier_match_score: float = 0.97
    soft_identifier_match_score: float = 0.85

    strong_threshold: float = 0.72
    possible_threshold: float = 0.45
    min_display_score: float = 0.28

    # Evidence coverage: a 90% score built on one signal is not a 90% score.
    coverage_cap_possible: float = 0.60
    coverage_cap_weak: float = 0.38
    min_signals: int = 2

    # Duplicate detection on submit (a warning, never a hard block).
    duplicate_similarity: float = 0.88
    duplicate_window_hours: float = 48.0

    def as_dict(self) -> dict:
        return {
            "weights": self.weights,
            "time_half_life_hours": self.time_half_life_hours,
            "location_half_life_m": self.location_half_life_m,
            "max_gap_days": self.max_gap_days,
            "bands": {
                "strong": self.strong_threshold,
                "possible": self.possible_threshold,
                "min_display": self.min_display_score,
            },
        }


CONFIG = MatchConfig()
