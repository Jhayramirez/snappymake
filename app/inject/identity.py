from __future__ import annotations

import calendar
import random
import re
from typing import Any

from app.config import settings

DEFAULT_PASSWORD = settings.snapchat_default_password
BIRTH_YEARS = (2002, 2003, 2004)

GIRLY_FIRST = (
    "Aria", "Ava", "Bella", "Chloe", "Cora", "Daisy", "Eden", "Ella",
    "Ellie", "Elsa", "Emma", "Freya", "Grace", "Hazel", "Iris", "Isla",
    "Ivy", "Jade", "June", "Kenzie", "Lana", "Lila", "Lily", "Linda",
    "Lindsey", "Luna", "Maddy", "Maya", "Mia", "Mila", "Monique", "Naomi",
    "Nia", "Nina", "Nora", "Olive", "Poppy", "Ruby", "Sage", "Sienna",
    "Sofia", "Sophia", "Stella", "Suki", "Thea", "Tori", "Violet", "Willa",
    "Willow", "Winnie", "Zoe",
)

GIRLY_LAST = (
    "Belle", "Blair", "Brooks", "Hart", "Hayes", "Lane", "Quinn",
    "Reese", "Rose", "Scott", "Sutton", "Walsh",
)

SLANG_TAILS = (
    "star", "queen", "babe", "baby", "lovee", "honey", "kitty",
    "angel", "doll", "miss", "bby", "tea", "luxe", "moon",
    "peach", "cherry", "spark", "soft", "mini", "girly",
    "boo", "cutie", "sweet", "sunny", "pinky", "lala", "nini",
    "mimi", "bb", "xo",
)

BOYISH_FIRST = (
    "Aaron", "Adam", "Aiden", "Alex", "Andrew", "Austin", "Ben", "Blake",
    "Caleb", "Cameron", "Carter", "Chase", "Cole", "Colin", "Connor", "Daniel",
    "Dylan", "Ethan", "Evan", "Finn", "Gabe", "Henry", "Isaac", "Jack",
    "Jacob", "Jake", "James", "Jayden", "Jordan", "Julian", "Kai", "Leo",
    "Liam", "Logan", "Lucas", "Luke", "Mason", "Matt", "Max", "Miles",
    "Nathan", "Noah", "Owen", "Parker", "Ryan", "Sam", "Sean", "Theo",
    "Tyler", "Will", "Wyatt", "Zane",
)

BOYISH_LAST = (
    "Brooks", "Carter", "Cole", "Hayes", "Lane", "Parker", "Reed",
    "Scott", "Stone", "Walsh", "Wells", "West",
)

BOYISH_TAILS = (
    "bro", "boy", "dude", "king", "beast", "wolf", "fox", "ace",
    "pro", "max", "dash", "crew", "lab", "wave", "vibe", "mode",
    "zone", "play", "jet", "nix", "rio", "sky", "neo", "zen",
)

# Bare, ultra-common handles that are effectively always already taken on
# Snapchat. Used to make the first username attempt bounce like a real person's.
DECOY_EXTRAS_GIRLY = (
    "love", "queen", "angel", "baby", "princess", "bella", "daisy",
    "honey", "star", "sunshine", "cutie", "barbie", "kitty", "sweetie",
)

DECOY_EXTRAS_BOYISH = (
    "king", "bro", "dude", "beast", "wolf", "ace", "legend", "boss",
    "champ", "hero", "ninja", "shadow", "storm", "blaze",
)

# Back-compat alias
DECOY_EXTRAS = DECOY_EXTRAS_GIRLY

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def normalize_gender(value: str | None) -> str:
    return "male" if str(value or "").strip().lower() == "male" else "female"


def random_first_name(gender: str = "female") -> str:
    if normalize_gender(gender) == "male":
        return random.choice(BOYISH_FIRST)
    return random.choice(GIRLY_FIRST)


def random_last_name(gender: str = "female") -> str:
    if normalize_gender(gender) == "male":
        return random.choice(BOYISH_LAST)
    return random.choice(GIRLY_LAST)


def random_birthday() -> dict[str, int]:
    year = random.choice(BIRTH_YEARS)
    month = random.randint(1, 12)
    day = random.randint(1, calendar.monthrange(year, month)[1])
    return {"year": year, "month": month, "day": day}


def month_label(month: int) -> str:
    if 1 <= month <= 12:
        return MONTH_NAMES[month - 1]
    return str(month)


def _letters(text: str) -> str:
    return re.sub(r"[^a-zA-Z]", "", text or "").lower()


def _yify(word: str) -> str:
    word = _letters(word)
    extra = random.randint(1, 3)
    if word.endswith("y"):
        return word + ("y" * extra)
    return word + ("y" * extra)


def _mash(left: str, right: str) -> str:
    a, b = _letters(left), _letters(right)
    if not a:
        return b
    if not b:
        return a
    for n in range(min(4, len(a), len(b)), 0, -1):
        if a.endswith(b[:n]):
            return a + b[n:]
    return a + b


def _slang_username(
    first: str,
    *,
    first_pool: tuple[str, ...],
    tails: tuple[str, ...],
    mash_extra: str,
    fallback: str,
    avoid: set[str] | None = None,
) -> str:
    """Slangy handle, letters only, no underscore, Snapchat 3–15 chars."""
    first = _letters(first) or fallback
    banned = {x.lower() for x in (avoid or set())}
    others = [n.lower() for n in first_pool if n.lower() != first]
    if not others:
        others = [fallback]
    tail_list = list(tails)
    for _ in range(40):
        other = random.choice(others)
        tail = random.choice(tail_list)
        tail2 = random.choice(tail_list)
        candidates = [
            _mash(first, _yify(other)),
            _mash(first, other) + ("y" * random.randint(0, 3)),
            _mash(first, tail),
            _mash(first, tail + tail2),
            _mash(first, mash_extra),
            first + first,
            _yify(first),
            _mash(first, _yify(tail)),
            _mash(first, other),
        ]
        random.shuffle(candidates)
        long_ok = [
            _letters(raw)[:15]
            for raw in candidates
            if 8 <= len(_letters(raw)[:15]) <= 15
            and _letters(raw)[:15] not in banned
            and "_" not in _letters(raw)
        ]
        if long_ok:
            return random.choice(long_ok)
        for raw in candidates:
            name = _letters(raw)[:15]
            if 3 <= len(name) <= 15 and name not in banned and "_" not in name:
                return name
        padded = (first + "yyy")[:15]
        if 3 <= len(padded) <= 15 and padded not in banned:
            return padded
    fb = (first + "yy")[:15]
    return fb or fallback


def girly_username(first: str, year: int | None = None, *, avoid: set[str] | None = None) -> str:
    return _slang_username(
        first,
        first_pool=GIRLY_FIRST,
        tails=SLANG_TAILS,
        mash_extra="queenstar",
        fallback="lunayy",
        avoid=avoid,
    )


def boyish_username(first: str, year: int | None = None, *, avoid: set[str] | None = None) -> str:
    return _slang_username(
        first,
        first_pool=BOYISH_FIRST,
        tails=BOYISH_TAILS,
        mash_extra="kingace",
        fallback="noahyy",
        avoid=avoid,
    )


def decoy_username(
    first: str,
    *,
    avoid: set[str] | None = None,
    gender: str = "female",
) -> str:
    """A common handle almost certainly already taken on Snapchat.

    Prefers the bare first name (a real person's obvious first try), and falls
    back to a common word. Guaranteed 3–15 letters, no underscore.
    """
    banned = {x.lower() for x in (avoid or set())}
    bare = _letters(first)
    if 3 <= len(bare) <= 15 and bare not in banned:
        return bare
    extras = DECOY_EXTRAS_BOYISH if normalize_gender(gender) == "male" else DECOY_EXTRAS_GIRLY
    pool = [w for w in extras if 3 <= len(w) <= 15 and w not in banned]
    if pool:
        return random.choice(pool)
    return "king" if normalize_gender(gender) == "male" else "love"


def looks_machine_username(username: str) -> bool:
    text = (username or "").strip()
    if not text:
        return True
    if "_" in text or "-" in text:
        return True
    if re.search(r"\d", text):
        return True
    if re.match(r"^snap[_-]?", text, re.I):
        return True
    return False


def first_name_from_username(username: str, gender: str = "female") -> str | None:
    lower = (username or "").lower()
    pool = BOYISH_FIRST if normalize_gender(gender) == "male" else GIRLY_FIRST
    # Also scan the other pool so a carried-over username still maps.
    for name in sorted((*pool, *GIRLY_FIRST, *BOYISH_FIRST), key=len, reverse=True):
        if lower.startswith(name.lower()):
            return name
    return None


def generate_username(_prefix: str = "", *, gender: str = "female") -> str:
    g = normalize_gender(gender)
    first = random_first_name(g)
    if g == "male":
        return boyish_username(first)
    return girly_username(first)


def generate_password(_length: int = 16) -> str:
    return DEFAULT_PASSWORD


def build_identity(
    *,
    username: str = "",
    password: str = "",
    gender: str = "female",
) -> dict[str, Any]:
    g = normalize_gender(gender)
    bday = random_birthday()
    first = random_first_name(g)
    last = random_last_name(g)
    make_user = boyish_username if g == "male" else girly_username
    if username and not looks_machine_username(username):
        user = _letters(username.strip())
        first = first_name_from_username(user, g) or first
    else:
        user = make_user(first, bday["year"])
    return {
        "first_name": first,
        "last_name": last,
        "username": user,
        "password": (password or "").strip() or DEFAULT_PASSWORD,
        "birth_year": bday["year"],
        "birth_month": bday["month"],
        "birth_day": bday["day"],
        "birth_month_label": month_label(bday["month"]),
        "gender": g,
    }
