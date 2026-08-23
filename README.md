# Lost & Found Matcher: Design and Decisions

> **Requirements:** Python 3.10+ (Zero external dependencies).

## Quickstart — How to Run

### 1. Launch Web UI & JSON API (Recommended)
```bash
python3 -m lostfound serve --demo
```
Then open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your web browser.

### 2. Terminal Demo CLI
To see scored match pairs with human explanations directly in your terminal:
```bash
python3 -m lostfound demo
```

### 3. Run Automated Unit Tests
```bash
python3 -m unittest discover -s tests -v
```

---

## The problem and the approach

A university receives lost-item and found-item reports. Someone has to manually read through them and guess which ones might match. This service automates that guessing and explains its reasoning.

**Why not embeddings?**

I chose a rule-based scorer with a hand-curated lexicon instead of a sentence transformer, deliberately:

1. **The output has to be defensible.** When a student is told "we think this is your bag", they deserve a reason. "Cosine distance 0.81" is not a reason. Every number in this system comes with a sentence explaining where it came from.

2. **The vocabulary is small and closed.** Campuses lose bags, phones, keys, bottles and chargers. A 130-entry lexicon covers the vast majority of it. Non-technical staff can read it, extend it, remove entries that are wrong for their campus, all without touching code or retraining a model.

3. **It runs anywhere, instantly, offline.** No model download, no GPU, no network dependency, no serving overhead. That matters for a campus IT budget and for making the initial deploy a one-liner.

4. **The thresholds and weights are policy, not magic.** They live in `config.py` as a dataclass and are exposed to the UI, so a lost-and-found supervisor can tweak them and see the impact immediately, without a data scientist.

The interfaces are deliberately narrow: every signal function only returns a score and a reason, so swapping the prose signal for embeddings later is a one-file change in `signals.py`. That is written up under *What I would improve with more time*.

## How the matching works

A pair (one lost report, one found report) goes through three gates first:

1. **Type gate**: if both side name clear item types and they don't overlap, the pair is rejected outright.
2. **Chronology gate**: if the found report is too long after the loss, or is before it (past a grace period for vague dates), reject it.
3. **Identifier gate**: if both sides name a high-precision identifier (IMEI, serial, student ID) and they conflict, reject outright.

Only if the pair passes all three gates does it get scored.

Scoring produces six independent signals. Each signal has a name, a label, a score (0.0 to 1.0), a weight, an availability flag, and a human-readable reason. The weights are:

- **Item type** .26 - exact type match
- **Description prose** .20 - shared wording, with typo tolerance
- **Location** .16 - same place or walking distance
- **Timing** .16 - gap between the reports
- **Colour** .14 - colour family agreement
- **Brand** .08 - shared brand

The final score is the weighted mean of the *available* signals only. If only type and location are available, their scores are the two points used; the missing signals (prose, timing, colour, brand) do not count as zeros. This is crucial: an empty lost report and an empty found report should not be matched just because they both lack information.

After the weighted mean is computed, the score is discounted by how much of the usual evidence was available. If coverage (the sum of weights of available signals) is below 0.6, the score is multiplied by `coverage / 0.6`. This prevents "lost my bag" (one word, only type matches) from scoring 100 against "found a backpack".

The score then determines the band:
- **strong**: >= 0.72 - tell both parties, follow up actively.
- **possible**: >= 0.45 - add to the board, a human should look.
- **weak**: >= 0.28 - keep in the list, do not chase.
- **below 0.28**: not shown at all.

Final touches:
- If there was a colour conflict, the score is multiplied by 0.70, since people genuinely misremember colour.
- If there is a matching identifier, the score is boosted to 0.97 (or 0.85 for low-precision identifiers like names), and the band is forced to **strong**, because an IMEI match is near-proof.

## The signals in detail

### Item type (.26)

Every report goes through the lexicon to extract what kind of item it is. The lexicon has 18 canonical types (phone, laptop, backpack, earbuds, etc.) and 133 surface forms people actually write ("airpods", "airpod", "earbud", "earphone", "headphones", etc., all map to `earbuds`).

**Key decision: the head noun.** "Black backpack containing a laptop charger and a blue notebook" mentions three item types, but the thing that went missing is the backpack, not the charger or the notebook. Without this rule, it paired happily with a found "laptop sleeve" because both mentioned laptops.

The implementation: tokenize the entire report, find the first token that maps to an item type, and that is the primary type. Contents are captured as secondary types.

**Ambiguity is preserved.** "Notebook" can be a paper notebook or a laptop (a notebook computer). Both readings are kept. When a pair is compared, the types are intersected: a lost "notebook" (which could be either) matches a found "notebook" if there is overlap in either reading.

When both sides list their types clearly and there is no overlap (e.g., lost "earbuds", found "laptop sleeve"), the pair is rejected by the type gate before scoring even starts.

### Description (.20)

After extracting types, colours, brands and place names (which are handled by other signals), what is left in the prose is the "bag": the distinctive details.

**IDF weighting.** "Library" appears in half the campus reports; "cracked" appears twice. The bag is scored by containment (does the lost report's bag overlap with the found report's bag?) weighted by IDF computed over the live corpus.

**Typo tolerance.** "Balck" for "black" and "bottel" for "bottle" should match. The matcher uses Damerau-Levenshtein (a transposition is one edit, not two) with a length-aware allowance: a word of 8+ characters gets 2 edits, 5-7 gets 1, under 5 gets 0.

**Fuzzy matching is refused below 5 characters.** "Back" is 0.89 similar to "black", and without the length cutoff it matches, which means the colour black gets invented in pairs that never mentioned it. This taught me to be explicit about where fuzzy matching is safe.

**Identifier values are removed from the bag.** The IMEI "356938035643809" and the serial "C02X1234ABCD" are scored separately by the identifier signal; leaving them in the prose would count them twice.

**Place words are removed from the bag.** "Science Block" was being read as the colour black by the fuzzy matcher. Now place names are stripped before any attribute detection.

### Location (.16)

Campus locations (Library, Coffee Shop, Science Block, etc.) are stored in `lostfound/data/campus.json` as Cartesian coordinates in metres, with aliases for common misspellings and variations ("library", "library entrance", "main library", "reading room").

Unrecognised place names are silently treated as "location unavailable", not as a mismatch. A student might say "lost near the cafe" when the system calls it "coffee shop", so graceful degradation is safer than assuming disagreement.

**Distance decay.** If both sides name a place, the score is `0.5 ^ (distance_metres / 150)`. So 150 metres apart is 0.5; 300 metres is 0.25. This is a judgement call based on campus walking distances.

**Same place scores 1.0.**

### Timing (.16)

If both reports have a date, the gap between them is scored as `0.5 ^ (gap_hours / 72)`. So 72 hours (3 days) is 0.5; 144 hours (6 days) is 0.25.

**Slack for vagueness.** If a report gives a date-only (no clock time), we treat it as noon UTC and add 12 hours of slack in both directions before the chronology gate runs. If both reports are date-only, 12 hours per side. Plus an additional 6-hour buffer globally for general human vagueness ("I lost it yesterday afternoon" might mean 6 AM yesterday in the reporter's timezone).

**Impossible times are gated.** If the found report is before the loss and the gap is larger than the slack budget, the pair is rejected in the chronology gate.

### Colour (.14)

Colours are stored in families: black, grey, white, blue, red, green, brown, yellow, orange, purple, pink, multicolour. Each family has surface forms: "grey", "gray", "silver", "graphite", etc.

Shades (dark, light) are handled separately. "Dark" is not a colour, it is a shade.

**Shade matching.** If one side says "dark" and the other says "black", that is a match because dark is compatible with black (and blue, and brown, etc.). If one side says "light" and the other says "blue", that is a match because light is compatible with blue.

**Adjacent colours.** Black and grey, black and blue, blue and purple, red and pink, etc. are treated as confusable. If the two sides name different colours within an adjacent pair, the score is 0.6 instead of 0.

**Colour conflict penalty.** If the colours are clearly different (black vs yellow), the pair is not rejected, but the final score is multiplied by 0.70. People do misremember colour, and the gate should not block a match just because of a colour disagreement.

**Availability.** If neither side mentions a colour, the signal is unavailable. If only one side does, it is still unavailable (can't compare one value to nothing).

### Brand (.08)

Brands are looked up in a frozenset: Apple, Samsung, Lenovo, Casio, Nike, etc. A brand is only credited if both sides name one and they match. Otherwise the signal is unavailable.

Small weight because brands are present in maybe 20% of reports.

### Identifiers

Not a signal in the six, but a gate and an override. Extracted from both the structured field and mined out of free text via regex patterns:

- **IMEI**: 15 digits
- **Phone**: Ethiopian format (0912345678 or +251912345678)
- **Student ID**: UGR/XXXX/XX or similar
- **Serial**: alphanumeric, 5-24 characters, found via regex patterns
- **Name tag**: "marked Selam" or "name written: Ahmed"
- **Email**: standard format

**High-precision identifiers** (IMEI, serial, student ID, email, phone) are compared strictly: if both sides list one of the same kind and they match, the pair scores 0.97 and is forced to strong. If they conflict, the pair is rejected.

**Low-precision identifiers** (name tags) do not veto on conflict, since two students can both be named Selam. A match boosts the score to 0.85 instead of 0.97.

## Assumptions

1. **One campus, one timezone.** Data is stored UTC in the database. The browser sends an explicit offset in the timestamp so "2pm" means 2pm in the student's local time, not UTC. Naive timestamps on the API are interpreted as UTC.

2. **Reports are short free text.** The description is the only required field. Structured fields (place, date, type, brand, identifiers) are optional accelerators. Everything is also mined from the prose as a fallback.

3. **Vagueness is not an error.** No place given, no date given, only a vague description: the signal becomes *unavailable*, not *zero*. This is why the system does not penalise thin reports; they just get fewer signals to score on.

4. **Recall beats precision, up to a point.** The cost of missing a real match (a student never sees their laptop) is much higher than the cost of a bad suggestion (a clerk spends 10 seconds looking at something that is not a match). So the display floor is low (0.28) and the bands are honest about confidence rather than tuned for a single hard cutoff.

5. **The desk is the arbiter.** Nothing is auto-resolved. Confirm and reject are human actions, and they are recorded in `match_decisions`. A rejected pair is never suggested again.

6. **Contact details are sensitive.** "Lost: iPhone 15" next to a phone number is a shopping list for thieves. Contacts are write-only and only revealed when a match is confirmed.

7. **One found item pairs with one lost item.** Confirming a match marks both reports as resolved.

8. **Duplicate submissions are acceptable.** If a student double-clicks the form or files twice, the second one is accepted but flagged as a probable duplicate. The duplicate check uses Jaccard similarity over tokens with a 0.88 threshold and a 48-hour window.

## Edge cases and how they are handled

| Case | Handling |
|------|----------|
| Found before it was lost | Chronology gate rejects it, with slack for vague dates (date-only = +12h per side, plus 6h global). |
| Same IMEI, different colour/place/time | Identifier gate overrides everything; pair is strong. |
| Nearly identical prose, different IMEI | Identifier gate vetoes it. No score can make up for this. |
| Typos: "balck", "bottel", "airpodz" | Damerau-Levenshtein with length-aware allowance. Transposition = 1 edit, not 2. |
| Fuzzy match false positive: "back" vs "black" | Refused below 5 characters; "back" does not match "black". |
| Place name read as colour: "Science Block" | Place words are stripped before attribute detection. |
| Accessory-only overlap: "case" vs "case" | Score multiplied by 0.6 because half the campus owns a black case. |
| Withdrawn or resolved reports | Excluded from matching in queries; will not match against anything. |
| Rejected pair resurfaces | Stored in `match_decisions`; suppressed in future queries. |
| Empty description | 422 with per-field message. |
| 50 KB description | Truncated to 2000 chars with a warning; request body over 64 KB is refused. |
| Duplicate submission / double-clicked form | Accepted, flagged as likely duplicate of earlier report using Jaccard similarity. |
| Unicode, curly quotes, full-width characters | NFKC-normalised before anything else. |
| Future date or date 2+ years old | Rejected as a probable typo. |
| Unknown JSON field | Rejected; typos in field names are never silently ignored. |
| XSS via user text in the UI | All user text rendered with `textContent`, never `innerHTML`. |
| Path traversal on /static/... | Character whitelist (alphanumeric, dot, dash, underscore) plus resolved-path check. |

## Technical decisions

**Standard library only.** Zero dependencies means a reviewer clones the repo and runs `python -m lostfound serve --demo`. No virtualenv, no pip, no network, no waiting. If this grew past a handful of endpoints I would switch to FastAPI for the validation and generated OpenAPI docs, but the router is 40 lines and the swap is cheap.

**Layered and thin at the edges.** `text.py` (normalisation) -> `lexicon.py` (vocabulary) and `places.py` (campus map) -> `matching.py` (the scoring engine) -> `store.py` (SQLite) -> `service.py` (use cases) -> `server.py` and `__main__.py` (HTTP and CLI). The matcher is pure and deterministic (no clock reads, no I/O), which is what makes golden tests meaningful. The CLI drives the whole product without touching the web layer, which proves the layering.

**Every tunable lives in one dataclass** (`config.py`), exposed at `GET /api/meta` so the UI can show the thresholds and explain itself. These are policy, not code, and policy belongs where a supervisor can reach it without a programmer.

**SQLite with real constraints.** One connection behind a threading lock; no connection pool. A lost-and-found desk does not need the complexity of async or multiple writers.

**Candidate blocking with a tiny inverted index.** Comparing every lost report against every found report is O(N*M). At campus scale (thousands of rows) that is fine, but a ten-line posting list (term -> positions) keeps the asymptotic shape right for when it scales, and it halves the comparisons in practice.

**Human decisions are recorded from day one.** Even though nothing learns from them yet, `match_decisions` stores confirmed and rejected pairs with scores and notes. That is the training data any future learned ranker would need.

**Vanilla JavaScript in the UI.** No build step, no CDN, works offline. The whole frontend is one readable file. Every value from the user is written with `textContent`, not `innerHTML`, which is the entire XSS story.

## What I did not build

- **Auth and roles.** Obviously required in production; adds nothing to the question being asked here.
- **Photo upload and image similarity.** Single highest-value feature for a real lost-and-found ("does your phone look like this?"), but far too big for a three-hour exercise. The data model could support it; the signal would be new.
- **Natural-language dates.** "Lost it yesterday" or "found Monday afternoon". The form asks for a date instead, which is cheaper and more reliable than parsing English. The hook is one function (`parse_timestamp`).
- **Email/SMS notifications.** The data model has contacts on file. No integration.
- **Pagination, i18n, an admin UI, Docker, a Makefile, CI/CD.**
- **A learned ranker.** No labelled data yet. The `match_decisions` table is where it comes from.

## If this were becoming a real product

1. **Photos.** A phone camera submission plus perceptual hashing would outperform every text signal here. Start with that.
2. **Learn the weights.** The six weights (.26, .20, .16, .16, .14, .08) are my judgment. With a few thousand confirm/reject decisions, fit them via logistic regression (keeps the per-signal explanations) and calibrate the bands to measured precision/recall instead of thresholds I chose by hand.
3. **Embeddings for prose only.** Swap `text_signal` in `matching.py` for one that uses a sentence transformer on the bag. Keeps the gates, the colour logic, the place logic, the identifier logic. Catches "power brick" for "charger", which the current lexicon misses.
4. **Notify on arrival.** Most matches only become possible when the second report lands. The value is telling someone within the minute.
5. **Custody and audit.** Where the item physically is, who signed for it, when it gets disposed of. That is what makes a desk trust the tool; without it, automation is just a suggestion machine.
6. **Hardening.** Rate limits, structured logs, request ids, Postgres, and abuse controls (someone will absolutely try to claim everything).

## AI Usage & Methodology

I utilized AI assistants as interactive pair-programming tools during development, adhering to standard engineering workflows:

1. **System Design & Architecture (Human Owned):**
   - Designed the multi-signal weighted engine architecture, hard gates (Type, Chronology, Identifier), scoring bands (Strong $\ge 72$, Possible $\ge 45$, Weak $\ge 28$), and zero-dependency constraint.
   - Defined the core mathematical formulations (IDF prose weighting, distance decay, and evidence-coverage discounting).

2. **Scaffolding & Repetitive Data Generation (AI Assisted):**
   - Accelerated initial module boilerplate creation and standard CRUD endpoints.
   - Generated domain vocabulary dictionaries (130+ campus item types, color variations, brand surface forms) and representative test data.

3. **Critical Review & Refactoring (Human Owned):**
   - **Head-Noun Extraction Rule:** Replaced naive keyword matching with primary item type extraction to prevent matching a "backpack containing a laptop charger" with a "laptop sleeve".
   - **Evidence Coverage Discounting:** Implemented a `coverage / 0.6` multiplier to prevent sparse, single-attribute reports ("lost bag") from scoring false 100% matches.
   - **Identifier Stripping:** Stripped IMEIs and serial numbers from prose token bags to avoid double-counting high-precision identifiers across multiple signals.
   - **Place Word Disambiguation:** Filtered campus place names prior to attribute detection so location names (e.g., "Science Block") wouldn't falsely match color tokens.
   - **Transposition Distance (Damerau-Levenshtein):** Replaced basic edit distance with transposition-aware distance to correctly score common typing transpositions (e.g., "bottel" vs "bottle") as a single edit.
   - **Fuzzy Threshold Guardrails:** Enforced strict length cutoffs (minimum 5 characters) for fuzzy matching to eliminate false attribute detection (e.g., preventing "back" from matching "black").

4. **Test-Driven Verification:**
   - Authored unit test cases (`tests/test_matching.py`) covering edge cases, gates, and domain rules to independently verify system behavior and prevent regressions.
