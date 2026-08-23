"""Demo corpus.

Each entry exists to exercise a specific behaviour, so `python -m lostfound demo`
doubles as a walkthrough of the matcher's decisions. Times are relative to now,
so the dataset never goes stale.
"""

from __future__ import annotations

from datetime import timedelta

from .models import build_report, utcnow

# (kind, hours_ago | None, payload)
SEED = [
    # 1. The brief's own example: should come out STRONG.
    ("lost", 360, {
        "description": "Black backpack containing a laptop charger and a blue notebook.",
        "location": "Library", "contact": "0911111111", "reporter": "hana"}),
    ("found", 356, {
        "description": "Dark-coloured backpack handed in, has a charger inside.",
        "location": "Library Entrance", "contact": "0922222222", "reporter": "desk"}),
    # 2. Same type and colour, wrong side of campus, two weeks later: POSSIBLE,
    #    which is exactly the honest answer.
    ("found", 24, {
        "description": "Black backpack left in the stands, nothing inside.",
        "location": "Football Field", "contact": "0933333333", "reporter": "coach"}),
    # 3. The other example from the brief: "dark" vs "black", cafe vs coffee shop.
    ("lost", 30, {
        "description": "I lost my black AirPods case yesterday near the cafeteria.",
        "location": "Cafeteria", "contact": "0944444444", "reporter": "samuel"}),
    ("found", 26, {
        "description": "Found a dark wireless earbud case beside the coffee shop.",
        "location": "Coffee Shop", "contact": "0955555555", "reporter": "meron"}),
    # 4. Identifier match beats everything: wrong colour, wrong end of campus,
    #    but the IMEI is identical.
    ("lost", 60, {
        "description": "Lost my white iPhone 13 in a red silicone case. IMEI 356938035643809.",
        "location": "Lecture Hall A", "identifiers": ["356938035643809"],
        "contact": "0966666666", "reporter": "bezawit"}),
    ("found", 12, {
        "description": "Handed in a grey phone, screen cracked. IMEI 356938035643809.",
        "location": "Dormitory B", "contact": "0977777777", "reporter": "desk"}),
    # 5. Identifier conflict: near-identical prose, different IMEI. Must be VETOED.
    ("found", 10, {
        "description": "Handed in a white iPhone in a red case. IMEI 351234567890123.",
        "location": "Lecture Hall A", "contact": "0988888888", "reporter": "desk"}),
    # 6. Typos on both sides, no colour agreement problem: fuzzy matching earns it.
    ("lost", 40, {
        "description": "balck watr bottel with a dent, samsung sticker on it",
        "location": "Gymnasium", "contact": "0999999999", "reporter": "dawit"}),
    ("found", 34, {
        "description": "Black water bottle with samsung sticker, dented.",
        "location": "Basketball Court", "contact": "0910101010", "reporter": "desk"}),
    # 7. Chronology: this umbrella was handed in three days before it was lost.
    ("found", 200, {
        "description": "Blue umbrella left in the reading room.",
        "location": "Library", "contact": "0911001100", "reporter": "desk"}),
    ("lost", 20, {
        "description": "Blue umbrella, wooden handle, lost in the library.",
        "location": "Library", "contact": "0912121212", "reporter": "kalkidan"}),
    # 8. Name tag on a calculator: low-precision identifier, still useful.
    ("lost", 50, {
        "description": "Casio scientific calculator, name Selam written on the back.",
        "location": "Science Block", "contact": "0913131313", "reporter": "selam"}),
    ("found", 48, {
        "description": "Calculator found in the chemistry lab, marked Selam.",
        "location": "Science Block", "contact": "0914141414", "reporter": "lab assistant"}),
    # 9. Adjacent colours, same type: grey vs black laptop sleeve.
    ("lost", 8, {
        "description": "Grey laptop sleeve for a 14 inch macbook.",
        "location": "Computer Lab", "contact": "0915151515", "reporter": "yonas"}),
    ("found", 6, {
        "description": "Black laptop sleeve found on a desk, fits a small macbook.",
        "location": "Computer Lab", "contact": "0916161616", "reporter": "desk"}),
    # 10. Deliberately useless report: no colour, no date, no place. Should stay
    #     capped at a low band no matter what it collides with.
    ("lost", None, {"description": "lost my bag", "reporter": "anonymous"}),
    # 11. Wrong type entirely: must never pair with the backpacks above.
    ("found", 5, {
        "description": "Single silver key on a red keychain.",
        "location": "Parking Lot", "contact": "0917171717", "reporter": "guard"}),
]


def load(service, seed=SEED):
    """Insert the demo corpus. Returns the created report ids."""
    now = utcnow()
    created = []
    for kind, hours_ago, payload in seed:
        body = dict(payload)
        if hours_ago is not None:
            body["occurred_at"] = (now - timedelta(hours=hours_ago)).isoformat()
        report, _ = build_report(body, kind=kind)
        service.store.add(report)
        created.append(report.id)
    return created
