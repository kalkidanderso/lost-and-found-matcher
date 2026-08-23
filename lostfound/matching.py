"""The matching engine.

Shape of the idea: a match is not a yes/no, it is an *argument*. So every pair
produces a set of independent signals, each of which can be unavailable, and
each of which carries its own human-readable reason. The score is a weighted
average over the signals that were actually available, and the band (strong /
possible / weak) additionally depends on how much evidence that average rests
on. On top of that sit a few hard gates, which are the cases where extra prose
similarity should never be able to rescue a pair.

Everything here is pure and deterministic: no clock reads, no I/O, no network.
That is what makes the golden tests in tests/test_matching.py meaningful.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import identifiers as ident
from .config import CONFIG, MatchConfig
from .lexicon import (
    ACCESSORY_TYPES,
    TYPE_INDEX,
    ADJACENT_COLORS,
    BRANDS,
    COLOR_TOKENS,
    SHADE_COMPATIBLE,
    TYPE_TOKENS,
    detect_brands,
    detect_colors,
    detect_types,
)
from .places import CAMPUS
from .text import best_fuzzy, normalize, tokenize


@dataclass
class Signal:
    name: str
    label: str
    score: float
    weight: float
    available: bool
    reason: str

    def as_dict(self):
        return {
            "name": self.name,
            "label": self.label,
            "score": None if not self.available else round(self.score, 3),
            "weight": self.weight,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass
class Match:
    lost_id: str
    found_id: str
    score: float
    band: str
    signals: list
    notes: list = field(default_factory=list)
    coverage: float = 0.0

    def as_dict(self):
        return {
            "lost_id": self.lost_id,
            "found_id": self.found_id,
            "score": round(self.score * 100),
            "band": self.band,
            "coverage": round(self.coverage, 2),
            "signals": [s.as_dict() for s in self.signals],
            "notes": list(self.notes),
        }


@dataclass
class Rejection:
    lost_id: str
    found_id: str
    reason: str

    def as_dict(self):
        return {"lost_id": self.lost_id, "found_id": self.found_id, "reason": self.reason}


@dataclass
class Features:
    """Everything derived from one report, computed once and reused for every
    candidate pair. Feature extraction is the expensive part; comparison is not.
    """

    report: object
    tokens: list
    bag: set
    types: set
    core_types: set
    primary_types: set
    colors: set
    shades: set
    brands: set
    place: object
    place_alias: str
    ids: list


def primary_types_for(tokens, item_type_field=""):
    """The item itself, as opposed to the things it contains.

    "Black backpack containing a laptop charger" mentions three item types. Only
    the first one is the thing that went missing; the rest are contents. Taking
    the head noun is a cheap heuristic that beats set-intersection badly enough
    to matter: without it, that report pairs happily with a found laptop sleeve.
    """
    declared = detect_types(tokenize(item_type_field)) if item_type_field else set()
    if declared:
        return declared, {t for t in tokens if (TYPE_INDEX.get(t) or set()) & declared}
    for token in tokens:
        hit = TYPE_INDEX.get(token) or _fuzzy_types(token)
        if hit:
            return set(hit), {t for t in tokens if (TYPE_INDEX.get(t) or set()) & set(hit)} | {token}
    return set(), set()


def _fuzzy_types(token):
    from .lexicon import _lookup
    return _lookup(token, TYPE_INDEX) or set()


def features_for(report, campus=CAMPUS) -> Features:
    tokens = tokenize(report.searchable())
    place, alias = campus.resolve(report.location, report.description, report.title)

    # Place words are removed *before* attribute detection, not just before the
    # prose comparison. "Science Block" was being read as the colour black by the
    # typo tolerance, and it agreed with itself on both sides of the pair.
    place_tokens = set(tokenize(alias, drop_stopwords=False)) if alias else set()
    attr_tokens = [t for t in tokens if t not in place_tokens]

    types = detect_types(attr_tokens)
    primary, primary_tokens = primary_types_for(attr_tokens, report.item_type)
    colors, shades = detect_colors(attr_tokens)
    brands = detect_brands(attr_tokens)

    # The prose bag excludes whatever another signal already scores: the primary
    # item type, colours, brands, and the place name. Contents ("a charger",
    # "a notebook") stay in, because they are real evidence and nothing else
    # looks at them.
    ids = ident.extract(report.searchable(), report.identifiers)
    consumed = set(primary_tokens) | set(COLOR_TOKENS) | set(BRANDS) | IDENTIFIER_WORDS
    if alias:
        consumed |= set(tokenize(alias, drop_stopwords=False))
    # Identifier values are scored by the identifier signal; leaving them in the
    # prose bag as well would count the same IMEI twice.
    for identifier in ids:
        consumed |= set(tokenize(identifier.raw, drop_stopwords=False))
        consumed.add(identifier.value.lower())
    bag = {t for t in tokens if t not in consumed}

    return Features(
        report=report,
        tokens=tokens,
        bag=bag,
        types=types,
        core_types=types - ACCESSORY_TYPES,
        primary_types=primary,
        colors=colors,
        shades=shades,
        brands=brands,
        place=place,
        place_alias=alias or "",
        ids=ids,
    )


def build_idf(feature_list) -> dict:
    """Inverse document frequency over report prose.

    "library" appears in half the reports on a campus and is nearly worthless;
    "cracked" appears twice and is gold. Computed over the live corpus rather
    than hardcoded, so it adapts to whatever this particular university loses.
    """
    total = max(len(feature_list), 1)
    doc_freq = {}
    for feats in feature_list:
        for token in feats.bag:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {tok: math.log((1 + total) / (1 + df)) + 1.0 for tok, df in doc_freq.items()}


def _human_gap(hours: float) -> str:
    hours = abs(hours)
    if hours < 1:
        return f"{int(round(hours * 60))} min"
    if hours < 36:
        return f"{hours:.0f} hour{'s' if round(hours) != 1 else ''}"
    return f"{hours / 24:.0f} days"


def _decay(value: float, half_life: float) -> float:
    return 0.5 ** (max(value, 0.0) / half_life)


def type_signal(a: Features, b: Features, cfg: MatchConfig) -> Signal:
    weight = cfg.weights["type"]
    left, right = a.primary_types, b.primary_types
    if not left or not right:
        side = "neither report" if not (left or right) else "one report"
        return Signal("type", "Item type", 0.0, weight, False,
                      f"Could not tell what kind of item {side} describes.")

    overlap = left & right
    if not overlap:
        return Signal("type", "Item type", 0.0, weight, True,
                      f"Different kinds of item: {'/'.join(sorted(left))} vs {'/'.join(sorted(right))}.")

    score = len(overlap) / min(len(left), len(right))
    # An overlap made only of accessories ("a case", "a charger") is weak
    # evidence: half the campus owns a black case.
    if overlap <= ACCESSORY_TYPES:
        score *= 0.6
        reason = f"Both mention a {'/'.join(sorted(overlap))}, but no clearer item type."
    else:
        reason = f"Both look like the same kind of item: {'/'.join(sorted(overlap))}."
    return Signal("type", "Item type", min(score, 1.0), weight, True, reason)


def text_signal(a: Features, b: Features, idf: dict, cfg: MatchConfig) -> Signal:
    weight = cfg.weights["text"]
    if len(a.bag) < cfg.min_text_tokens or len(b.bag) < cfg.min_text_tokens:
        return Signal("text", "Description details", 0.0, weight, False,
                      "Not enough distinctive wording to compare.")

    shorter, longer = (a.bag, b.bag) if len(a.bag) <= len(b.bag) else (b.bag, a.bag)
    total = sum(idf.get(tok, 1.0) for tok in shorter) or 1.0
    matched, hits, fuzzy_hits = 0.0, [], []
    for token in shorter:
        weight_tok = idf.get(token, 1.0)
        if token in longer:
            matched += weight_tok
            hits.append(token)
            continue
        near, _ = best_fuzzy(token, longer, cfg.fuzzy_threshold)
        if near:
            matched += cfg.fuzzy_credit * weight_tok
            fuzzy_hits.append(f"{token}~{near}")

    score = min(matched / total, 1.0)
    # Containment rather than Jaccard: "Found a dark case" should not be punished
    # for being shorter than a paragraph-long loss report.
    if hits or fuzzy_hits:
        shared = ", ".join(sorted(hits) + fuzzy_hits)
        reason = f"Shared wording: {shared}."
    else:
        reason = "No wording in common beyond the item type and colour."
    return Signal("text", "Description details", score, weight, True, reason)


def color_signal(a: Features, b: Features, cfg: MatchConfig):
    """Returns (Signal, conflict) - conflict also applies a global penalty."""
    weight = cfg.weights["color"]
    label = "Colour"
    if (a.colors or a.shades) and (b.colors or b.shades):
        if a.colors and b.colors:
            shared = a.colors & b.colors
            if shared:
                return Signal("color", label, 1.0, weight, True,
                              f"Same colour: {'/'.join(sorted(shared))}."), False
            for left in a.colors:
                for right in b.colors:
                    if frozenset({left, right}) in ADJACENT_COLORS:
                        return Signal("color", label, 0.6, weight, True,
                                      f"Easily confused colours: {left} vs {right}."), False
            return Signal("color", label, 0.0, weight, True,
                          f"Colours disagree: {'/'.join(sorted(a.colors))} vs "
                          f"{'/'.join(sorted(b.colors))}."), True

        # One side only said "dark"/"light". That is a shade, not a colour, and
        # "dark" against "black" is agreement, not a mismatch.
        colors = a.colors or b.colors
        shades = b.shades if a.colors else a.shades
        if colors:
            compatible = any(c in SHADE_COMPATIBLE.get(s, set()) for s in shades for c in colors)
            shade, color = "/".join(sorted(shades)), "/".join(sorted(colors))
            if compatible:
                return Signal("color", label, 0.75, weight, True,
                              f"'{shade}' is consistent with {color}."), False
            return Signal("color", label, 0.15, weight, True,
                          f"'{shade}' does not sit well with {color}."), False
        if a.shades & b.shades:
            return Signal("color", label, 0.7, weight, True,
                          f"Both described as {'/'.join(sorted(a.shades & b.shades))}."), False
        return Signal("color", label, 0.1, weight, True,
                      f"Opposite shades: {'/'.join(sorted(a.shades))} vs "
                      f"{'/'.join(sorted(b.shades))}."), False

    return Signal("color", label, 0.0, weight, False, "No colour given in at least one report."), False


def location_signal(a: Features, b: Features, cfg: MatchConfig, campus=CAMPUS) -> Signal:
    weight = cfg.weights["location"]
    if not a.place or not b.place:
        missing = "neither report names" if not (a.place or b.place) else "one report does not name"
        return Signal("location", "Location", 0.0, weight, False,
                      f"Ignored because {missing} a place we recognise.")
    if a.place.id == b.place.id:
        return Signal("location", "Location", 1.0, weight, True, f"Same place: {a.place.name}.")
    metres = campus.distance(a.place, b.place)
    score = _decay(metres, cfg.location_half_life_m)
    return Signal("location", "Location", score, weight, True,
                  f"{a.place.name} and {b.place.name} are about {metres:.0f} m apart.")


def time_signal(lost: Features, found: Features, cfg: MatchConfig) -> Signal:
    weight = cfg.weights["time"]
    lost_at, found_at = lost.report.occurred_at, found.report.occurred_at
    if not lost_at or not found_at:
        missing = "neither report has" if not (lost_at or found_at) else "one report has no"
        return Signal("time", "Timing", 0.0, weight, False, f"Ignored because {missing} a date.")
    gap_hours = (found_at - lost_at).total_seconds() / 3600.0
    score = _decay(gap_hours, cfg.time_half_life_hours)
    if gap_hours < 0:
        reason = f"Reported found {_human_gap(gap_hours)} before the loss - within reporting slack."
    else:
        reason = f"Found {_human_gap(gap_hours)} after the loss."
    vague = [f.report.time_precision for f in (lost, found) if f.report.time_precision == "day"]
    if vague:
        reason += " (date only, no time of day)"
    return Signal("time", "Timing", score, weight, True, reason)


def brand_signal(a: Features, b: Features, cfg: MatchConfig) -> Signal:
    weight = cfg.weights["brand"]
    if not a.brands or not b.brands:
        return Signal("brand", "Brand", 0.0, weight, False, "No brand named in at least one report.")
    shared = a.brands & b.brands
    if shared:
        return Signal("brand", "Brand", 1.0, weight, True, f"Same brand: {'/'.join(sorted(shared))}.")
    return Signal("brand", "Brand", 0.0, weight, True,
                  f"Different brands: {'/'.join(sorted(a.brands))} vs {'/'.join(sorted(b.brands))}.")


IDENTIFIER_WORDS = frozenset({
    "imei", "serial", "sn", "number", "tag", "marked", "labelled", "labeled",
    "engraved", "written", "name", "named", "inscribed", "sticker",
})

BAND_RANK = {"weak": 0, "possible": 1, "strong": 2}
RANK_BAND = {v: k for k, v in BAND_RANK.items()}


def _cap(band: str, ceiling: str) -> str:
    return RANK_BAND[min(BAND_RANK[band], BAND_RANK[ceiling])]


def chronology_gate(lost: Features, found: Features, cfg: MatchConfig):
    """An item cannot be handed in before it goes missing - but reports are vague,
    so the impossibility test gets explicit slack rather than a strict '<'.
    """
    lost_at, found_at = lost.report.occurred_at, found.report.occurred_at
    if not lost_at or not found_at:
        return None
    slack = cfg.chronology_grace_hours
    for feats in (lost, found):
        if feats.report.time_precision == "day":
            slack += cfg.day_precision_slack_hours
    gap_hours = (found_at - lost_at).total_seconds() / 3600.0
    if gap_hours < -slack:
        return f"Handed in {_human_gap(gap_hours)} before the item was lost."
    if gap_hours > cfg.max_gap_days * 24:
        return f"Found {_human_gap(gap_hours)} after the loss, beyond the {cfg.max_gap_days:.0f} day cutoff."
    return None


def type_gate(lost: Features, found: Features):
    """No amount of shared prose makes a backpack a pair of earbuds."""
    left, right = lost.primary_types, found.primary_types
    if left and right and not (left & right):
        return (f"Different kinds of item: {'/'.join(sorted(left))} vs "
                f"{'/'.join(sorted(right))}.")
    return None


class Matcher:
    def __init__(self, config: MatchConfig = CONFIG, campus=CAMPUS):
        self.cfg = config
        self.campus = campus

    # -- feature/corpus preparation ---------------------------------------
    def prepare(self, reports):
        feats = [features_for(r, self.campus) for r in reports]
        return feats, build_idf(feats)

    # -- single pair -------------------------------------------------------
    def score_pair(self, lost: Features, found: Features, idf: dict):
        """Returns a Match or a Rejection. Never raises on odd input."""
        cfg = self.cfg
        lost_id, found_id = lost.report.id, found.report.id
        if lost.report.kind != "lost" or found.report.kind != "found":
            return Rejection(lost_id, found_id, "Reports must be one lost and one found.")

        verdict, detail = ident.compare(lost.ids, found.ids)
        if verdict == "conflict":
            return Rejection(lost_id, found_id,
                             f"Conflicting {detail['kind'].replace('_', ' ')}: "
                             f"{detail['left']} vs {detail['right']}.")

        blocked = type_gate(lost, found) or chronology_gate(lost, found, cfg)
        if blocked and verdict != "match":
            return Rejection(lost_id, found_id, blocked)

        color, color_conflict = color_signal(lost, found, cfg)
        signals = [
            type_signal(lost, found, cfg),
            text_signal(lost, found, idf, cfg),
            location_signal(lost, found, cfg, self.campus),
            time_signal(lost, found, cfg),
            color,
            brand_signal(lost, found, cfg),
        ]

        available = [s for s in signals if s.available]
        coverage = sum(s.weight for s in available)
        if not available:
            return Rejection(lost_id, found_id, "Both reports are too thin to compare at all.")

        score = sum(s.score * s.weight for s in available) / coverage
        notes = []

        if color_conflict:
            score *= cfg.color_conflict_penalty
            notes.append("Colour disagreement applied as a penalty; people do misremember colour, "
                         "so this lowers the score rather than ruling the pair out.")

        # A weighted average over one signal is still an average over one signal.
        # Discounting by evidence coverage stops "lost my bag" vs "found a bag"
        # from reporting 100%% just because the single comparable field agreed.
        confidence = min(1.0, coverage / cfg.coverage_cap_possible)
        if confidence < 1.0:
            score *= confidence
            notes.append(f"Only {coverage:.0%} of the usual evidence was available, "
                         "so the score is discounted accordingly.")

        band = ("strong" if score >= cfg.strong_threshold
                else "possible" if score >= cfg.possible_threshold else "weak")

        if coverage < cfg.coverage_cap_weak or len(available) < cfg.min_signals:
            band = _cap(band, "weak")
            notes.append("Thin evidence: too few comparable fields to rate this higher.")
        elif coverage < cfg.coverage_cap_possible:
            band = _cap(band, "possible")
            notes.append("Capped at 'possible': the score rests on only a few fields.")

        if verdict == "match":
            precise = detail["kind"] in ident.HIGH_PRECISION
            score = max(score, cfg.identifier_match_score if precise
                        else cfg.soft_identifier_match_score)
            band = "strong"
            kind_label = detail["kind"].replace("_", " ")
            notes.insert(0, f"Matching {kind_label} ({detail['value']}) - treated as "
                            + ("near-proof, overriding the other signals." if precise else
                               "strong evidence, though names are not unique."))
            if blocked:
                notes.append(f"Note: {blocked} Kept anyway because the identifiers match.")

        if score < cfg.min_display_score:
            return Rejection(lost_id, found_id,
                             f"Score {score * 100:.0f} is below the {cfg.min_display_score * 100:.0f} "
                             "point threshold for showing a human.")

        return Match(lost_id, found_id, score, band, signals, notes, coverage)

    # -- candidate generation ---------------------------------------------
    @staticmethod
    def _postings(feature_list):
        """Tiny inverted index: term -> positions in feature_list.

        Comparing every lost report against every found report is O(N*M). At one
        university that is genuinely fine (thousands of rows, milliseconds), but
        blocking on shared item type / distinctive word / identifier costs ten
        lines and keeps the shape right for when it is not fine.
        """
        index = {}
        for pos, feats in enumerate(feature_list):
            terms = set(feats.types) | feats.bag | {i.value for i in feats.ids}
            for term in terms:
                index.setdefault(term, set()).add(pos)
        return index

    def _candidates(self, feats, pool, index):
        terms = set(feats.types) | feats.bag | {i.value for i in feats.ids}
        positions = set()
        for term in terms:
            positions |= index.get(term, set())
        if not positions:
            # Nothing in common with anything: fall back to a full scan rather
            # than silently returning "no matches" for a sparse report.
            return pool
        return [pool[p] for p in sorted(positions)]

    # -- bulk entry points -------------------------------------------------
    def matches_for(self, report, others, suppressed=(), include_rejections=False):
        """Ranked matches for one report against the given pool."""
        pool = [r for r in others if r.id != report.id and r.status == "open"
                and r.kind != report.kind]
        feats, idf = self.prepare([report] + pool)
        subject, candidates = feats[0], feats[1:]
        index = self._postings(candidates)

        matches, rejections = [], []
        for other in self._candidates(subject, candidates, index):
            lost, found = (subject, other) if report.kind == "lost" else (other, subject)
            if (lost.report.id, found.report.id) in set(suppressed):
                continue
            outcome = self.score_pair(lost, found, idf)
            (matches if isinstance(outcome, Match) else rejections).append(outcome)

        matches.sort(key=lambda m: -m.score)
        return (matches, rejections) if include_rejections else matches

    def all_matches(self, reports, suppressed=()):
        """Every displayable pair across the whole corpus, best first."""
        open_reports = [r for r in reports if r.status == "open"]
        feats, idf = self.prepare(open_reports)
        lost = [f for f in feats if f.report.kind == "lost"]
        found = [f for f in feats if f.report.kind == "found"]
        if not lost or not found:
            return []
        index = self._postings(found)
        suppressed = set(suppressed)

        matches = []
        for subject in lost:
            for candidate in self._candidates(subject, found, index):
                if (subject.report.id, candidate.report.id) in suppressed:
                    continue
                outcome = self.score_pair(subject, candidate, idf)
                if isinstance(outcome, Match):
                    matches.append(outcome)
        matches.sort(key=lambda m: -m.score)
        return matches

    def explain(self, lost_report, found_report, corpus=()):
        """Score one specific pair, including the reason it was ruled out."""
        pool = list(corpus) or [lost_report, found_report]
        feats, idf = self.prepare(pool if lost_report in pool else
                                  [lost_report, found_report] + list(corpus))
        by_id = {f.report.id: f for f in feats}
        lost = by_id.get(lost_report.id) or features_for(lost_report, self.campus)
        found = by_id.get(found_report.id) or features_for(found_report, self.campus)
        return self.score_pair(lost, found, idf)


MATCHER = Matcher()
