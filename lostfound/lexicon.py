"""Hand-curated domain vocabulary: item types, colours, brands.

Why a lexicon and not embeddings? See the README. Short version: on a campus
the vocabulary of lost items is small, closed and stable, a lexicon is
inspectable and free to run, and the desk staff can extend it without a data
scientist. The interfaces here are narrow, so swapping in embeddings later is a
localised change (`signals.py` only asks for a score and a reason).
"""

from __future__ import annotations

from .text import best_fuzzy, singularize

# canonical item type -> surface forms users actually write
ITEM_TYPES: dict = {
    "earbuds": ["airpods", "airpod", "earbuds", "earbud", "earphones", "earphone",
                 "headphones", "headphone", "headset", "buds", "pods"],
    "phone": ["phone", "iphone", "smartphone", "mobile", "cellphone", "handset", "telephone"],
    "laptop": ["laptop", "macbook", "chromebook", "ultrabook", "notebook", "pc"],
    "tablet": ["tablet", "ipad", "kindle", "ereader"],
    "charger": ["charger", "adapter", "adaptor", "cable", "cord", "powerbank", "plug"],
    "backpack": ["backpack", "bag", "rucksack", "knapsack", "satchel", "totebag", "handbag"],
    "wallet": ["wallet", "purse", "billfold", "cardholder"],
    "keys": ["key", "keys", "keychain"],
    "idcard": ["idcard", "studentid", "badge", "id", "licence", "license", "passport"],
    "bottle": ["bottle", "waterbottle", "flask", "thermos", "tumbler", "canteen"],
    "umbrella": ["umbrella", "parasol"],
    "glasses": ["glasses", "glass", "spectacles", "sunglasses", "eyewear"],
    "watch": ["watch", "wristwatch", "smartwatch", "fitbit"],
    "book": ["book", "textbook", "notebook", "notes", "binder", "folder", "journal", "planner"],
    "case": ["case", "cover", "pouch", "sleeve", "box"],
    "calculator": ["calculator", "casio"],
    "flashdrive": ["flashdrive", "usb", "harddrive", "ssd"],
    "clothing": ["jacket", "hoodie", "coat", "sweater", "jumper", "scarf", "cap", "hat",
                  "gloves", "glove", "shirt", "tshirt", "shoe", "shoes", "sandal", "sneaker"],
    "jewellery": ["ring", "bracelet", "necklace", "earring", "chain", "pendant", "anklet"],
    "camera": ["camera", "gopro", "dslr", "lens"],
    "stationery": ["pen", "pencil", "marker", "eraser", "ruler", "stapler", "geometry"],
    "documents": ["document", "paper", "transcript", "certificate", "receipt", "form"],
    "sports": ["racket", "ball", "football", "basketball", "skateboard", "helmet"],
    "money": ["cash", "money", "birr", "note", "coin"],
}

# Types that only ever qualify another object. A "case" matching a "case" is not
# evidence if one is a phone case and the other holds earbuds.
ACCESSORY_TYPES = frozenset({"case", "charger", "documents", "money"})

# form -> {types}. A form may be ambiguous: "notebook" is both a laptop and a
# paper notebook, so it maps to both and the matcher resolves by intersection
# instead of committing to a guess.
TYPE_INDEX: dict = {}
for _canon, _forms in ITEM_TYPES.items():
    for _form in _forms:
        TYPE_INDEX.setdefault(singularize(_form), set()).add(_canon)

TYPE_TOKENS = frozenset(TYPE_INDEX)

# canonical colour family -> surface forms
COLOR_FAMILIES: dict = {
    "black": ["black", "jet", "ebony", "onyx", "charcoal"],
    "grey": ["grey", "gray", "silver", "graphite", "gunmetal", "slate", "ash"],
    "white": ["white", "ivory", "cream", "offwhite", "pearl"],
    "blue": ["blue", "navy", "teal", "cyan", "turquoise", "lightblue", "indigo"],
    "red": ["red", "maroon", "crimson", "burgundy", "scarlet"],
    "green": ["green", "olive", "lime", "emerald", "mint"],
    "brown": ["brown", "tan", "beige", "khaki", "bronze", "coffee", "chocolate", "camel"],
    "yellow": ["yellow", "gold", "golden", "mustard", "rosegold"],
    "orange": ["orange", "peach", "amber"],
    "purple": ["purple", "violet", "lilac", "lavender", "mauve"],
    "pink": ["pink", "rose", "fuchsia", "magenta"],
    "multicolour": ["multicolour", "multicolor", "colourful", "colorful", "patterned",
                     "striped", "checked", "floral", "rainbow", "transparent", "clear"],
}

COLOR_INDEX: dict = {}
for _canon, _forms in COLOR_FAMILIES.items():
    for _form in _forms:
        COLOR_INDEX[singularize(_form)] = _canon

# "dark" is not a colour, it is a shade. Treating it as one is how you end up
# scoring "dark" against "black" as a total mismatch.
SHADES: dict = {
    "dark": ["dark", "darkish", "blackish", "dull"],
    "light": ["light", "pale", "pastel", "bright", "shiny"],
}
SHADE_INDEX: dict = {}
for _canon, _forms in SHADES.items():
    for _form in _forms:
        SHADE_INDEX[singularize(_form)] = _canon

# Which colour families a bare shade word is compatible with.
SHADE_COMPATIBLE: dict = {
    "dark": {"black", "grey", "blue", "brown", "purple", "green", "red"},
    "light": {"white", "grey", "yellow", "pink", "brown", "orange", "blue", "green"},
}

# Colour pairs people genuinely confuse or describe interchangeably.
ADJACENT_COLORS = frozenset({
    frozenset({"black", "grey"}),
    frozenset({"black", "blue"}),
    frozenset({"black", "brown"}),
    frozenset({"grey", "white"}),
    frozenset({"grey", "blue"}),
    frozenset({"brown", "yellow"}),
    frozenset({"brown", "orange"}),
    frozenset({"red", "pink"}),
    frozenset({"red", "orange"}),
    frozenset({"purple", "pink"}),
    frozenset({"purple", "blue"}),
    frozenset({"white", "yellow"}),
})

BRANDS = frozenset({
    "apple", "samsung", "huawei", "tecno", "infinix", "itel", "nokia", "xiaomi",
    "redmi", "oppo", "vivo", "oneplus", "google", "pixel", "sony", "jbl", "anker",
    "beats", "bose", "lenovo", "dell", "hp", "asus", "acer", "toshiba", "msi",
    "casio", "canon", "nikon", "gopro", "nike", "adidas", "puma", "jansport",
    "northface", "kipling", "rayban", "fitbit", "garmin", "logitech", "kingston",
    "sandisk", "transcend", "realme", "techno",
})

# Brand tokens that are also item types ('casio' -> calculator) stay in both.
COLOR_TOKENS = frozenset(COLOR_INDEX) | frozenset(SHADE_INDEX)


def _lookup(token, index, fuzzy=True):
    if token in index:
        return index[token]
    if fuzzy:
        match, _ = best_fuzzy(token, index.keys())
        if match:
            return index[match]
    return None


def detect_types(tokens, fuzzy=True) -> set:
    """All plausible item types mentioned. Ambiguity is preserved, not resolved."""
    found = set()
    for tok in tokens:
        hit = _lookup(tok, TYPE_INDEX, fuzzy)
        if hit:
            found |= hit
    return found


def detect_colors(tokens, fuzzy=True):
    """Returns (colour families, shade words)."""
    colors, shades = set(), set()
    for tok in tokens:
        hit = _lookup(tok, COLOR_INDEX, fuzzy)
        if hit:
            colors.add(hit)
            continue
        hit = _lookup(tok, SHADE_INDEX, fuzzy)
        if hit:
            shades.add(hit)
    return colors, shades


def detect_brands(tokens, fuzzy=True) -> set:
    found = set()
    for tok in tokens:
        if tok in BRANDS:
            found.add(tok)
            continue
        if fuzzy:
            match, _ = best_fuzzy(tok, BRANDS)
            if match:
                found.add(match)
    return found
