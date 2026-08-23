"""Text normalisation. Boring on purpose, and the highest-value code here.

Most "clever" matching failures in this domain are really normalisation
failures: "Air Pods" vs "airpods", "grey" vs "gray", "BLACK!!!" vs "black",
"keys" vs "key", a pasted non-breaking space, a curly apostrophe. Fix that
first and a simple scorer starts looking smart.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Multi-word forms people actually type, collapsed into one token so they can be
# looked up in the lexicon as a single concept.
PHRASES: dict = {
    "air pods": "airpods",
    "ear buds": "earbuds",
    "ear phones": "earphones",
    "head phones": "headphones",
    "head set": "headset",
    "back pack": "backpack",
    "lap top": "laptop",
    "note book": "notebook",
    "text book": "textbook",
    "water bottle": "waterbottle",
    "flash drive": "flashdrive",
    "flash disk": "flashdrive",
    "thumb drive": "flashdrive",
    "usb stick": "flashdrive",
    "memory stick": "flashdrive",
    "power bank": "powerbank",
    "id card": "idcard",
    "student id": "studentid",
    "student card": "studentid",
    "key chain": "keychain",
    "key ring": "keychain",
    "sun glasses": "sunglasses",
    "eye glasses": "glasses",
    "wrist watch": "wristwatch",
    "smart watch": "smartwatch",
    "coffee shop": "coffeeshop",
    "lecture hall": "lecturehall",
    "football field": "footballfield",
    "sports field": "footballfield",
    "parking lot": "parkinglot",
    "bus stop": "busstop",
    "main gate": "maingate",
    "student center": "studentcenter",
    "student centre": "studentcenter",
    "off white": "offwhite",
    "light blue": "lightblue",
    "dark blue": "navy",
    "dark colored": "dark",
    "dark coloured": "dark",
    "light colored": "light",
    "light coloured": "light",
    "rose gold": "rosegold",
    "tote bag": "totebag",
}

# Words with no discriminating power in this corpus. "lost"/"found" are in here
# on purpose: every single report contains one of them.
STOPWORDS = frozenset(
    """
    a an the my mine your yours his her hers its our ours their theirs this that these those
    i me we us you he she it they them someone somebody anyone anybody
    is am are was were be been being do does did doing done have has had having
    of in on at by to for from with without near beside next inside outside within
    into onto around about over under above below between behind front rear
    back side top bottom middle end corner edge outer inner block
    and or but if then than so as also very really quite just only even still too
    there here where when while during after before yesterday today tonight tomorrow
    lost found finding losing lose loses lose left leave leaving misplaced missing
    dropped drop forgot forgotten seen see saw somewhere anywhere please help
    thanks thank pls plz kindly urgent reward asap contact call whatsapp number
    item items thing things stuff belonging belongings property report reported
    got get take taken took picked pick put down out off away again
    approximately approx roughly maybe probably possibly think believe guess sure
    look looks looking like similar containing contains contained
    little bit lot much many some any all no not none nothing anything something
    am pm oclock morning afternoon evening night noon midnight
    day week month year hour minute ago last next around
    """.split()
)

_CONTROL = dict.fromkeys(range(0x00, 0x20))
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")
_DIGITS = re.compile(r"^\d+$")


def normalize(text) -> str:
    """NFKC-fold, drop punctuation/control chars, lowercase, collapse phrases."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = t.translate(_CONTROL)
    for ch in ("\u2019", "\u2018", "\u02bc"):
        t = t.replace(ch, "'")
    t = t.casefold()
    t = t.replace("-", " ").replace("_", " ").replace("/", " ")
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    for phrase, joined in PHRASES.items():
        if phrase in t:
            t = t.replace(phrase, joined)
    return t


def singularize(token: str) -> str:
    """Deliberately naive stemmer. Enough for nouns, and zero dependencies."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text, drop_stopwords: bool = True) -> list:
    out = []
    for raw in normalize(text).split():
        if len(raw) < 2:
            continue
        if drop_stopwords and raw in STOPWORDS:
            continue
        stem = singularize(raw)
        if drop_stopwords and stem in STOPWORDS:
            continue
        out.append(stem)
    return out


def token_set(text, drop_stopwords: bool = True) -> set:
    return set(tokenize(text, drop_stopwords))


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Damerau-Levenshtein distance, bounded for speed.

    Transpositions matter here: "bottel" for "bottle" is the single most common
    typing error and plain Levenshtein charges it two edits, which is enough to
    push a real match under any sane ratio threshold.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(prev[j] + 1, current[j - 1] + 1, prev[j - 1] + cost)
            if (i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb):
                current[j] = min(current[j], prev2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        prev2, prev = prev, current
    return prev[len(b)]


def _typo_allowance(token: str) -> int:
    """How many edits count as "the same word" for a token of this length."""
    if len(token) >= 8:
        return 2
    if len(token) >= 5:
        return 1
    return 0


def best_fuzzy(token: str, candidates, threshold: float = 0.86):
    """Closest candidate above the threshold, or (None, 0.0).

    Two acceptance rules, not one: a similarity ratio for general wobble, plus a
    length-aware edit-distance allowance so short-but-clearly-mistyped words are
    not rejected by a ratio that is dominated by their length.

    Numeric tokens are never fuzzy-matched: "12345" and "12346" are 0.8 similar
    and almost certainly different objects.
    """
    # Below five characters, fuzzy matching does more harm than good: "back" is
    # 0.89 similar to "black", and that one false positive taught me to draw the
    # line by length rather than by threshold alone.
    if len(token) < 5 or _DIGITS.match(token):
        return None, 0.0
    allowance = _typo_allowance(token)
    best, best_score = None, 0.0
    for cand in candidates:
        if _DIGITS.match(cand) or abs(len(cand) - len(token)) > 3:
            continue
        score = ratio(token, cand)
        if score < threshold and allowance and edit_distance(token, cand, allowance) <= allowance:
            score = max(score, threshold)
        if score > best_score:
            best, best_score = cand, score
    return (best, best_score) if best_score >= threshold else (None, 0.0)


def similarity(a, b) -> float:
    """Symmetric Jaccard over tokens. Used only for duplicate detection."""
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
