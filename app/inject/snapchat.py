from __future__ import annotations

import random
import re
import time
from pathlib import Path
from typing import Any, Callable

from app.inject.identity import (
    boyish_username,
    build_identity,
    decoy_username,
    girly_username,
    month_label,
    normalize_gender,
    random_first_name,
    random_last_name,
)

SNAPCHAT_SIGNUP_URL = "https://accounts.snapchat.com/v2/signup"
SNAPCHAT_LOGIN_URL = "https://accounts.snapchat.com/accounts/v2/login"
SNAPCHAT_WELCOME_URL = "https://accounts.snapchat.com/v2/welcome"
BITMOJI_HOME_URL = "https://www.bitmoji.com/"
USA_BOY_NAMES = (
    "James", "Michael", "David", "Daniel", "Ryan", "Chris", "Matthew",
    "Andrew", "Josh", "Tyler", "John", "Robert", "William", "Joseph",
    "Thomas", "Charles", "Christopher", "Anthony", "Mark", "Steven",
    "Paul", "Kevin", "Brian", "Jason", "Justin", "Brandon", "Eric",
    "Jacob", "Nicholas", "Jonathan", "Aaron", "Adam", "Benjamin",
    "Samuel", "Nathan", "Alexander", "Noah", "Ethan", "Logan", "Caleb",
    "Luke", "Jack", "Owen", "Hunter", "Connor", "Dylan", "Austin",
    "Jordan", "Kyle", "Zachary", "Nathaniel", "Sean", "Patrick", "Trevor",
)

FIRST_NAME_SELECTORS = [
    "#firstname",
    "input#firstname",
    'input[name="firstName"]',
    'input[placeholder*="First" i]',
    'input[aria-label*="First" i]',
    'input[placeholder="Pangalan"]',
    'input[autocomplete="given-name"]',
]
LAST_NAME_SELECTORS = [
    "#lastName",
    "input#lastName",
    'input[name="lastName"]',
    'input[placeholder*="Apelyido" i]',
    'input[autocomplete="family-name"]',
    'input[placeholder*="Last" i]',
    'input[aria-label*="Last" i]',
]
USERNAME_SELECTORS = [
    "#username",
    "input#username",
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[placeholder*="username" i]',
    'input[aria-label*="Username" i]',
]
PASSWORD_SELECTORS = [
    "#password",
    "input#password",
    'input[name="password"]',
    'input[type="password"]',
    'input[autocomplete="new-password"]',
    'input[aria-label*="Password" i]',
]
EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[autocomplete="email"]',
    'input[placeholder*="Email" i]',
    'input[aria-label*="Email" i]',
]
PHONE_SELECTORS = [
    "#phoneNumber",
    'input[name="phoneNumber"]',
    'input[autocomplete="tel"]',
    'input[autocomplete="tel-national"]',
    'input[type="tel"]',
    'input[placeholder*="Phone" i]',
    'input[aria-label*="Phone" i]',
]
OTP_SELECTORS = [
    'input[name="otp"]',
    'input[autocomplete="one-time-code"]',
    'input[inputmode="numeric"]',
    'input[placeholder*="code" i]',
    'input[aria-label*="code" i]',
]
NEXT_SELECTORS = [
    'button:has-text("Agree and Continue")',
    'button:has-text("Agree & Continue")',
    'button:has-text("Sign Up & Accept")',
    'button:has-text("Next")',
    'button:has-text("Continue")',
    'button:has-text("Send")',
    'button:has-text("Verify")',
    'button:has-text("Confirm")',
    'button:has-text("Sign Up")',
    'button:has-text("Agree")',
    'button:has-text("Sumang-ayon at Magpatuloy")',
    'form button[type="submit"]',
    'button[type="submit"]',
]

FILIPINO_MARKERS = (
    "Pangalan",
    "Apelyido",
    "Mag-sign Up",
    "Sumang-ayon",
    "Magpatuloy",
    "Hakbang",
    "Buwan",
    "Enero",
    "Pebrero",
)

ENGLISH_MARKERS = (
    "First Name",
    "First name",
    "Agree and Continue",
    "Agree & Continue",
    "Sign Up & Accept",
    "Birthday",
)

FILIPINO_MONTHS = (
    "Enero",
    "Pebrero",
    "Marso",
    "Abril",
    "Mayo",
    "Hunyo",
    "Hulyo",
    "Agosto",
    "Setyembre",
    "Oktubre",
    "Nobyembre",
    "Disyembre",
)

USERNAME_SHORT_HINTS = (
    "at least 3",
    "3 character",
    "3 characters",
    "minimum of 3",
    "too short",
    "mas maikli",
    "hindi maaaring mas",
    "dapat 3",
    "3 na character",
    "kahit 3",
    "must be at least",
    "minimum length",
)

USERNAME_TAKEN_HINTS = (
    "already taken",
    "already in use",
    "username is taken",
    "has been taken",
    "is taken",
    "not available",
    "unavailable",
    "try another",
    "try a different",
    "someone already",
    "can't be used",
    "cannot be used",
    "can't use that",
    "please choose another",
    "ginagamit na",
    "hindi available",
    "may gumagamit",
    "gumagamit na",
    "subukan ang ibang",
    "try another username",
)


def random_display_name() -> tuple[str, str]:
    return random_first_name(), random_last_name()


def _pause(page, lo: int = 280, hi: int = 820) -> None:
    page.wait_for_timeout(random.randint(lo, hi))


def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _human_type(page, loc, value: str, label: str, filled: list[str]) -> bool:
    if not value or loc is None:
        return False
    text = str(value)
    try:
        loc.scroll_into_view_if_needed(timeout=2500)
        loc.click(timeout=3500)
        _pause(page, 120, 280)
        itype = ""
        try:
            itype = (loc.get_attribute("type") or "").lower()
        except Exception:
            itype = ""
        try:
            loc.click(click_count=3, timeout=1500)
            loc.press("Backspace")
        except Exception:
            pass
        if itype == "number":
            loc.fill(text, timeout=8000)
            _commit_react_input(loc, text)
        else:
            delay = random.randint(70, 150)
            loc.press_sequentially(text, delay=delay, timeout=20000)
            _commit_react_input(loc, text)
        _pause(page, 180, 360)
        got = _read_value(loc)
        if not _value_matches(got, text, label):
            loc.press_sequentially(text, delay=80, timeout=20000)
            _commit_react_input(loc, text)
            _pause(page, 120, 240)
            got = _read_value(loc)
        if not _value_matches(got, text, label):
            return False
        if label not in filled:
            filled.append(label)
        _pause(page)
        return True
    except Exception:
        try:
            loc.fill(text, timeout=8000)
            _commit_react_input(loc, text)
            got = _read_value(loc)
            if _value_matches(got, text, label):
                if label not in filled:
                    filled.append(label)
                _pause(page)
                return True
        except Exception:
            return False
        return False


def _read_value(loc) -> str:
    try:
        return str(loc.input_value(timeout=800) or "")
    except Exception:
        try:
            return str(loc.evaluate("el => el.value || ''") or "")
        except Exception:
            return ""


def _commit_react_input(loc, value: str) -> None:
    try:
        loc.evaluate(
            """(el, value) => {
                const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                if (desc && desc.set) desc.set.call(el, value);
                else el.value = value;
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    cancelable: true,
                    inputType: 'insertText',
                    data: value,
                }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            value,
        )
    except Exception:
        pass


def _value_matches(got: str, want: str, label: str = "") -> bool:
    g = str(got or "").strip()
    w = str(want or "").strip()
    if not w:
        return bool(g)
    if label in {"birth_month", "birth_day", "birth_year"}:
        return g.lstrip("0") == w.lstrip("0") or g == w
    return g == w


def _type_first(page, selectors: list[str], value: str, label: str, filled: list[str]) -> bool:
    loc = _first_visible(page, selectors)
    for _ in range(3):
        if _human_type(page, loc, value, label, filled):
            return True
        loc = _first_visible(page, selectors)
        _pause(page, 200, 400)
    return False


def _loc(page, *selectors: str):
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count():
                return loc
        except Exception:
            continue
    return None


def _select_month_value(page, month: int) -> bool:
    loc = _loc(page, "#month", "select#month", 'select[name="month"]', '[data-testid="month-input"]')
    if loc is None:
        return False
    month_s = str(int(month))
    eng = month_label(int(month))
    fil = FILIPINO_MONTHS[int(month) - 1] if 1 <= int(month) <= 12 else month_s
    for kwargs in (
        {"value": month_s},
        {"label": eng},
        {"label": fil},
        {"index": int(month)},
    ):
        try:
            loc.select_option(**kwargs, timeout=2500)
            if _value_matches(_read_value(loc), month_s, "birth_month"):
                return True
        except Exception:
            continue
    try:
        loc.evaluate(
            """(el, want) => {
                el.value = String(want);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            month_s,
        )
        return _value_matches(_read_value(loc), month_s, "birth_month")
    except Exception:
        return False


def _fill_snap_birthday(page, identity: dict[str, Any], filled: list[str], notes: list[str]) -> bool:
    """Snap v2: month is <select id=month values 1-12>, day/year are type=number."""
    try:
        page.locator("#month, [data-testid='month-input']").first.wait_for(state="visible", timeout=12000)
    except Exception:
        pass
    month_ok = _select_month_value(page, identity["birth_month"])
    if month_ok and "birth_month" not in filled:
        filled.append("birth_month")
        _pause(page, 180, 360)
    day_loc = _loc(page, "#day", 'input[name="day"]', '[data-testid="day-input"]')
    year_loc = _loc(page, "#year", 'input[name="year"]', '[data-testid="year-input"]')
    if day_loc is not None:
        _human_type(page, day_loc, str(identity["birth_day"]), "birth_day", filled)
    if year_loc is not None:
        _human_type(page, year_loc, str(identity["birth_year"]), "birth_year", filled)
    ok = _birthday_complete(page, identity, filled)
    if not ok:
        notes.append("snap_birthday_dom_miss")
    return ok


def _choose_dropdown(page, *, names: list[str], values: list[str], label: str, filled: list[str]) -> bool:
    locators = []
    for name in names:
        locators.extend(
            [
                page.locator(f'select[name="{name}"]'),
                page.locator(f"select#{name}"),
                page.locator(f'select[name*="{name}" i]'),
                page.locator(f'[aria-label="{name}" i]'),
                page.get_by_label(name, exact=False),
                page.get_by_role("combobox", name=name, exact=False),
            ]
        )
    for raw in locators:
        loc = raw.first
        try:
            if loc.count() == 0 or not loc.is_visible():
                continue
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=3000)
            _pause(page, 140, 320)
            tag = ""
            try:
                tag = (loc.evaluate("el => el.tagName") or "").lower()
            except Exception:
                tag = ""
            if tag == "select":
                for value in values:
                    try:
                        loc.select_option(value, timeout=1500)
                        filled.append(label)
                        _pause(page)
                        return True
                    except Exception:
                        try:
                            loc.select_option(label=value, timeout=1500)
                            filled.append(label)
                            _pause(page)
                            return True
                        except Exception:
                            continue
            typed = str(values[0])
            delay = random.randint(60, 120)
            loc.press_sequentially(typed, delay=delay, timeout=8000)
            loc.press("Enter")
            filled.append(label)
            _pause(page)
            return True
        except Exception:
            continue
    return False


def _control_blob(loc) -> str:
    try:
        return (
            loc.evaluate(
                """el => [
                    el.name, el.id, el.title,
                    el.getAttribute('aria-label'),
                    el.getAttribute('placeholder'),
                    el.getAttribute('autocomplete')
                ].filter(Boolean).join(' ')"""
            )
            or ""
        ).lower()
    except Exception:
        return ""


def _kind_from_blob(blob: str) -> str:
    b = (blob or "").lower()
    if "month" in b:
        return "month"
    if "year" in b:
        return "year"
    if "day" in b:
        return "day"
    return ""


def _select_matching(page, kind: str, values: list[str]) -> bool:
    try:
        total = page.locator("select").count()
    except Exception:
        return False
    for i in range(total):
        loc = page.locator("select").nth(i)
        if _kind_from_blob(_control_blob(loc)) != kind:
            continue
        for value in values:
            try:
                loc.select_option(value=str(value), timeout=2000)
                return True
            except Exception:
                try:
                    loc.select_option(label=str(value), timeout=2000)
                    return True
                except Exception:
                    continue
    return False


def _type_matching_input(page, kind: str, value: str, label: str, filled: list[str]) -> bool:
    try:
        total = page.locator("input").count()
    except Exception:
        return False
    for i in range(total):
        loc = page.locator("input").nth(i)
        try:
            itype = (loc.get_attribute("type") or "text").lower()
            if itype in {"hidden", "password", "email", "checkbox", "radio", "submit"}:
                continue
            if not loc.is_visible():
                continue
        except Exception:
            continue
        if _kind_from_blob(_control_blob(loc)) != kind:
            continue
        if _human_type(page, loc, value, label, filled):
            return True
    return False


def _pick_option(page, labels: list[str]) -> bool:
    for label in labels:
        for loc in (
            page.get_by_role("option", name=str(label), exact=True),
            page.locator(f'[role="option"]:text-is("{label}")'),
            page.locator(f'li:text-is("{label}")'),
            page.locator(f'div[role="listbox"] >> text="{label}"'),
        ):
            try:
                hit = loc.first
                if hit.count() == 0:
                    continue
                hit.scroll_into_view_if_needed(timeout=2000)
                hit.click(timeout=2500)
                return True
            except Exception:
                continue
    return False


def _open_named_control(page, names: list[str]):
    locators = []
    for name in names:
        locators.extend(
            [
                page.get_by_role("combobox", name=name, exact=False),
                page.get_by_label(name, exact=False),
                page.locator(f'[aria-label="{name}" i]'),
                page.locator(f'button:has-text("{name}")'),
            ]
        )
    for raw in locators:
        loc = raw.first
        try:
            if loc.count() == 0 or not loc.is_visible():
                continue
            loc.scroll_into_view_if_needed(timeout=2000)
            loc.click(timeout=3000)
            return loc
        except Exception:
            continue
    return None


def _read_birthday(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
                const out = {month:'', day:'', year:''};
                const take = (el) => (el.value || el.innerText || '').trim();
                for (const el of document.querySelectorAll('select, input')) {
                    const blob = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder]
                        .filter(Boolean).join(' ').toLowerCase();
                    const val = take(el);
                    if (!val || val.toLowerCase().includes('month') || val.toLowerCase() === 'day'
                        || val.toLowerCase() === 'year') continue;
                    if (!out.month && /month/.test(blob)) out.month = val;
                    else if (!out.day && /\\bday\\b|birthday/.test(blob) && !/month|year/.test(blob)) out.day = val;
                    else if (!out.year && /year/.test(blob)) out.year = val;
                }
                return out;
            }"""
        )
    except Exception:
        return {"month": "", "day": "", "year": ""}


def _birthday_complete(page, identity: dict[str, Any], filled: list[str]) -> bool:
    got = _read_birthday(page)
    year = str(identity["birth_year"])
    day = str(identity["birth_day"])
    month_ok = "birth_month" in filled or bool(got.get("month"))
    day_ok = "birth_day" in filled or str(got.get("day") or "").lstrip("0") == day
    year_ok = "birth_year" in filled or str(got.get("year") or "") == year
    return bool(month_ok and day_ok and year_ok)


def _fill_one_birthday(
    page,
    *,
    kind: str,
    names: list[str],
    values: list[str],
    label: str,
    filled: list[str],
    type_value: str | None = None,
) -> bool:
    if label in filled:
        return True
    if _select_matching(page, kind, values):
        filled.append(label)
        _pause(page)
        return True
    if type_value and _type_matching_input(page, kind, type_value, label, filled):
        return True
    opened = _open_named_control(page, names)
    if opened is not None:
        _pause(page, 160, 360)
        if _pick_option(page, [str(v) for v in values]):
            if label not in filled:
                filled.append(label)
            _pause(page)
            return True
        if type_value:
            try:
                delay = random.randint(60, 120)
                opened.press_sequentially(type_value, delay=delay, timeout=8000)
                opened.press("Enter")
                filled.append(label)
                _pause(page)
                return True
            except Exception:
                pass
    if _choose_dropdown(page, names=names, values=values, label=label, filled=filled):
        return True
    return False


def _force_birthday(page, identity: dict[str, Any]) -> None:
    try:
        page.evaluate(
            """([monthName, monthNum, day, year]) => {
                const setSelect = (el, wants) => {
                    const opts = [...el.options];
                    for (const want of wants) {
                        const hit = opts.find(o =>
                            o.value === String(want) ||
                            o.text.trim() === String(want) ||
                            o.value === String(want).padStart(2, '0') ||
                            o.text.trim() === String(want).padStart(2, '0')
                        );
                        if (!hit) continue;
                        el.value = hit.value;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                };
                for (const el of document.querySelectorAll('select')) {
                    const b = [el.name, el.id, el.getAttribute('aria-label')].filter(Boolean).join(' ').toLowerCase();
                    if (b.includes('month')) setSelect(el, [monthName, monthNum]);
                    else if (b.includes('year')) setSelect(el, [year]);
                    else if (b.includes('day')) setSelect(el, [day]);
                }
                for (const el of document.querySelectorAll('input')) {
                    const b = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder]
                        .filter(Boolean).join(' ').toLowerCase();
                    if (!b.includes('year') || b.includes('month')) continue;
                    const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                    if (proto && proto.set) proto.set.call(el, year);
                    else el.value = year;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""",
            [
                identity["birth_month_label"],
                str(identity["birth_month"]),
                str(identity["birth_day"]),
                str(identity["birth_year"]),
            ],
        )
    except Exception:
        pass


def _fill_birthday(page, identity: dict[str, Any], filled: list[str], notes: list[str]) -> bool:
    if _fill_snap_birthday(page, identity, filled, notes):
        return True
    year = str(identity["birth_year"])
    month_num = str(identity["birth_month"])
    month_pad = month_num.zfill(2)
    day = str(identity["birth_day"])
    day_pad = day.zfill(2)
    month_name = identity["birth_month_label"]
    for attempt in range(3):
        if _birthday_complete(page, identity, filled):
            for key in ("birth_month", "birth_day", "birth_year"):
                if key not in filled:
                    filled.append(key)
            return True
        _fill_one_birthday(
            page,
            kind="month",
            names=["Month", "birthMonth", "month"],
            values=[month_name, month_num, month_pad],
            label="birth_month",
            filled=filled,
        )
        _pause(page, 250, 500)
        _fill_one_birthday(
            page,
            kind="day",
            names=["Day", "birthDay", "day"],
            values=[day, day_pad],
            label="birth_day",
            filled=filled,
            type_value=day,
        )
        _pause(page, 250, 500)
        _fill_one_birthday(
            page,
            kind="year",
            names=["Year", "birthYear", "year"],
            values=[year],
            label="birth_year",
            filled=filled,
            type_value=year,
        )
        if _birthday_complete(page, identity, filled):
            for key in ("birth_month", "birth_day", "birth_year"):
                if key not in filled:
                    filled.append(key)
            return True
        notes.append(f"birthday_retry_{attempt + 1}")
        for key in ("birth_month", "birth_day", "birth_year"):
            if key in filled:
                filled.remove(key)
        _pause(page, 400, 800)
    _force_birthday(page, identity)
    if _birthday_complete(page, identity, filled):
        for key in ("birth_month", "birth_day", "birth_year"):
            if key not in filled:
                filled.append(key)
        notes.append("birthday_forced")
        return True
    notes.append("birthday_incomplete")
    return False


def _error_texts(page) -> str:
    parts: list[str] = []
    try:
        loc = page.locator('[data-testid="error-text"]')
        for i in range(loc.count()):
            parts.append(loc.nth(i).inner_text() or "")
    except Exception:
        pass
    try:
        loc = page.locator('[class*="ErrorMessage"], [class*="error__"], [role="alert"]')
        for i in range(min(loc.count(), 8)):
            parts.append(loc.nth(i).inner_text() or "")
    except Exception:
        pass
    return " ".join(parts).lower()


def _username_error_text(page) -> str:
    return _error_texts(page)


def _username_taken(page) -> bool:
    blob = _username_error_text(page)
    return any(hint in blob for hint in USERNAME_TAKEN_HINTS)


def _username_dom_value(page) -> str:
    loc = _first_visible(page, USERNAME_SELECTORS)
    if loc is None:
        return ""
    return _read_value(loc).strip()


def _username_too_short(page) -> bool:
    loc = _first_visible(page, USERNAME_SELECTORS)
    if loc is None:
        return False
    return len(_read_value(loc).strip()) < 3


def _username_short_message(page) -> bool:
    blob = _username_error_text(page)
    return any(hint in blob for hint in USERNAME_SHORT_HINTS)


def _blur_username(page) -> None:
    loc = _first_visible(page, USERNAME_SELECTORS)
    if loc is None:
        return
    try:
        loc.press("Tab")
    except Exception:
        try:
            loc.evaluate("el => el.blur()")
        except Exception:
            pass


def _wait_username_ready(page, expected: str, notes: list[str]) -> bool:
    want = str(expected or "").strip()
    for _ in range(40):
        got = _username_dom_value(page)
        if len(got) >= 3 and (not want or _value_matches(got, want, "username")):
            return True
        page.wait_for_timeout(250)
    notes.append(
        f"username_not_ready:dom={_username_dom_value(page)!r} want={want!r}"
    )
    return False


def _form_values(page) -> dict[str, str]:
    try:
        return page.evaluate(
            """() => {
                const val = (sel) => {
                    const el = document.querySelector(sel);
                    return el && 'value' in el ? String(el.value || '') : '';
                };
                return {
                    first: val('#firstname, input[name="firstName"]'),
                    last: val('#lastName, input[name="lastName"]'),
                    month: val('#month, select[name="month"]'),
                    day: val('#day, input[name="day"]'),
                    year: val('#year, input[name="year"]'),
                    username: val('#username, input[name="username"]'),
                    password: val('#password, input[name="password"]'),
                };
            }"""
        )
    except Exception:
        return {}


def _clear_last_name(page, notes: list[str]) -> None:
    loc = _first_visible(page, LAST_NAME_SELECTORS)
    if loc is None:
        return
    try:
        current = _read_value(loc)
        if current:
            loc.click(timeout=2000)
            loc.fill("")
            notes.append("cleared_last_name")
    except Exception:
        pass


def _ensure_fields(page, identity: dict[str, Any], filled: list[str], notes: list[str], on_step) -> bool:
    for attempt in range(3):
        _clear_last_name(page, notes)
        vals = _form_values(page)
        if not _value_matches(vals.get("first") or "", identity["first_name"], "first_name"):
            _type_first(page, FIRST_NAME_SELECTORS, identity["first_name"], "first_name", filled)
        if not _value_matches(vals.get("month") or "", str(identity["birth_month"]), "birth_month"):
            _fill_snap_birthday(page, identity, filled, notes)
        else:
            if not _value_matches(vals.get("day") or "", str(identity["birth_day"]), "birth_day"):
                day_loc = _loc(page, "#day", 'input[name="day"]', '[data-testid="day-input"]')
                _human_type(page, day_loc, str(identity["birth_day"]), "birth_day", filled)
            if not _value_matches(vals.get("year") or "", str(identity["birth_year"]), "birth_year"):
                year_loc = _loc(page, "#year", 'input[name="year"]', '[data-testid="year-input"]')
                _human_type(page, year_loc, str(identity["birth_year"]), "birth_year", filled)
        if not _value_matches(vals.get("username") or "", identity["username"], "username"):
            _type_first(page, USERNAME_SELECTORS, identity["username"], "username", filled)
        if not _value_matches(vals.get("password") or "", identity["password"], "password"):
            _type_first(page, PASSWORD_SELECTORS, identity["password"], "password", filled)
        vals = _form_values(page)
        ok = (
            _value_matches(vals.get("first") or "", identity["first_name"], "first_name")
            and _value_matches(vals.get("month") or "", str(identity["birth_month"]), "birth_month")
            and _value_matches(vals.get("day") or "", str(identity["birth_day"]), "birth_day")
            and _value_matches(vals.get("year") or "", str(identity["birth_year"]), "birth_year")
            and _value_matches(vals.get("username") or "", identity["username"], "username")
            and _value_matches(vals.get("password") or "", identity["password"], "password")
        )
        if ok:
            for key in ("first_name", "birth_month", "birth_day", "birth_year", "username", "password"):
                if key not in filled:
                    filled.append(key)
            return True
        safe = {k: ("*" if k == "password" else v) for k, v in vals.items()}
        notes.append(f"field_verify_retry_{attempt + 1}:{safe}")
        if on_step:
            on_step("retyping empty fields")
        _pause(page, 300, 600)
    return False


def _body_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def _page_is_filipino(page) -> bool:
    body = _body_text(page)
    return any(word in body for word in FILIPINO_MARKERS)


def _page_is_english(page) -> bool:
    body = _body_text(page)
    return any(word in body for word in ENGLISH_MARKERS)


def _with_english_locale(url: str) -> str:
    if "locale=" in url or "lang=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}locale=en-US"


def _apply_english_cdp(page, notes: list[str]) -> None:
    try:
        session = page.context.new_cdp_session(page)
        session.send("Emulation.setLocaleOverride", {"locale": "en-US"})
        ua = page.evaluate("() => navigator.userAgent")
        session.send(
            "Network.setUserAgentOverride",
            {"userAgent": ua, "acceptLanguage": "en-US,en;q=0.9"},
        )
        notes.append("locale_en-US")
    except Exception:
        pass
    try:
        page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
    except Exception:
        pass


def _click_first_text(page, labels: tuple[str, ...]) -> bool:
    for label in labels:
        for loc in (
            page.get_by_role("button", name=label, exact=False),
            page.get_by_role("link", name=label, exact=False),
            page.get_by_text(label, exact=False),
            page.locator(f'option:has-text("{label}")'),
        ):
            try:
                target = loc.first
                if target.count() and target.is_visible():
                    target.click(timeout=2500)
                    return True
            except Exception:
                continue
    return False


COOKIE_ACCEPT_LABELS = (
    "Accept All",
    "Accept all cookies",
    "Accept all",
    "Allow all",
    "Allow All",
)

COOKIE_ACCEPT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    '[id*="accept-btn-handler"]',
    'button:has-text("Accept All")',
    'button:has-text("Accept all cookies")',
    'button:has-text("Accept all")',
    'button:has-text("Allow all")',
    'button:has-text("Allow All")',
)


def _cookie_wall_visible(page) -> bool:
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    if "cookies help us" in body or "manage cookie" in body or "cookie preferences" in body:
        return True
    return "cookie" in body and "accept all" in body


def _dismiss_cookie_banner(page, notes: list[str], on_step=None) -> bool:
    """Snap's cookie modal blocks the signup form until Accept All is clicked."""
    clicked = False
    for sel in COOKIE_ACCEPT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                try:
                    loc.scroll_into_view_if_needed(timeout=1200)
                except Exception:
                    pass
                loc.click(timeout=2500)
                clicked = True
                break
        except Exception:
            continue
    if not clicked and _cookie_wall_visible(page):
        clicked = _click_first_text(page, COOKIE_ACCEPT_LABELS)
    if clicked:
        notes.append("clicked_accept_all_cookies")
        if on_step:
            on_step("clicked_accept_all_cookies")
        try:
            page.wait_for_timeout(random.randint(500, 1000))
        except Exception:
            pass
        return True
    return False


def _pick_english_ui(page, notes: list[str]) -> bool:
    if _page_is_english(page) and not _page_is_filipino(page):
        notes.append("page_already_english")
        return True
    opened = _click_first_text(page, ("Filipino", "Tagalog", "Pilipino", "Filipinas"))
    if opened:
        notes.append("opened_language_menu")
        _pause(page, 280, 520)
    picked = _click_first_text(
        page,
        ("English (US)", "English (United States)", "English"),
    )
    if not picked:
        try:
            for sel in page.locator("select").all():
                try:
                    sel.select_option(label="English (US)")
                    picked = True
                    break
                except Exception:
                    try:
                        sel.select_option(value="en-US")
                        picked = True
                        break
                    except Exception:
                        continue
        except Exception:
            pass
    if picked:
        notes.append("clicked_english_ui")
        _pause(page, 700, 1200)
        return _page_is_english(page)
    return False


def _force_english(page, start_url: str, notes: list[str]) -> None:
    """Prefer AdsPower page language. CDP locale/UA override is a bot tell on proxies."""
    if _page_is_english(page) and not _page_is_filipino(page):
        notes.append("page_is_english")
        return
    if _pick_english_ui(page, notes) and _page_is_english(page) and not _page_is_filipino(page):
        notes.append("page_is_english")
        return
    if _page_is_filipino(page):
        notes.append("page_was_filipino_reloading")
        try:
            page.goto(_with_english_locale(start_url), wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(random.randint(700, 1200))
        except Exception:
            pass
        _pick_english_ui(page, notes)
        if _page_is_filipino(page):
            _apply_english_cdp(page, notes)
            _pick_english_ui(page, notes)
    if _page_is_filipino(page):
        notes.append("page_still_filipino")
    elif _page_is_english(page):
        notes.append("page_is_english")


def _fill_username(page, identity: dict[str, Any], filled: list[str], notes: list[str], on_step) -> bool:
    """Type the current username once. Snapchat only validates it after Next."""
    name = identity["username"]
    if "username" in filled:
        filled.remove("username")
    for _ in range(3):
        if not _type_first(page, USERNAME_SELECTORS, name, "username", filled):
            notes.append("username_field_missing")
            return False
        _blur_username(page)
        _pause(page, 400, 800)
        if _wait_username_ready(page, name, notes):
            return True
        notes.append(f"username_short_retype:{name}")
        if on_step:
            on_step("username too short · retyping")
    return False


def _required_ready(page, filled: list[str]) -> bool:
    need = {"first_name", "username", "password", "birth_month", "birth_day", "birth_year"}
    return need.issubset(set(filled))


PROCESS_ERROR_PHRASES = (
    "unable to process your request",
    "unable to process this request",
    "were unable to process",
    "we were unable to process",
    "sorry, we were unable",
    "sorry we were unable",
    "we are sorry, we were unable",
    "cannot process your request",
    "couldn't process your request",
    "could not process your request",
    "unable to process",
    "we cannot process",
    "sorry we cannot",
    "sorry, we cannot",
)


def _process_error_visible(page) -> bool:
    try:
        blob = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(phrase in blob for phrase in PROCESS_ERROR_PHRASES)


# Snapchat rejects certain email providers/addresses at the email step. These
# phrases only appear AFTER a well-formed address is submitted, so seeing them
# means the domain/address was refused (e.g. an unsupported provider).
EMAIL_REJECT_PHRASES = (
    "enter a valid email",
    "valid email address",
    "not a valid email",
    "email address is invalid",
    "invalid email",
    "email provider is not supported",
    "provider is not supported",
    "email is not supported",
    "not supported",
    "use a different email",
    "different email address",
    "try a different email",
    "couldn't verify that this is a valid email",
    "couldn't verify your email",
    "problem with your email",
    "this email can't be used",
    "email can't be used",
    "email cannot be used",
)


def _email_rejected(page) -> bool:
    """True if Snapchat is showing an email-rejection message.

    Only meaningful right after an email has been submitted at the email step.
    """
    try:
        blob = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(phrase in blob for phrase in EMAIL_REJECT_PHRASES)


# Distinct from a bad-provider rejection: the address is fine, it was just already
# used on another Snapchat account. We should rent/reorder a fresh email and retry
# rather than deleting the profile.
EMAIL_TAKEN_PHRASES = (
    "already been verified by another account",
    "already verified by another account",
    "verified by another account",
    "already been verified",
    "please enter another email",
    "enter another email",
    "already associated with",
    "email is already in use",
    "already in use",
    "already registered",
    "already taken",
)


def _email_taken(page) -> bool:
    """True if Snapchat says the email is already used by another account."""
    try:
        blob = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(phrase in blob for phrase in EMAIL_TAKEN_PHRASES)


def _clear_email_input(page) -> None:
    """Empty the email field so a fresh address can be typed in."""
    loc = _first_visible(page, EMAIL_SELECTORS)
    if loc is None:
        return
    try:
        loc.scroll_into_view_if_needed(timeout=2000)
        loc.click(timeout=2500)
        loc.click(click_count=3, timeout=1500)
        loc.press("Backspace")
        _commit_react_input(loc, "")
        _pause(page, 150, 320)
    except Exception:
        pass


def _human_scroll_into_view(page, loc) -> None:
    try:
        for _ in range(random.randint(2, 4)):
            page.mouse.wheel(0, random.randint(140, 280))
            _pause(page, 120, 260)
    except Exception:
        pass
    try:
        loc.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    try:
        loc.evaluate("el => el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })")
    except Exception:
        pass
    _pause(page, 380, 760)
    try:
        page.mouse.wheel(0, random.randint(18, 55))
    except Exception:
        pass
    _pause(page, 160, 320)


def _human_click(page, loc) -> bool:
    _human_scroll_into_view(page, loc)
    try:
        loc.hover(timeout=3500)
    except Exception:
        pass
    _pause(page, 220, 480)
    box = None
    try:
        box = loc.bounding_box()
    except Exception:
        box = None
    try:
        if box and box.get("width", 0) > 8 and box.get("height", 0) > 8:
            x = box["x"] + box["width"] * random.uniform(0.38, 0.62)
            y = box["y"] + box["height"] * random.uniform(0.40, 0.60)
            page.mouse.move(x, y, steps=random.randint(12, 24))
            _pause(page, 90, 210)
            page.mouse.down()
            _pause(page, 55, 140)
            page.mouse.up()
            return True
        loc.click(delay=random.randint(55, 140), timeout=4000)
        return True
    except Exception:
        try:
            loc.click(timeout=4000)
            return True
        except Exception:
            return False


def _find_visible_next(page):
    for selector in NEXT_SELECTORS:
        candidate = page.locator(selector).first
        try:
            if candidate.count() and candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _wait_process_error(page, tries: int = 8) -> bool:
    for _ in range(max(1, tries)):
        if _process_error_visible(page):
            return True
        try:
            page.wait_for_timeout(280)
        except Exception:
            return _process_error_visible(page)
    return _process_error_visible(page)


def _click_next(page, notes: list[str], on_step=None, *, expected_username: str = "") -> bool:
    if expected_username:
        if not _wait_username_ready(page, expected_username, notes):
            notes.append("blocked_next_username_short")
            return False
        if _username_too_short(page):
            notes.append("blocked_next_username_hint")
            return False
    _pause(page, 1800, 3200)
    loc = _find_visible_next(page)
    if loc is None:
        return False
    for _ in range(28):
        try:
            disabled = loc.get_attribute("disabled")
            if loc.is_enabled() and disabled is None:
                break
        except Exception:
            pass
        page.wait_for_timeout(250)
    else:
        notes.append("next_still_disabled")
        return False
    clicked = _human_click(page, loc)
    if not clicked:
        return False
    _note(on_step, notes, "clicked_next")
    _pause(page, 900, 1600)
    if expected_username and (_username_too_short(page) or _username_short_message(page)):
        notes.append("username_short_after_next")
        return False
    retries = random.randint(10, 15)
    for retry in range(1, retries + 1):
        if not _wait_process_error(page):
            break
        _note(on_step, notes, f"process_error_retry:{retry}")
        _pause(page, 800, 1400)
        retry_loc = _find_visible_next(page) or loc
        if not _human_click(page, retry_loc):
            continue
        _pause(page, 900, 1600)
    if _process_error_visible(page):
        _note(on_step, notes, "process_error_stuck")
        return False
    return True


def _otp_boxes(page):
    for selector in (
        'input[maxlength="1"]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
    ):
        loc = page.locator(selector)
        try:
            if loc.count() >= 4:
                return loc
        except Exception:
            continue
    return None


def _type_otp_keys(page, text: str) -> None:
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(90, 170))
        _pause(page, 70, 150)


# After the signup form, Snapchat sometimes lands on the phone-number screen
# with a small link to switch to email entry. Click it so we can type the mail.
USE_EMAIL_LABELS = (
    "Use email instead",
    "Use Email Instead",
    "Sign up with email",
    "Sign Up with Email",
    "Continue with email",
    "Use email address",
    "Use my email",
    "Email instead",
    "Use email",
)


# Targeted selectors for the "Use Email Instead" control (class hashes vary,
# so match on a substring). Ordered from most to least specific.
USE_EMAIL_SELECTORS = (
    '[class*="useEmailInstead"] a[role="button"]',
    '[class*="useEmailInstead"] a',
    '[class*="useEmailInstead"] button',
    '[class*="useEmailInstead"]',
    'a[role="button"]:has-text("Use Email Instead")',
    'a[role="button"]:has-text("Use email instead")',
    'button:has-text("Use Email Instead")',
    'a:has-text("Use Email Instead")',
    'a:has-text("Use email instead")',
)

USE_PHONE_LABELS = (
    "Use phone instead",
    "Use Phone Instead",
    "Sign up with phone",
    "Sign up with phone number",
    "Use phone number instead",
    "Use mobile instead",
    "Use phone number",
    "Phone instead",
    "Use phone",
)

USE_PHONE_SELECTORS = (
    '[class*="usePhoneInstead"] a[role="button"]',
    '[class*="usePhoneInstead"] a',
    '[class*="usePhoneInstead"] button',
    '[class*="usePhoneInstead"]',
    'a[role="button"]:has-text("Use Phone Instead")',
    'a[role="button"]:has-text("Use phone instead")',
    'button:has-text("Use Phone Instead")',
    'button:has-text("Use phone instead")',
    'a:has-text("Use Phone Instead")',
    'a:has-text("Use phone instead")',
    'a:has-text("Use phone number")',
)


def _phone_step_visible(page) -> bool:
    """True if Snapchat is showing the phone-number verification step."""
    try:
        loc = page.locator('#phoneNumber, input[name="phoneNumber"]').first
        if loc.count() and loc.is_visible():
            return True
    except Exception:
        pass
    try:
        loc = page.locator('input[type="tel"], input[autocomplete="tel"], input[autocomplete="tel-national"]').first
        if loc.count() and loc.is_visible():
            return True
    except Exception:
        pass
    try:
        if page.locator('[class*="PhoneNumberVerification"]').first.count():
            return True
    except Exception:
        pass
    try:
        return page.locator('[class*="useEmailInstead"]').first.count() > 0
    except Exception:
        return False


PHONE_REJECT_PHRASES = (
    "invalid phone",
    "invalid number",
    "phone number is invalid",
    "couldn't send",
    "could not send",
    "can't send",
    "cannot send",
    "cannot verify this phone",
    "can't verify this phone",
    "we cannot verify this phone number",
    "we can't verify this phone number",
    "request verification with another",
    "verification with another phone",
    "another phone number",
    "try a different number",
    "try another number",
    "this number can't be used",
    "this number cannot be used",
    "number can't be used",
    "unable to send",
    "too many attempts",
)


def _phone_rejected(page) -> bool:
    try:
        blob = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(phrase in blob for phrase in PHONE_REJECT_PHRASES)


def _national_us_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _ensure_us_country(page) -> None:
    """Prefer United States / +1 on the phone step."""
    try:
        sel = page.locator("select").first
        if sel.count() and sel.is_visible():
            for kwargs in ({"label": "United States"}, {"label": "United States (+1)"}, {"value": "US"}):
                try:
                    sel.select_option(**kwargs, timeout=1500)
                    return
                except Exception:
                    continue
    except Exception:
        pass
    try:
        box = page.get_by_role("combobox").first
        if box.count() and box.is_visible():
            cur = (box.inner_text() or "").lower()
            if "united states" in cur or "+1" in cur:
                return
            box.click(timeout=2000)
            page.wait_for_timeout(400)
            opt = page.get_by_text("United States", exact=False).first
            if opt.count():
                opt.click(timeout=2500)
    except Exception:
        pass


def _fill_phone_number(page, phone: str, filled: list[str], notes: list[str], on_step) -> bool:
    national = _national_us_phone(phone)
    if not national:
        return False
    _ensure_us_country(page)
    if "phone" in filled:
        filled.remove("phone")
    if not _type_first(page, PHONE_SELECTORS, national, "phone", filled):
        return False
    _note(on_step, notes, f"typed_phone:+1{national}")
    return True


def _use_email_instead(page, notes: list[str], on_step=None) -> bool:
    """If the phone step is showing, switch it to email entry. Returns True once
    an email input is present (either it already was, or after clicking the link)."""
    # Already on an email field → nothing to do.
    if _first_visible(page, EMAIL_SELECTORS) is not None:
        return True
    clicked = False
    for sel in USE_EMAIL_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                loc.click(timeout=2500)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        clicked = _click_first_text(page, USE_EMAIL_LABELS)
    if clicked:
        _note(on_step, notes, "clicked_use_email_instead")
        for _ in range(10):
            page.wait_for_timeout(300)
            if _first_visible(page, EMAIL_SELECTORS) is not None:
                return True
    return _first_visible(page, EMAIL_SELECTORS) is not None


def _use_phone_instead(page, notes: list[str], on_step=None) -> bool:
    """If Snap is on email signup, switch to the phone-number field."""
    if _phone_step_visible(page):
        return True
    clicked = False
    for sel in USE_PHONE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                try:
                    loc.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                loc.click(timeout=2500)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        clicked = _click_first_text(page, USE_PHONE_LABELS)
    if clicked:
        _note(on_step, notes, "clicked_use_phone_instead")
        for _ in range(12):
            page.wait_for_timeout(350)
            if _phone_step_visible(page):
                return True
    return _phone_step_visible(page)


def _email_or_otp_visible(page) -> str:
    if _first_visible(page, EMAIL_SELECTORS) is not None:
        return "email"
    # Check the phone step BEFORE OTP. The phone/country-code inputs use
    # inputmode="numeric", which matches an OTP selector — so without this
    # the "Step 2 of 3" phone screen is misread as the OTP step and we never
    # click "Use Email Instead".
    if _phone_step_visible(page):
        return "phone"
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        body = ""
    codeish = "verification code" in body or "confirmation code" in body or "enter the code" in body
    if "email address" in body and not codeish:
        return "email"
    if any(word in body for word in ("phone number", "mobile number", "use email instead")) and not codeish:
        return "phone"
    if _first_visible(page, OTP_SELECTORS) is not None or _otp_boxes(page) is not None:
        return "otp"
    if codeish:
        return "otp"
    if "email" in body and any(word in body for word in ("verify", "code", "confirmation")):
        return "email"
    return ""


def _wait_for_stage(page, wanted: str, tries: int = 24) -> str:
    for _ in range(tries):
        stage = _email_or_otp_visible(page)
        if wanted == "any" and stage:
            return stage
        if stage == wanted:
            return stage
        page.wait_for_timeout(500)
    return _email_or_otp_visible(page)


def _type_otp(page, code: str, filled: list[str]) -> bool:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if not digits:
        return False
    boxes = _otp_boxes(page)
    if boxes is not None:
        try:
            first = boxes.nth(0)
            first.scroll_into_view_if_needed(timeout=2500)
            first.click(timeout=3000)
            _pause(page, 160, 280)
            for _ in range(8):
                page.keyboard.press("Backspace")
            _type_otp_keys(page, digits[: boxes.count()])
            filled.append("otp")
            return True
        except Exception:
            pass
        try:
            for i, ch in enumerate(digits):
                if i >= boxes.count():
                    break
                box = boxes.nth(i)
                box.click(timeout=2500)
                _pause(page, 80, 160)
                page.keyboard.press("Backspace")
                page.keyboard.type(ch, delay=random.randint(90, 170))
                _pause(page, 90, 180)
            filled.append("otp")
            return True
        except Exception:
            return False
    loc = _first_visible(page, OTP_SELECTORS)
    if loc is None:
        return False
    try:
        loc.scroll_into_view_if_needed(timeout=2500)
        loc.click(timeout=3000)
        _pause(page, 160, 280)
        for _ in range(12):
            page.keyboard.press("Backspace")
        _type_otp_keys(page, digits)
        filled.append("otp")
        return True
    except Exception:
        return False


def _note(on_step: Callable[[str], None] | None, notes: list[str], message: str) -> None:
    notes.append(message)
    if on_step:
        on_step(message)


def _first_visible_locator(candidates):
    for loc in candidates:
        try:
            target = loc.first
            if target.count() and target.is_visible():
                return target
        except Exception:
            continue
    return None


def _on_welcome_page(page, username: str = "") -> bool:
    url = ""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if "accounts.snapchat.com/v2/welcome" in url:
        return True
    body = _body_text(page)
    blob = body.lower()
    if username and username.lower() in blob and ("chat" in blob or "my snapcode" in blob):
        return True
    return "my snapcode" in blob or "content visibility" in blob


def _wait_for_welcome(page, notes: list[str], on_step, username: str = "", tries: int = 40) -> bool:
    for _ in range(tries):
        if _on_welcome_page(page, username):
            _note(on_step, notes, "welcome_page_visible")
            return True
        page.wait_for_timeout(500)
    if _on_welcome_page(page, username):
        _note(on_step, notes, "welcome_page_visible")
        return True
    return False


def _find_chat_control(page):
    """Welcome page Chat — top nav link to snapchat.com/web (opens a new tab)."""
    return _first_visible_locator(
        [
            page.locator('[data-testid="navItem-CHAT"]'),
            page.locator('a[aria-label="CHAT"]'),
            page.locator('a[href*="snapchat.com/web"]'),
            page.locator("li.ConsumerNav_item__kOUD6 a"),
            page.get_by_role("link", name="CHAT", exact=True),
            page.get_by_role("link", name="Chat", exact=True),
            page.get_by_role("button", name="Chat now", exact=True),
            page.get_by_role("link", name="Chat now", exact=True),
            page.get_by_text("Chat now", exact=True),
        ]
    )


def _welcome_tab(context, page, username: str = ""):
    if _on_welcome_page(page, username):
        return page
    try:
        pages = list(context.pages)
    except Exception:
        pages = [page]
    for candidate in pages:
        if _on_welcome_page(candidate, username):
            _bring_page(candidate)
            return candidate
    return page


def _accounts_auth_kind(url: str) -> str | None:
    """Detect logged-out Snapchat URLs.

    - accounts.snapchat.com/v2/signup|login
    - www.snapchat.com/?original_referrer=… (web session redirect when logged out)
    - bare www.snapchat.com/ (same landing, no /web/<uuid>)
    """
    u = (url or "").strip().lower()
    if not u or "snapchat.com" not in u:
        return None
    # Still on a real web session deep-link — not logout by URL alone.
    if re.search(r"snapchat\.com/web/[0-9a-f-]{20,}", u):
        return None
    if "accounts.snapchat.com" in u:
        if "signup" in u:
            return "signup"
        if "login" in u:
            return "login"
        return None
    # Logged-out marketing / web login landing (screenshot: snapchat.com/?original_referrer=none)
    if "bitmoji.com" in u:
        return None
    if "original_referrer" in u:
        return "login"
    if re.search(r"https?://(www\.)?snapchat\.com/?(\?|#|$)", u):
        return "login"
    return None


def _signup_form_visible(page) -> bool:
    """True when the real signup form is painted (not just a signup URL flash).

    Live sessions often briefly hit /v2/signup before cookies redirect to welcome.
    The first-name field (plus username or password) means the form actually loaded.
    """
    try:
        has_first = _first_visible(page, FIRST_NAME_SELECTORS) is not None
        if not has_first:
            return False
        has_user = _first_visible(page, USERNAME_SELECTORS) is not None
        has_pass = _first_visible(page, PASSWORD_SELECTORS) is not None
        return has_user or has_pass or has_first
    except Exception:
        return False


def _web_login_landing_visible(page) -> bool:
    """www.snapchat.com login wall: 'Log in to Snapchat' + username/email (no /web session)."""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if "snapchat.com/web/" in url:
        return False
    blob = _body_text(page).lower()
    if "log in to snapchat" in blob:
        return True
    if "username or email" in blob and (
        "use phone number instead" in blob or "log in" in blob
    ):
        return True
    return False


def _login_form_visible(page) -> bool:
    """True when a login form is painted (accounts or www.snapchat.com landing)."""
    if _web_login_landing_visible(page):
        return True
    try:
        if _first_visible(page, FIRST_NAME_SELECTORS) is not None:
            return False
        has_pass = _first_visible(page, PASSWORD_SELECTORS) is not None
        has_user = _first_visible(page, USERNAME_SELECTORS) is not None
        return has_pass and has_user
    except Exception:
        return False


def _auth_form_logout_kind(page) -> str | None:
    """Return signup|login only when the auth *form* / login landing is visible."""
    if _signup_form_visible(page):
        return "signup"
    if _login_form_visible(page):
        return "login"
    return None


def _find_logged_out_tab(context, pages=None) -> tuple[Any, str] | tuple[None, None]:
    """Return (page, signup|login) if any open tab is on accounts signup/login."""
    seen: set[int] = set()
    for candidate in list(pages or []) + list(getattr(context, "pages", []) or []):
        try:
            key = id(candidate)
            if key in seen:
                continue
            seen.add(key)
            kind = _accounts_auth_kind(getattr(candidate, "url", None) or "")
            if kind:
                return candidate, kind
        except Exception:
            continue
    return None, None


def _is_snapchat_web(page) -> bool:
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if "snapchat.com/web" in url:
        return True
    blob = _body_text(page).lower()
    return any(
        marker in blob
        for marker in (
            "welcome to snapchat for web",
            "add your best friends",
            "heads up!",
            "my ai",
        )
    )


def _wait_snapchat_web(page, tries: int = 40) -> bool:
    for _ in range(tries):
        if _is_snapchat_web(page):
            return True
        try:
            page.wait_for_timeout(400)
        except Exception:
            return False
    return _is_snapchat_web(page)


def _find_snapchat_web_tab(context, opened, page):
    for candidate in list(opened) + list(context.pages):
        try:
            if _is_snapchat_web(candidate):
                try:
                    candidate.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
                _bring_page(candidate)
                return candidate
        except Exception:
            continue
    try:
        if _is_snapchat_web(page):
            return page
    except Exception:
        pass
    return None


def _open_web_from_welcome(page, context, notes: list[str], on_step):
    opened: list = []

    def _on_page(new_page) -> None:
        opened.append(new_page)

    try:
        context.on("page", _on_page)
    except Exception:
        pass

    try:
        for attempt in range(1, 4):
            already = _find_snapchat_web_tab(context, opened, page)
            if already is not None:
                _note(on_step, notes, "web_onboard_chat")
                return already
            loc = _find_chat_control(page)
            if loc is None:
                _note(on_step, notes, f"web_onboard_chat_not_found_try:{attempt}")
                page.wait_for_timeout(500)
                continue
            _note(on_step, notes, f"web_onboard_chat_try:{attempt}")
            clicked = False
            try:
                with context.expect_page(timeout=5000) as pending:
                    clicked = _human_click(page, loc)
                if clicked:
                    opened.append(pending.value)
            except Exception:
                if not clicked:
                    clicked = _human_click(page, loc)
            if not clicked:
                _note(on_step, notes, f"web_onboard_chat_click_failed_try:{attempt}")
            found = None
            deadline = time.time() + 10
            while time.time() < deadline:
                found = _find_snapchat_web_tab(context, opened, page)
                if found is not None:
                    _note(on_step, notes, "web_onboard_chat")
                    return found
                page.wait_for_timeout(350)
            _note(on_step, notes, "web_onboard_chat_still_here")
        _note(on_step, notes, "web_onboard_chat_stuck")
        return page
    finally:
        try:
            context.remove_listener("page", _on_page)
        except Exception:
            pass


def _web_has_text(page, needle: str) -> bool:
    return needle.lower() in _body_text(page).lower()


def _poll(page, pred, tries: int = 24, wait: int = 350) -> bool:
    for _ in range(max(1, tries)):
        try:
            if pred():
                return True
        except Exception:
            pass
        try:
            page.wait_for_timeout(wait)
        except Exception:
            return False
    try:
        return bool(pred())
    except Exception:
        return False


def _expect_step(
    page,
    notes: list[str],
    on_step,
    *,
    name: str,
    here,
    nxt,
    act,
    retries: int = 3,
    appear_tries: int = 40,
    leave_tries: int = 16,
) -> bool:
    """Expect `here`, click until `nxt`. Retry if the screen does not move."""
    if _poll(page, nxt, tries=3, wait=200):
        return True
    if not _poll(page, lambda: here() or nxt(), tries=appear_tries):
        _note(on_step, notes, f"{name}_not_seen")
        return False
    if nxt():
        return True
    for attempt in range(1, retries + 1):
        if nxt():
            _note(on_step, notes, name)
            return True
        _note(on_step, notes, f"{name}_try:{attempt}")
        try:
            act()
        except Exception as exc:
            _note(on_step, notes, f"{name}_act_failed:{exc}")
        if _poll(page, nxt, tries=leave_tries):
            _note(on_step, notes, name)
            return True
        if here():
            _note(on_step, notes, f"{name}_still_here")
    if nxt():
        _note(on_step, notes, name)
        return True
    _note(on_step, notes, f"{name}_stuck")
    return False


def _click_web_control(page, locators, notes: list[str], on_step, note: str) -> bool:
    loc = _first_visible_locator(locators)
    if loc is None:
        return False
    if not _click_xy(page, loc) and not _human_click(page, loc):
        return False
    _note(on_step, notes, note)
    _pause(page, 700, 1300)
    return True


def _click_when_labeled(page, locators, label: str, tries: int = 16) -> bool:
    """Wait until the control's visible text is painted, then click without page-wheel."""
    needle = (label or "").strip().lower()
    for _ in range(tries):
        loc = _first_visible_locator(locators)
        if loc is not None:
            try:
                text = (loc.inner_text(timeout=400) or "").strip().lower()
            except Exception:
                text = ""
            if needle and needle not in text:
                loc = None
            elif _click_xy(page, loc) or _human_click(page, loc):
                return True
        page.wait_for_timeout(300)
    return False


def _dismiss_web_welcome(page, notes: list[str], on_step) -> None:
    locators = [
        page.get_by_role("button", name="Next", exact=True),
        page.locator("button").filter(has_text="Next"),
        page.locator('button:has-text("Next")'),
    ]
    _expect_step(
        page,
        notes,
        on_step,
        name="web_onboard_next",
        here=lambda: _web_has_text(page, "Welcome to Snapchat for Web"),
        nxt=lambda: (
            _web_has_text(page, "Add your best friends")
            or _web_has_text(page, "Heads up!")
            or _web_has_text(page, "My AI")
        ),
        act=lambda: _click_when_labeled(page, locators, "Next"),
    )


def _skip_best_friends(page, notes: list[str], on_step) -> None:
    locators = [
        page.get_by_role("button", name="Skip", exact=True),
        page.get_by_role("link", name="Skip", exact=True),
        page.locator("button").filter(has_text="Skip"),
        page.get_by_text("Skip", exact=True),
        page.locator('button:has-text("Skip")'),
    ]
    _expect_step(
        page,
        notes,
        on_step,
        name="web_onboard_skip",
        here=lambda: _web_has_text(page, "Add your best friends"),
        nxt=lambda: (
            _web_has_text(page, "Heads up!")
            or _web_has_text(page, "My AI")
            or _web_has_text(page, "Team Snapchat")
        ),
        act=lambda: _click_when_labeled(page, locators, "Skip"),
    )


def _dismiss_heads_up(page, notes: list[str], on_step) -> None:
    locators = [
        page.get_by_role("button", name="Not now", exact=True),
        page.get_by_text("Not now", exact=True),
        page.locator('button:has-text("Not now")'),
    ]
    close_locs = [
        page.locator('[aria-label="close"]'),
        page.locator('[aria-label="Close"]'),
        page.get_by_role("button", name="Close", exact=True),
        page.get_by_role("button", name="Dismiss", exact=True),
        page.locator('button[aria-label*="close" i]'),
    ]

    def _act() -> bool:
        if _click_when_labeled(page, locators, "Not now", tries=10):
            return True
        loc = _first_visible_locator(close_locs)
        if loc is None:
            return False
        return _click_xy(page, loc) or _human_click(page, loc)

    _expect_step(
        page,
        notes,
        on_step,
        name="web_onboard_heads_up_dismissed",
        here=lambda: _web_has_text(page, "Heads up!"),
        nxt=lambda: (
            (not _web_has_text(page, "Heads up!"))
            and (
                _web_has_text(page, "My AI")
                or _web_has_text(page, "Team Snapchat")
            )
        ),
        act=_act,
        retries=3,
        appear_tries=16,
        leave_tries=12,
    )


def _leftmost_text(page, name: str):
    loc = page.get_by_text(name, exact=True)
    best = None
    best_x = 10**9
    try:
        count = min(loc.count(), 8)
    except Exception:
        count = 0
    for i in range(count):
        item = loc.nth(i)
        try:
            if not item.is_visible():
                continue
            box = item.bounding_box()
        except Exception:
            continue
        if not box or box.get("width", 0) < 20 or box.get("height", 0) < 10:
            continue
        if box["x"] < best_x:
            best_x = box["x"]
            best = item
    return best


def _find_inbox_row(page, name: str):
    left = _leftmost_text(page, name)
    if left is not None:
        return left
    return _first_visible_locator(
        [
            page.get_by_role("listitem").filter(has_text=name),
            page.get_by_role("button", name=name, exact=True),
            page.get_by_role("link", name=name, exact=True),
            page.get_by_text(name, exact=True),
        ]
    )


def _dismiss_desktop_app_banner(page, notes: list[str], on_step) -> None:
    if not (
        _web_has_text(page, "Click to install the Desktop App")
        or _web_has_text(page, "Desktop App")
    ):
        return
    banner = page.get_by_text("Click to install the Desktop App", exact=False)
    locators = []
    try:
        locators.append(banner.locator("xpath=ancestor::*[.//button][1]").get_by_role("button").last)
    except Exception:
        pass
    locators.extend(
        [
            page.get_by_role("button", name="Close", exact=False),
            page.locator('[aria-label*="close" i]'),
            page.locator('[aria-label*="dismiss" i]'),
        ]
    )
    _click_web_control(page, locators, notes, on_step, "web_inbox_desktop_banner_dismissed")


def _dismiss_screenshot_nudge(page, notes: list[str], on_step) -> None:
    if not _web_has_text(page, "Looks like you're trying to take a screenshot"):
        return
    loc = _first_visible_locator(
        [
            page.get_by_text("Looks like you're trying to take a screenshot!", exact=False),
            page.get_by_text("Click anywhere to return to the conversation", exact=False),
        ]
    )
    dismissed = False
    if loc is not None:
        dismissed = _human_click(page, loc)
    if not dismissed:
        try:
            page.mouse.click(random.randint(420, 720), random.randint(260, 420))
            dismissed = True
        except Exception:
            dismissed = False
    if dismissed:
        _note(on_step, notes, "web_inbox_screenshot_nudge_dismissed")
        _pause(page, 500, 900)


def _human_glance_conversation(page) -> None:
    _pause(page, 700, 1400)
    scrollable = False
    try:
        scrollable = bool(
            page.evaluate(
                """() => {
                    const root = document.scrollingElement || document.documentElement;
                    if (root && root.scrollHeight > root.clientHeight + 80) return true;
                    const nodes = document.querySelectorAll('div');
                    for (const n of nodes) {
                        const style = getComputedStyle(n);
                        const oy = style.overflowY;
                        if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 80) {
                            return true;
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        scrollable = True
    if not scrollable:
        _pause(page, 400, 800)
        return
    try:
        for _ in range(random.randint(1, 3)):
            page.mouse.wheel(0, random.randint(90, 240))
            _pause(page, 200, 450)
    except Exception:
        pass
    _pause(page, 400, 900)


def _tap_inbox_row(page, name: str, notes: list[str], on_step, note: str) -> bool:
    loc = _find_inbox_row(page, name)
    if loc is None:
        _dismiss_desktop_app_banner(page, notes, on_step)
        loc = _find_inbox_row(page, name)
    if loc is None:
        _note(on_step, notes, f"{note}_not_found")
        return False
    if not _human_click(page, loc):
        _dismiss_desktop_app_banner(page, notes, on_step)
        loc = _find_inbox_row(page, name)
        if loc is None or not _human_click(page, loc):
            _note(on_step, notes, f"{note}_click_failed")
            return False
    _note(on_step, notes, note)
    _pause(page, 600, 1100)
    _dismiss_screenshot_nudge(page, notes, on_step)
    return True


def _browse_web_inbox(page, notes: list[str], on_step) -> None:
    for _ in range(20):
        if _web_has_text(page, "My AI") or _web_has_text(page, "Team Snapchat"):
            break
        page.wait_for_timeout(400)
    _tap_inbox_row(page, "My AI", notes, on_step, "web_inbox_my_ai")
    _human_glance_conversation(page)
    _tap_inbox_row(page, "Team Snapchat", notes, on_step, "web_inbox_team_snapchat")
    _pause(page, 500, 900)
    _dismiss_screenshot_nudge(page, notes, on_step)


def _page_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _bring_page(page) -> None:
    # Default: leave the OS focus on Cursor. CDP clicks still work.
    try:
        from app.ads.client import no_focus_enabled

        if no_focus_enabled():
            return
    except Exception:
        pass
    try:
        page.bring_to_front()
    except Exception:
        pass


def _context_pages(context, fallback=None):
    try:
        return list(context.pages)
    except Exception:
        return [fallback] if fallback is not None else []


def _click_xy(page, loc) -> bool:
    """Click without page-wheel scrolling (iframe builder / small buttons)."""
    try:
        loc.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    _pause(page, 120, 280)
    box = None
    try:
        box = loc.bounding_box()
    except Exception:
        box = None
    try:
        if box and box.get("width", 0) > 4 and box.get("height", 0) > 4:
            x = box["x"] + box["width"] * random.uniform(0.40, 0.60)
            y = box["y"] + box["height"] * random.uniform(0.40, 0.60)
            page.mouse.move(x, y, steps=random.randint(8, 16))
            _pause(page, 60, 140)
            page.mouse.click(x, y, delay=random.randint(40, 90))
            return True
        loc.click(delay=random.randint(40, 90), timeout=4000)
        return True
    except Exception:
        try:
            loc.click(force=True, timeout=3000)
            return True
        except Exception:
            return False


def _builder_frame(page):
    try:
        frames = list(page.frames)
    except Exception:
        return None
    for frame in frames:
        try:
            if "sdk.bitmoji.com/web-builder" in (frame.url or ""):
                return frame
        except Exception:
            continue
    return None


def _wait_builder_frame(page, tries: int = 90):
    for _ in range(max(1, tries)):
        frame = _builder_frame(page)
        if frame is not None:
            blob = _body_text(frame).lower()
            if "save" in blob or "skin tone" in blob or "hairstyle" in blob:
                return frame
        page.wait_for_timeout(400)
    return _builder_frame(page)


def _find_bitmoji_create_page(context):
    for candidate in _context_pages(context):
        url = _page_url(candidate).lower()
        if "bitmoji.com/avatar/create" in url:
            return candidate
    return None


def _find_bitmoji_home_page(context):
    for candidate in _context_pages(context):
        url = _page_url(candidate).lower()
        if "bitmoji.com/home" in url:
            return candidate
    return None


def _click_continue_fast(page) -> bool:
    loc = _first_visible_locator(
        [
            page.get_by_role("button", name="Continue", exact=True),
            page.locator('button:has-text("Continue")'),
        ]
    )
    if loc is None:
        return False
    return _click_xy(page, loc)


def _bitmoji_go_locators(page):
    return [
        page.get_by_role("link", name="Go to my account", exact=True),
        page.get_by_role("button", name="Go to my account", exact=True),
        page.get_by_text("Go to my account", exact=True),
        page.locator('a:has-text("Go to my account")'),
        page.locator('button:has-text("Go to my account")'),
    ]


def _bitmoji_login_locators(page):
    return [
        page.locator("button.sc-button").filter(has_text="Log In with Snapchat"),
        page.get_by_role("button", name="Log In with Snapchat", exact=True),
        page.locator('button:has-text("Log In with Snapchat")'),
        page.get_by_text("Log In with Snapchat", exact=True),
    ]


def _bitmoji_login_ready(page) -> bool:
    if _find_bitmoji_create_page(page.context) is not None:
        return True
    if _first_visible_locator(_bitmoji_login_locators(page)) is not None:
        return True
    blob = _body_text(page).lower()
    return "log in with snapchat" in blob or "have a snapchat account" in blob


def _bitmoji_oauth_visible(context) -> bool:
    for candidate in _context_pages(context):
        url = _page_url(candidate).lower()
        if "devtools" in url:
            continue
        blob = _body_text(candidate).lower()
        if (
            "authorization successful" in blob
            or "click continue to return" in blob
            or "continue to bitmoji" in blob
        ):
            return True
    return False


def _bitmoji_create_ready(context) -> bool:
    if _find_bitmoji_create_page(context) is not None:
        return True
    for candidate in _context_pages(context):
        try:
            female = candidate.locator('button[aria-label="Female Avatar"]')
            male = candidate.locator('button[aria-label="Male Avatar"]')
            if (female.count() and female.first.is_visible()) or (
                male.count() and male.first.is_visible()
            ):
                return True
        except Exception:
            pass
        if _builder_frame(candidate) is not None:
            return True
    return False


def _bitmoji_oauth_or_create(context) -> bool:
    return _bitmoji_create_ready(context) or _bitmoji_oauth_visible(context)


def _click_bitmoji_login(page) -> bool:
    snap = _first_visible_locator(_bitmoji_login_locators(page))
    if snap is not None:
        return _click_xy(page, snap) or _human_click(page, snap)
    blob = _body_text(page).lower()
    if "log in with snapchat" not in blob and "have a snapchat account" not in blob:
        return False
    try:
        box = page.evaluate(
            """() => {
                const el = Array.from(document.querySelectorAll('button')).find(
                    b => (b.innerText || '').trim() === 'Log In with Snapchat'
                );
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: r.x, y: r.y, w: r.width, h: r.height};
            }"""
        )
    except Exception:
        box = None
    if box and box.get("w", 0) > 8:
        page.mouse.click(
            box["x"] + box["w"] / 2,
            box["y"] + box["h"] / 2,
            delay=random.randint(40, 90),
        )
        return True
    return False


def _click_oauth_continues(context, notes: list[str], on_step) -> None:
    """First Continue to Bitmoji, then immediately Authorization Successful Continue."""
    saw_first = False
    for candidate in _context_pages(context):
        url = _page_url(candidate).lower()
        if "devtools" in url:
            continue
        blob = _body_text(candidate).lower()
        if "authorization successful" in blob or "click continue to return" in blob:
            _bring_page(candidate)
            if _click_continue_fast(candidate):
                _note(on_step, notes, "bitmoji_oauth_continue_return")
                try:
                    candidate.wait_for_timeout(200)
                except Exception:
                    pass
            continue
        if "continue to bitmoji" in blob:
            _bring_page(candidate)
            if _click_continue_fast(candidate):
                _note(on_step, notes, "bitmoji_oauth_continue")
                saw_first = True
                try:
                    candidate.wait_for_timeout(180)
                except Exception:
                    pass
    if not saw_first:
        return
    for candidate in _context_pages(context):
        url = _page_url(candidate).lower()
        if "devtools" in url:
            continue
        blob = _body_text(candidate).lower()
        if "authorization successful" in blob or "click continue to return" in blob:
            _bring_page(candidate)
            if _click_continue_fast(candidate):
                _note(on_step, notes, "bitmoji_oauth_continue_return")
            return


def _bitmoji_timeout_page(context, fallback=None):
    pages = _context_pages(context, fallback)
    preferred = []
    others = []
    for candidate in pages:
        url = _page_url(candidate).lower()
        if "devtools" in url:
            continue
        if "bitmoji" in url or "accounts.snapchat" in url:
            preferred.append(candidate)
        else:
            others.append(candidate)
    if preferred:
        return preferred[0]
    if others:
        return others[0]
    return fallback


def _bitmoji_oauth_continues(context, notes: list[str], on_step) -> None:
    """First Continue, then immediately the Authorization Successful Continue."""
    page = _bitmoji_timeout_page(context)
    if page is None:
        return
    _expect_step(
        page,
        notes,
        on_step,
        name="bitmoji_oauth_continues",
        here=lambda: _bitmoji_oauth_visible(context),
        nxt=lambda: _bitmoji_create_ready(context),
        act=lambda: _click_oauth_continues(context, notes, on_step),
        retries=3,
        appear_tries=50,
        leave_tries=24,
    )


def _open_bitmoji_and_login(context, notes: list[str], on_step):
    page = context.new_page()
    page.goto(BITMOJI_HOME_URL, wait_until="domcontentloaded", timeout=90000)
    _bring_page(page)
    _note(on_step, notes, "bitmoji_home")
    _pause(page, 600, 1100)

    def _click_go() -> None:
        loc = _first_visible_locator(_bitmoji_go_locators(page))
        if loc is None:
            return
        _human_click(page, loc)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        _pause(page, 700, 1200)

    _expect_step(
        page,
        notes,
        on_step,
        name="bitmoji_go_to_account",
        here=lambda: (
            _first_visible_locator(_bitmoji_go_locators(page)) is not None
            or _web_has_text(page, "Go to my account")
        ),
        nxt=lambda: _bitmoji_login_ready(page),
        act=_click_go,
        retries=3,
        appear_tries=40,
        leave_tries=20,
    )
    if _find_bitmoji_create_page(page.context) is not None:
        _note(on_step, notes, "bitmoji_already_create")
        return page
    _expect_step(
        page,
        notes,
        on_step,
        name="bitmoji_login_with_snapchat",
        here=lambda: _bitmoji_login_ready(page),
        nxt=lambda: _bitmoji_oauth_or_create(page.context),
        act=lambda: _click_bitmoji_login(page),
        retries=3,
        appear_tries=40,
        leave_tries=24,
    )
    return page


def _builder_grid_info(frame, category_id: str) -> dict[str, Any]:
    try:
        info = frame.evaluate(
            """(cid) => {
                const root = document.querySelector('#' + cid);
                const title = (document.querySelector('.title')?.innerText || '').trim();
                if (!root) return {count: 0, cols: 0, title};
                const items = Array.from(root.querySelectorAll('.trait-preview-container'));
                const laid = items.filter((el) => el.offsetWidth > 4 && el.offsetHeight > 4);
                const xs = [...new Set(laid.map((el) => el.offsetLeft))].sort((a, b) => a - b);
                return {count: laid.length, cols: xs.length, title};
            }""",
            category_id,
        )
    except Exception:
        return {}
    return info if isinstance(info, dict) else {}


def _wait_builder_grid(
    page,
    frame,
    category_id: str,
    *,
    min_count: int,
    expect_cols: int = 4,
    title_needle: str = "",
    tries: int = 50,
) -> bool:
    needle = title_needle.lower()
    for _ in range(max(1, tries)):
        info = _builder_grid_info(frame, category_id)
        title = str(info.get("title") or "").lower()
        count = int(info.get("count") or 0)
        cols = int(info.get("cols") or 0)
        title_ok = (not needle) or needle in title
        if title_ok and count >= min_count and cols >= expect_cols:
            return True
        page.wait_for_timeout(300)
    info = _builder_grid_info(frame, category_id)
    title = str(info.get("title") or "").lower()
    title_ok = (not needle) or needle in title
    return (
        title_ok
        and int(info.get("count") or 0) >= min_count
        and int(info.get("cols") or 0) >= expect_cols
    )


def _click_builder_cell(
    page,
    frame,
    category_id: str,
    row: int,
    col: int,
    *,
    expect_cols: int = 4,
) -> bool:
    try:
        idx = frame.evaluate(
            """({cid, row, col, expectCols}) => {
                const root = document.querySelector('#' + cid);
                if (!root) return -1;
                const items = Array.from(root.querySelectorAll('.trait-preview-container'));
                const laid = items.filter((el) => el.offsetWidth > 4 && el.offsetHeight > 4);
                const xs = [...new Set(laid.map((el) => el.offsetLeft))].sort((a, b) => a - b);
                if (xs.length < expectCols) return -1;
                const cols = xs.length;
                const i = (row - 1) * cols + (col - 1);
                if (!laid[i]) return -1;
                laid[i].scrollIntoView({block: 'center', inline: 'nearest'});
                return items.indexOf(laid[i]);
            }""",
            {"cid": category_id, "row": row, "col": col, "expectCols": expect_cols},
        )
    except Exception:
        return False
    if idx is None or int(idx) < 0:
        return False
    loc = frame.locator(f"#{category_id} .trait-preview-container").nth(int(idx))
    return _click_xy(page, loc)


def _open_hairstyle_panel(page, frame) -> bool:
    title = ""
    try:
        title = (frame.locator(".title").first.inner_text(timeout=2000) or "").lower()
    except Exception:
        title = ""
    if "hairstyle" in title:
        return True
    tab = frame.locator(".category-item.top-category-container").nth(1)
    if not _click_xy(page, tab):
        return False
    _pause(page, 500, 900)
    for _ in range(5):
        try:
            title = (frame.locator(".title").first.inner_text(timeout=1500) or "").lower()
        except Exception:
            title = ""
        if "hairstyle" in title:
            return True
        arrow = frame.locator("#arrow_btn_forward")
        if not _click_xy(page, arrow):
            break
        _pause(page, 350, 650)
    return "hairstyle" in title


def _save_bitmoji_avatar(page, frame, context, notes: list[str], on_step) -> bool:
    save = frame.locator(".save-button")
    if not _click_xy(page, save):
        _note(on_step, notes, "bitmoji_save_not_found")
        return False
    _note(on_step, notes, "bitmoji_save")
    for _ in range(24):
        blob = _body_text(frame).lower()
        if "ready to pick an outfit" in blob:
            yes = frame.locator(".modal-primary-button")
            if _click_xy(page, yes):
                _note(on_step, notes, "bitmoji_outfit_yes")
            break
        page.wait_for_timeout(280)
    for _ in range(24):
        if "outfits" in _body_text(frame).lower():
            break
        page.wait_for_timeout(280)
    _pause(page, 400, 800)
    save = frame.locator(".save-button")
    if _click_xy(page, save):
        _note(on_step, notes, "bitmoji_save_outfit")

    def _on_home() -> bool:
        if _find_bitmoji_home_page(context) is not None:
            return True
        return "bitmoji.com/home" in _page_url(page).lower()

    def _click_parent_save() -> None:
        btn = page.locator('.dialog-modal.show button[type="submit"]')
        _click_xy(page, btn) or _human_click(page, btn)

    _expect_step(
        page,
        notes,
        on_step,
        name="bitmoji_save_confirm",
        here=lambda: "save your new bitmoji" in _body_text(page).lower(),
        nxt=_on_home,
        act=_click_parent_save,
        retries=3,
        appear_tries=40,
        leave_tries=24,
    )
    home = _find_bitmoji_home_page(context)
    if home is not None:
        _bring_page(home)
        _note(on_step, notes, "bitmoji_account_home")
        return True
    if _on_home():
        _note(on_step, notes, "bitmoji_account_home")
        return True
    return False


def _wait_bitmoji_create_page(context, *, tries: int = 45):
    """Poll for the Bitmoji create/gender page (it may open in a new tab) or an
    already-rendered builder frame, returning as soon as one is ready.

    The create page can be slow to appear after the OAuth handoff, so this keeps
    per-iteration waits modest (~400ms) and polls patiently instead of blocking
    on a single long sleep.
    """
    waiter = None
    for _ in range(max(1, tries)):
        create = _find_bitmoji_create_page(context)
        if create is not None:
            return create
        for candidate in _context_pages(context):
            if _builder_frame(candidate) is not None:
                return candidate
            waiter = candidate
        try:
            (waiter or context.pages[0]).wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    return _find_bitmoji_create_page(context)


def _click_bitmoji_gender(
    page, notes: list[str], on_step, *, gender: str = "female", tries: int = 40
) -> bool:
    """Wait for the male/female selection buttons to be present & visible, then
    click the requested gender. This is the exact screen the flow can get stuck
    on, so it polls patiently and retries the click a few times before giving up.
    """
    if _builder_frame(page) is not None:
        return True
    g = normalize_gender(gender)
    label = "Male Avatar" if g == "male" else "Female Avatar"
    tag = "bitmoji_male" if g == "male" else "bitmoji_female"

    def _gender_btn():
        return _first_visible_locator(
            [
                page.locator(f'button[aria-label="{label}"]'),
                page.get_by_role("button", name=label, exact=True),
            ]
        )

    btn = None
    for _ in range(max(1, tries)):
        if _builder_frame(page) is not None:
            return True
        btn = _gender_btn()
        if btn is not None:
            break
        page.wait_for_timeout(400)
    if btn is None:
        _note(on_step, notes, "bitmoji_gender_not_seen")
        return False
    for attempt in range(1, 4):
        if _click_xy(page, btn) or _human_click(page, btn):
            _note(
                on_step,
                notes,
                tag if attempt == 1 else f"{tag}_try:{attempt}",
            )
            return True
        page.wait_for_timeout(400)
        btn = _gender_btn() or btn
    _note(on_step, notes, f"{tag}_click_failed")
    return False


def _random_male_skin_cell() -> tuple[int, int]:
    """Skin grid is 4 cols; ~52+ cells → ~13 rows. Pick a mid-range tone."""
    row = random.randint(4, 12)
    col = random.randint(1, 4)
    return row, col


def _random_male_hair_cell() -> tuple[int, int]:
    """Bias toward earlier rows (typically shorter / male-leaning styles)."""
    row = random.randint(1, 6)
    col = random.randint(1, 4)
    return row, col


def _snapchat_bitmoji(
    page, context, notes: list[str], on_step, *, gender: str = "female"
) -> bool:
    """Signup welcome → Bitmoji create (gender, skin, hair) → save → back to welcome."""
    g = normalize_gender(gender)
    try:
        _open_bitmoji_and_login(context, notes, on_step)
        _bitmoji_oauth_continues(context, notes, on_step)
        create = None
        frame = None
        gender_done = False
        # Patiently reach the builder after the OAuth handoff. The create/gender
        # page and the builder frame can be slow to render (and may open in a new
        # tab), so re-check the create page/tab, wait for the gender UI, and retry
        # a few passes before emitting bitmoji_builder_not_seen. Total budget is
        # ~60-90s of modest (~400ms) polling that returns as soon as it's ready.
        for wait_retry in range(1, 4):
            create = _wait_bitmoji_create_page(context, tries=45)
            if create is None:
                _note(on_step, notes, f"bitmoji_builder_wait_retry:{wait_retry}")
                page.wait_for_timeout(500)
                continue
            _bring_page(create)
            _pause(create, 400, 800)
            if not gender_done:
                gender_done = _click_bitmoji_gender(
                    create, notes, on_step, gender=g, tries=40
                )
            frame = _wait_builder_frame(create, tries=45)
            if frame is not None:
                break
            _note(on_step, notes, f"bitmoji_builder_wait_retry:{wait_retry}")
            page.wait_for_timeout(500)
        if create is None:
            _note(on_step, notes, "bitmoji_create_not_seen")
            return False
        if frame is None:
            _note(on_step, notes, "bitmoji_builder_not_seen")
            return False
        if not _wait_builder_grid(
            create, frame, "skin_tone", min_count=52, expect_cols=4, title_needle="skin"
        ):
            _note(on_step, notes, "bitmoji_skin_not_ready")
        if g == "male":
            skin_row, skin_col = _random_male_skin_cell()
        else:
            skin_row, skin_col = 9, 2
        skin_ok = False
        for attempt in range(1, 4):
            if _click_builder_cell(
                create, frame, "skin_tone", skin_row, skin_col, expect_cols=4
            ):
                _note(
                    on_step,
                    notes,
                    (
                        f"bitmoji_skin:{skin_row},{skin_col}"
                        if attempt == 1
                        else f"bitmoji_skin_try:{attempt}:{skin_row},{skin_col}"
                    ),
                )
                skin_ok = True
                break
            _note(on_step, notes, f"bitmoji_skin_still_here:{attempt}")
            if g == "male":
                skin_row, skin_col = _random_male_skin_cell()
            _wait_builder_grid(
                create, frame, "skin_tone", min_count=52, expect_cols=4, title_needle="skin", tries=12
            )
        if not skin_ok:
            _note(on_step, notes, "bitmoji_skin_not_clicked")
        _pause(create, 400, 800)
        if not _open_hairstyle_panel(create, frame):
            _note(on_step, notes, "bitmoji_hairstyle_not_opened")
            return False
        _note(on_step, notes, "bitmoji_hairstyle")
        if not _wait_builder_grid(
            create, frame, "hair", min_count=12, expect_cols=4, title_needle="hairstyle"
        ):
            _note(on_step, notes, "bitmoji_hair_not_ready")
        _pause(create, 400, 700)
        hair_ok = False
        if g == "male":
            for attempt in range(1, 5):
                hair_row, hair_col = _random_male_hair_cell()
                if _click_builder_cell(
                    create, frame, "hair", hair_row, hair_col, expect_cols=4
                ):
                    _note(on_step, notes, f"bitmoji_hair:{hair_row},{hair_col}")
                    hair_ok = True
                    break
                _note(on_step, notes, f"bitmoji_hair_try:{attempt}:{hair_row},{hair_col}")
                _pause(create, 200, 400)
            if not hair_ok:
                # Fall back to any early cell that usually exists.
                for hair_row, hair_col in ((1, 1), (1, 2), (2, 1), (2, 2), (3, 1)):
                    if _click_builder_cell(
                        create, frame, "hair", hair_row, hair_col, expect_cols=4
                    ):
                        _note(on_step, notes, f"bitmoji_hair_fallback:{hair_row},{hair_col}")
                        hair_ok = True
                        break
            if not hair_ok:
                _note(on_step, notes, "bitmoji_hair_not_clicked")
        else:
            if _click_builder_cell(create, frame, "hair", 3, 4, expect_cols=4):
                _note(on_step, notes, "bitmoji_hair:3,4")
            else:
                _note(on_step, notes, "bitmoji_hair_not_clicked")
        _pause(create, 500, 900)
        if not _save_bitmoji_avatar(create, frame, context, notes, on_step):
            _note(on_step, notes, "bitmoji_save_failed")
            return False
        welcome = _welcome_tab(context, page)
        _bring_page(welcome)
        _note(on_step, notes, "bitmoji_done")
        return True
    except Exception as exc:
        _note(on_step, notes, f"bitmoji_failed:{exc}")
        return False


def _snapchat_bitmoji_placeholder(
    page, context, notes: list[str], on_step, *, gender: str = "female"
) -> bool:
    return _snapchat_bitmoji(page, context, notes, on_step, gender=gender)


def _viewport_width(page) -> float:
    try:
        size = page.viewport_size or {}
        width = float(size.get("width") or 0)
        if width:
            return width
    except Exception:
        pass
    try:
        return float(page.evaluate("() => window.innerWidth") or 0)
    except Exception:
        return 1280.0


def _add_friends_search_box(page):
    """Add Friends overlay search.

    Small AdsPower fingerprints shrink Snap web, so this overlay sits on the
    left (SMS-0017 was x=145). Do not require a wide-window x>280 cutoff.
    Prefer the rightmost visible Search so a far-left inbox bar loses.
    """
    best = None
    best_x = -1.0
    for sel in (
        'input[placeholder="Search..."]',
        'input[placeholder="Search"]',
        'input[aria-label*="Search" i]',
    ):
        loc = page.locator(sel)
        try:
            count = loc.count()
        except Exception:
            continue
        for i in range(count):
            item = loc.nth(i)
            try:
                if not item.is_visible():
                    continue
                box = item.bounding_box()
            except Exception:
                continue
            if not box:
                continue
            x = float(box.get("x") or 0)
            if x >= best_x:
                best = item
                best_x = x
    return best


def _add_friends_panel_open(page) -> bool:
    blob = _body_text(page).lower()
    if "new friend requests" in blob or "friend requests will appear" in blob:
        return True
    return "add friends" in blob and _add_friends_search_box(page) is not None


def _open_add_friends_panel(page) -> bool:
    loc = _first_visible_locator(
        [
            page.locator('button[title="View friend requests"]'),
            page.locator('button[title*="friend request" i]'),
            page.locator('button[title*="Add Friend" i]'),
        ]
    )
    clicked = bool(loc is not None and _human_click(page, loc))
    if not clicked:
        # Tiny fingerprints collapse the chat header; the button exists at 0×0.
        try:
            clicked = bool(
                page.evaluate(
                    """() => {
                      const btn = document.querySelector(
                        'button[title="View friend requests"], button[title*="friend request" i], button[title*="Add Friend" i]'
                      );
                      if (!btn) return false;
                      btn.click();
                      return true;
                    }"""
                )
            )
        except Exception:
            clicked = False
    if not clicked:
        return False
    _pause(page, 600, 1100)
    for _ in range(20):
        if _add_friends_panel_open(page):
            return True
        page.wait_for_timeout(300)
    return _add_friends_panel_open(page)


def _type_add_friends_query(page, query: str) -> bool:
    search = _add_friends_search_box(page)
    if search is None:
        return False
    if not _click_xy(page, search):
        _human_click(page, search)
    try:
        search.click(click_count=3, timeout=1500)
        search.press("Backspace")
    except Exception:
        pass
    try:
        search.press_sequentially(query, delay=random.randint(70, 140), timeout=15000)
    except Exception:
        try:
            search.fill(query, timeout=8000)
        except Exception:
            return False
    return True


def _add_visible_web_friends(page, remaining: int) -> int:
    added = 0
    for _ in range(20):
        adds = page.get_by_role("button", name="Add", exact=True)
        try:
            if adds.count():
                break
        except Exception:
            pass
        page.wait_for_timeout(350)
    for _ in range(max(0, remaining)):
        _pause(page, 350, 700)
        adds = page.get_by_role("button", name="Add", exact=True)
        cands = []
        try:
            count = adds.count()
        except Exception:
            count = 0
        min_x = min(350.0, max(90.0, _viewport_width(page) * 0.16))
        for i in range(count):
            btn = adds.nth(i)
            try:
                box = btn.bounding_box()
            except Exception:
                box = None
            if box and 50 < box.get("y", 0) < 900 and box.get("x", 0) > min_x:
                cands.append(box)
        if not cands:
            break
        box = random.choice(cands)
        try:
            page.mouse.move(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
                steps=random.randint(6, 12),
            )
            _pause(page, 70, 150)
            page.mouse.click(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
                delay=random.randint(40, 90),
            )
            added += 1
        except Exception:
            continue
    return added


def _add_random_web_friends(page, notes: list[str], on_step) -> None:
    target = random.randint(2, 4)
    added = 0
    used: list[str] = []
    for attempt in range(1, 4):
        if added >= target:
            break
        if not _add_friends_panel_open(page):
            if attempt > 1:
                _note(on_step, notes, "web_add_friends_retry")
            if not _open_add_friends_panel(page):
                if attempt == 1:
                    _note(on_step, notes, "web_add_friends_not_found")
                    return
                _note(on_step, notes, "web_add_friends_panel_closed")
                continue
            _note(on_step, notes, "web_add_friends_open")
        elif attempt > 1:
            _note(on_step, notes, "web_add_friends_retry")
        if _add_friends_search_box(page) is None:
            _note(on_step, notes, "web_add_friends_search_not_in_panel")
            continue
        pool = [name for name in USA_BOY_NAMES if name not in used] or list(USA_BOY_NAMES)
        query = random.choice(pool)
        used.append(query)
        if not _type_add_friends_query(page, query):
            if not _add_friends_panel_open(page):
                _note(on_step, notes, "web_add_friends_panel_closed")
            else:
                _note(on_step, notes, "web_add_friends_search_type_failed")
            continue
        _note(on_step, notes, f"web_add_friends_search:{query}")
        added += _add_visible_web_friends(page, target - added)
        if added < target and not _add_friends_panel_open(page):
            _note(on_step, notes, "web_add_friends_panel_closed")
    if added:
        _note(on_step, notes, f"web_add_friends_added:{added}")
    else:
        _note(on_step, notes, "web_add_friends_add_failed")
    _pause(page, 400, 800)


def _detect_add_result(page, query: str) -> str:
    """Best-effort classification after searching a username."""
    blob = _body_text(page).lower()
    q = (query or "").lower()
    if any(
        phrase in blob
        for phrase in (
            "no results",
            "couldn't find",
            "could not find",
            "user not found",
            "doesn't exist",
            "does not exist",
            "no users found",
        )
    ):
        return "Not Found"
    if any(
        phrase in blob
        for phrase in (
            "added already",
            "already added",
            "already friends",
            "pending",
            "requested",
        )
    ) and q and q in blob:
        # Weak signal — prefer Added Already only when clearly locked.
        if "already" in blob:
            return "Added Already"
    return "Added"


def _add_snapx_web_friends(
    page, usernames: list[str], notes: list[str], on_step
) -> list[dict[str, Any]]:
    """Search + add each SnapX username; return per-username outcomes."""
    results: list[dict[str, Any]] = []
    if not usernames:
        _note(on_step, notes, "web_add_friends_empty_list")
        return results

    for query in usernames:
        query = str(query or "").strip()
        if not query:
            continue
        if not _add_friends_panel_open(page):
            if not _open_add_friends_panel(page):
                _note(on_step, notes, "web_add_friends_not_found")
                results.append({"username": query, "status": "Not Found", "added": 0})
                continue
            _note(on_step, notes, "web_add_friends_open")
        if _add_friends_search_box(page) is None:
            _note(on_step, notes, "web_add_friends_search_not_in_panel")
            results.append({"username": query, "status": "Not Found", "added": 0})
            continue
        if not _type_add_friends_query(page, query):
            _note(on_step, notes, f"web_add_friends_search_type_failed:{query}")
            results.append({"username": query, "status": "Not Found", "added": 0})
            continue
        _note(on_step, notes, f"web_add_friends_search:{query}")
        page.wait_for_timeout(random.randint(700, 1200))
        added = _add_visible_web_friends(page, 1)
        if added:
            status = "Added"
            _note(on_step, notes, f"web_add_friends_added:{query}")
        else:
            status = _detect_add_result(page, query)
            _note(on_step, notes, f"web_add_friends_{status.lower().replace(' ', '_')}:{query}")
        results.append({"username": query, "status": status, "added": added})
        _pause(page, 400, 800)
    return results


def _flag_account_logged_out(page, notes: list[str], on_step, kind: str) -> None:
    try:
        auth_url = page.url or ""
    except Exception:
        auth_url = ""
    try:
        _bring_page(page)
    except Exception:
        pass
    _note(on_step, notes, f"account_logged_out:{kind}")
    if auth_url:
        _note(on_step, notes, f"account_logged_out_url:{auth_url[:180]}")


def _still_on_auth_after_welcome(
    page, context, notes: list[str], on_step, *, tries: int = 28
) -> tuple[Any, str] | tuple[None, None]:
    """AdsPower often reopens on signup even when cookies are still logged in.

    URL flash alone is not logout. Signup/login *form* visible = logout now.
    Otherwise navigate to welcome and wait; stuck auth URL after settle = logout.
    """
    form_kind = _auth_form_logout_kind(page)
    if form_kind:
        _note(on_step, notes, f"qa_auth_form_visible:{form_kind}")
        return page, form_kind

    auth_kind = _accounts_auth_kind(getattr(page, "url", None) or "")
    if auth_kind:
        _note(on_step, notes, f"qa_auth_page_seen:{auth_kind}")

    if not _on_welcome_page(page) and not _is_snapchat_web(page):
        try:
            _note(on_step, notes, "qa_goto_welcome")
            page.goto(
                _with_english_locale(SNAPCHAT_WELCOME_URL),
                wait_until="domcontentloaded",
                timeout=45000,
            )
            page.wait_for_timeout(random.randint(1800, 2800))
        except Exception as exc:
            _note(on_step, notes, f"qa_goto_welcome_failed:{exc}")
        page = _welcome_tab(context, page)
        _bring_page(page)

    for i in range(1, tries + 1):
        if _is_snapchat_web(page) or _on_welcome_page(page):
            return None, None
        web = _find_snapchat_web_tab(context, list(context.pages), page)
        if web is not None:
            return None, None
        # Form painted mid-wait → definite logout (don't wait out the flash timer).
        form_kind = _auth_form_logout_kind(page)
        if form_kind:
            _note(on_step, notes, f"qa_auth_form_visible:{form_kind}")
            return page, form_kind
        if i in (1, 10, 20):
            kind = _accounts_auth_kind(getattr(page, "url", None) or "") or "…"
            _note(on_step, notes, f"qa_waiting_session_try:{i}:{kind}")
        try:
            page.wait_for_timeout(500)
        except Exception:
            break

    form_kind = _auth_form_logout_kind(page)
    if form_kind:
        _note(on_step, notes, f"qa_auth_form_visible:{form_kind}")
        return page, form_kind

    # Settled on auth URL with no welcome/web (form may still be loading).
    auth_page, auth_kind = _find_logged_out_tab(context, [page])
    if auth_page is not None and auth_kind:
        return auth_page, auth_kind
    return None, None


def _snapchat_qa_add(
    page,
    context,
    notes: list[str],
    on_step,
    friend_usernames: list[str],
    *,
    web_session_url: str = "",
):
    """Open the profile's Snapchat Web session URL, then add claimed SnapX usernames.

    Basis for Logout/Dead: navigate to remark `https://www.snapchat.com/web/<uuid>`.
    If Snapchat redirects to accounts login/signup (or paints that form) → logged out.
    """
    web_session_url = str(web_session_url or "").strip()

    # 1) Prefer an already-open Snapchat Web tab (ignore leftover signup tabs).
    web = _find_snapchat_web_tab(context, list(context.pages), page)
    if web is not None:
        _bring_page(web)
        _note(on_step, notes, "qa_reuse_snapchat_web_tab")
    elif web_session_url:
        # Primary basis: deep-link from AdsPower remark / startup tab.
        _bring_page(page)
        try:
            _note(on_step, notes, f"qa_goto_web_session:{web_session_url[:120]}")
            page.goto(web_session_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(random.randint(2000, 3500))
        except Exception as exc:
            _note(on_step, notes, f"qa_goto_web_session_failed:{exc}")

        for i in range(1, 30):
            form_kind = _auth_form_logout_kind(page)
            if form_kind:
                _note(on_step, notes, f"qa_auth_form_visible:{form_kind}")
                _flag_account_logged_out(page, notes, on_step, form_kind)
                return page, []
            try:
                cur = page.url or ""
            except Exception:
                cur = ""
            url_kind = _accounts_auth_kind(cur)
            if url_kind:
                # Redirected off /web/<uuid> onto accounts login/signup.
                _note(on_step, notes, f"qa_web_session_redirect:{url_kind}")
                _flag_account_logged_out(page, notes, on_step, url_kind)
                return page, []
            if _is_snapchat_web(page):
                web = page
                _note(on_step, notes, "qa_web_session_ok")
                break
            found = _find_snapchat_web_tab(context, list(context.pages), page)
            if found is not None:
                web = found
                _bring_page(web)
                _note(on_step, notes, "qa_web_session_tab_found")
                break
            if i in (1, 10, 20):
                _note(on_step, notes, f"qa_waiting_web_session_try:{i}")
            try:
                page.wait_for_timeout(500)
            except Exception:
                break

        if web is None:
            # Still not on web after session URL — treat stuck auth URL as logout,
            # otherwise fall back to welcome path.
            try:
                cur = page.url or ""
            except Exception:
                cur = ""
            url_kind = _accounts_auth_kind(cur) or _auth_form_logout_kind(page)
            if url_kind:
                _flag_account_logged_out(page, notes, on_step, url_kind)
                return page, []
            _note(on_step, notes, "qa_web_session_miss_fallback_welcome")
            page = _welcome_tab(context, page)
            _bring_page(page)
            logged_out, auth_kind = _still_on_auth_after_welcome(page, context, notes, on_step)
            if logged_out is not None and auth_kind:
                _flag_account_logged_out(logged_out, notes, on_step, auth_kind)
                return logged_out, []
            page = _welcome_tab(context, page)
            if _is_snapchat_web(page):
                web = page
            elif _on_welcome_page(page):
                web = _open_web_from_welcome(page, context, notes, on_step)
            else:
                web = _snapchat_web_onboard(page, context, notes, on_step, add_friends=False) or page
    else:
        # No /web/<uuid> in remark — older welcome path.
        form_kind = _auth_form_logout_kind(page)
        if form_kind:
            _note(on_step, notes, f"qa_auth_form_visible:{form_kind}")
            _flag_account_logged_out(page, notes, on_step, form_kind)
            return page, []

        page = _welcome_tab(context, page)
        _bring_page(page)

        logged_out, auth_kind = _still_on_auth_after_welcome(page, context, notes, on_step)
        if logged_out is not None and auth_kind:
            _flag_account_logged_out(logged_out, notes, on_step, auth_kind)
            return logged_out, []

        page = _welcome_tab(context, page)
        _bring_page(page)

        if _is_snapchat_web(page):
            web = page
            _note(on_step, notes, "qa_already_on_web")
        elif _on_welcome_page(page):
            _note(on_step, notes, "qa_welcome_visible")
            _pause(page, 600, 1100)
            web = _open_web_from_welcome(page, context, notes, on_step)
        else:
            web = _find_snapchat_web_tab(context, list(context.pages), page)
            if web is None:
                _note(on_step, notes, "qa_fallback_web_onboard")
                web = _snapchat_web_onboard(page, context, notes, on_step, add_friends=False) or page

    _bring_page(web)

    focus_kind = _auth_form_logout_kind(web)
    if focus_kind:
        _flag_account_logged_out(web, notes, on_step, focus_kind)
        return web, []
    try:
        focus_url = web.url or ""
    except Exception:
        focus_url = ""
    url_kind = _accounts_auth_kind(focus_url)
    if url_kind and not _is_snapchat_web(web) and not _on_welcome_page(web):
        _flag_account_logged_out(web, notes, on_step, url_kind)
        return web, []

    if not _wait_snapchat_web(web, tries=50):
        focus_kind = _auth_form_logout_kind(web)
        if focus_kind:
            _flag_account_logged_out(web, notes, on_step, focus_kind)
            return web, []
        try:
            focus_url = web.url or ""
        except Exception:
            focus_url = ""
        url_kind = _accounts_auth_kind(focus_url)
        if url_kind:
            _flag_account_logged_out(web, notes, on_step, url_kind)
            return web, []
        logged_out, auth_kind = _still_on_auth_after_welcome(web, context, notes, on_step, tries=12)
        if logged_out is not None and auth_kind:
            _flag_account_logged_out(logged_out, notes, on_step, auth_kind)
            return logged_out, []
        _note(on_step, notes, "web_not_ready_for_qa")
        return web, []

    _pause(web, 700, 1300)
    _dismiss_web_welcome(web, notes, on_step)
    if _web_has_text(web, "Welcome to Snapchat for Web"):
        _note(on_step, notes, "qa_web_welcome_retry")
        _dismiss_web_welcome(web, notes, on_step)
    _skip_best_friends(web, notes, on_step)
    _dismiss_heads_up(web, notes, on_step)
    _note(on_step, notes, "qa_ready_to_add")
    friend_results = _add_snapx_web_friends(web, friend_usernames, notes, on_step)
    _note(on_step, notes, "web_add_friends_settle")
    _pause(web, 800, 1400)
    return web, friend_results


def _snapchat_web_onboard(page, context, notes: list[str], on_step, *, add_friends: bool = True):
    """Welcome Chat → Snapchat for Web onboard → inbox glance → add friends."""
    page = _welcome_tab(context, page)
    # Resume-after-proxy-merge: AdsPower reopens with the previous session tabs,
    # so we often land back on the signup page. The account is already created
    # and logged in (cookies persist in the profile), so just navigate to the
    # welcome page and continue from there instead of bailing out.
    if not _on_welcome_page(page) and not _is_snapchat_web(page):
        try:
            _note(on_step, notes, "web_onboard_goto_welcome")
            page.goto(_with_english_locale(SNAPCHAT_WELCOME_URL), wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(random.randint(1500, 2500))
        except Exception as exc:
            _note(on_step, notes, f"web_onboard_goto_welcome_failed:{exc}")
        page = _welcome_tab(context, page)
    if not _on_welcome_page(page) and not _is_snapchat_web(page):
        _note(on_step, notes, "web_onboard_not_on_welcome")
        return page
    _pause(page, 500, 1000)
    web = _open_web_from_welcome(page, context, notes, on_step)
    if not _wait_snapchat_web(web):
        _note(on_step, notes, "web_onboard_web_tab_not_seen")
        return web
    _pause(web, 600, 1200)
    _dismiss_web_welcome(web, notes, on_step)
    if _web_has_text(web, "Welcome to Snapchat for Web"):
        _note(on_step, notes, "web_onboard_next_retry")
        _dismiss_web_welcome(web, notes, on_step)
    _skip_best_friends(web, notes, on_step)
    _dismiss_heads_up(web, notes, on_step)
    _browse_web_inbox(web, notes, on_step)
    if add_friends:
        _add_random_web_friends(web, notes, on_step)
        _note(on_step, notes, "web_add_friends_settle")
        try:
            web.wait_for_timeout(5000)
        except Exception:
            time.sleep(5)
        _note(on_step, notes, "close_profile")
    return web


def run_page_action(
    puppeteer_ws: str,
    *,
    action: str = "snapchat_signup",
    start_url: str = "",
    username: str = "",
    password: str = "",
    first_name: str = "",
    last_name: str = "",
    birth_year: int | None = None,
    birth_month: int | None = None,
    birth_day: int | None = None,
    dwell_seconds: float = 6,
    screenshot_path: str | Path | None = None,
    email: str = "",
    phone_number: str = "",
    otp_waiter: Callable[[], str | None] | None = None,
    email_provider: Callable[[], dict[str, Any] | None] | None = None,
    phone_provider: Callable[[], dict[str, Any] | None] | None = None,
    friend_usernames: list[str] | None = None,
    on_step: Callable[[str], None] | None = None,
    until: str = "",
    web_session_url: str = "",
    bitmoji_gender: str = "female",
) -> dict[str, Any]:
    """Attach to an AdsPower browser over CDP and type the Snapchat signup like a person."""
    from playwright.sync_api import sync_playwright

    # Normalize CDP endpoint — AdsPower sometimes nests the URL under ws.puppeteer.
    if isinstance(puppeteer_ws, dict):
        puppeteer_ws = (
            puppeteer_ws.get("puppeteer")
            or puppeteer_ws.get("puppeteer_ws")
            or ""
        )
    puppeteer_ws = str(puppeteer_ws or "").strip()
    if not puppeteer_ws:
        raise RuntimeError("CDP endpoint missing (expected puppeteer ws string)")

    action = (action or "snapchat_signup").lower()
    gender = normalize_gender(bitmoji_gender)
    if not start_url:
        start_url = SNAPCHAT_LOGIN_URL if action == "snapchat_login" else SNAPCHAT_SIGNUP_URL
    identity = build_identity(username=username, password=password, gender=gender)
    if first_name:
        identity["first_name"] = first_name
    identity["last_name"] = ""
    phone_number = (phone_number or "").strip()
    email = (email or "").strip()
    if birth_year:
        identity["birth_year"] = int(birth_year)
    if birth_month:
        identity["birth_month"] = int(birth_month)
        identity["birth_month_label"] = month_label(identity["birth_month"])
    if birth_day:
        identity["birth_day"] = int(birth_day)
    filled: list[str] = []
    notes: list[str] = []
    friend_results: list[dict[str, Any]] = []
    session_url = str(web_session_url or "").strip()
    if not session_url and action in {"snapchat_qa_add", "snapchat_warmup"}:
        # Allow callers to pass the remark deep-link via start_url.
        from app.services.profiles import extract_snap_web_url

        session_url = extract_snap_web_url(start_url) or ""

    playwright = sync_playwright().start()
    browser = None
    try:
        browser = playwright.chromium.connect_over_cdp(puppeteer_ws)
        context = browser.contexts[0] if browser.contexts else None
        if context is None:
            raise RuntimeError("AdsPower browser has no context yet. Retry open.")
        page = context.pages[0] if context.pages else context.new_page()
        if action == "snapchat_qa_add":
            page, friend_results = _snapchat_qa_add(
                page,
                context,
                notes,
                on_step,
                list(friend_usernames or []),
                web_session_url=session_url,
            )
        elif action == "snapchat_warmup":
            page, friend_results = _snapchat_qa_add(
                page,
                context,
                notes,
                on_step,
                [],
                web_session_url=session_url,
            )
            logged_out = any(str(n).startswith("account_logged_out") for n in notes)
            if not logged_out:
                _add_random_web_friends(page, notes, on_step)
                _note(on_step, notes, "web_add_friends_settle")
                _pause(page, 800, 1400)
        elif action == "snapchat_web_onboard":
            page = _welcome_tab(context, page, identity.get("username") or "")
            _bring_page(page)
            web_page = _snapchat_web_onboard(page, context, notes, on_step)
            if web_page is not None:
                page = web_page
        else:
            page.goto(_with_english_locale(start_url), wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(random.randint(1200, 2200))
            _force_english(page, start_url, notes)
            _dismiss_cookie_banner(page, notes, on_step)
            for _ck in range(4):
                try:
                    loc = page.locator("#firstname, input[name='firstName']").first
                    if loc.count() and loc.is_visible():
                        break
                except Exception:
                    pass
                if not _dismiss_cookie_banner(page, notes, on_step):
                    page.wait_for_timeout(400)
                    continue
                page.wait_for_timeout(500)

            if action in {"snapchat_signup", "snapchat_login"}:
                try:
                    page.locator("#firstname, input[name='firstName']").first.wait_for(
                        state="visible", timeout=15000
                    )
                except Exception:
                    pass
                _dismiss_cookie_banner(page, notes, on_step)
                _type_first(page, FIRST_NAME_SELECTORS, identity["first_name"], "first_name", filled)
                _clear_last_name(page, notes)

                if action == "snapchat_signup":
                    if not _fill_birthday(page, identity, filled, notes):
                        _note(on_step, notes, "birthday_not_fully_filled")

                # Preserve the pre-generated unique handle as the fallback, then
                # (for signup) deliberately type a decoy first — the bare first
                # name is almost always taken on Snapchat. When it bounces we
                # fall back to the real handle like a person would. Skipped ~20%
                # of the time so it isn't a perfect pattern.
                real_username = identity["username"]
                pending_real = False
                if action == "snapchat_signup":
                    decoy = decoy_username(
                        identity["first_name"],
                        avoid={real_username},
                        gender=gender,
                    )
                    if decoy and decoy != real_username and random.random() < 0.8:
                        identity["username"] = decoy
                        pending_real = True
                        _note(on_step, notes, f"username_decoy:{decoy}")

                _fill_username(page, identity, filled, notes, on_step)
                _type_first(page, PASSWORD_SELECTORS, identity["password"], "password", filled)

                if action == "snapchat_signup":
                    if email and not phone_number:
                        _use_email_instead(page, notes, on_step)
                    if email and not phone_number and _first_visible(page, EMAIL_SELECTORS) is not None:
                        _type_first(page, EMAIL_SELECTORS, email, "email", filled)
                    if phone_number:
                        _use_phone_instead(page, notes, on_step)
                        if _phone_step_visible(page):
                            _fill_phone_number(page, phone_number, filled, notes, on_step)

                    if not _ensure_fields(page, identity, filled, notes, on_step):
                        missing = sorted(
                            {"first_name", "username", "password", "birth_month", "birth_day", "birth_year"}
                            - set(filled)
                        )
                        _note(on_step, notes, f"blocked_next_missing:{missing}")
                    else:
                        submitted = False
                        used_names: set[str] = {identity["username"], real_username}
                        for attempt in range(7):
                            if not _wait_username_ready(page, identity["username"], notes):
                                _note(on_step, notes, "username_short_before_next")
                                _fill_username(page, identity, filled, notes, on_step)
                                _ensure_fields(page, identity, filled, notes, on_step)
                            before = len(notes)
                            if not _click_next(
                                page, notes, on_step, expected_username=identity["username"]
                            ):
                                recent = notes[before:]
                                if "process_error_stuck" in recent:
                                    break
                                if any(
                                    item in recent
                                    for item in (
                                        "blocked_next_username_short",
                                        "blocked_next_username_hint",
                                        "username_short_after_next",
                                    )
                                ):
                                    _fill_username(page, identity, filled, notes, on_step)
                                    _ensure_fields(page, identity, filled, notes, on_step)
                                    continue
                                _note(on_step, notes, "next_button_not_found")
                                break
                            page.wait_for_timeout(random.randint(900, 1600))
                            if "process_error_stuck" in notes:
                                break
                            if _username_too_short(page) or _username_short_message(page):
                                _note(on_step, notes, "username_short_after_next")
                                _fill_username(page, identity, filled, notes, on_step)
                                _ensure_fields(page, identity, filled, notes, on_step)
                                continue
                            if _username_taken(page):
                                if pending_real:
                                    nxt = real_username
                                    pending_real = False
                                else:
                                    make_user = (
                                        boyish_username if gender == "male" else girly_username
                                    )
                                    nxt = make_user(
                                        identity["first_name"],
                                        identity["birth_year"],
                                        avoid=used_names,
                                    )
                                used_names.add(nxt)
                                old = identity["username"]
                                identity["username"] = nxt
                                _note(on_step, notes, f"username_taken_after_next:{old}->{nxt}")
                                _fill_username(page, identity, filled, notes, on_step)
                                _ensure_fields(page, identity, filled, notes, on_step)
                                continue
                            submitted = True
                            break
                        if not submitted:
                            if "process_error_stuck" not in notes:
                                _note(on_step, notes, "signup_not_submitted")
                        else:
                            page.wait_for_timeout(random.randint(1500, 2500))
                            stage = _wait_for_stage(page, "any", tries=40)
                            try:
                                blob = " ".join((page.inner_text("body") or "").split())[:180]
                            except Exception:
                                blob = ""
                            _note(
                                on_step,
                                notes,
                                f"after_submit_stage:{stage or 'none'} {(page.url or '')[:90]} {blob}",
                            )
                            if (not stage) and _cookie_wall_visible(page):
                                if _dismiss_cookie_banner(page, notes, on_step):
                                    stage = _wait_for_stage(page, "any", tries=24)
                            if phone_number and stage != "otp":
                                if stage == "email" or _first_visible(page, EMAIL_SELECTORS) is not None:
                                    if _use_phone_instead(page, notes, on_step):
                                        stage = "phone"
                                for _wait_phone in range(16):
                                    now = _email_or_otp_visible(page)
                                    if now == "otp":
                                        stage = "otp"
                                        break
                                    if now == "phone" or _phone_step_visible(page):
                                        stage = "phone"
                                        break
                                    if now == "email" or _first_visible(page, EMAIL_SELECTORS) is not None:
                                        if _use_phone_instead(page, notes, on_step):
                                            stage = "phone"
                                            break
                                    page.wait_for_timeout(1200)
                                if stage != "otp" and stage != "phone":
                                    _note(on_step, notes, "phone_step_not_seen")
                                for _phone_try in range(8):
                                    if _email_or_otp_visible(page) == "otp":
                                        stage = "otp"
                                        break
                                    if not _phone_step_visible(page):
                                        break
                                    already_rejected = _phone_rejected(page) and "phone" in filled
                                    if not already_rejected:
                                        if not _fill_phone_number(page, phone_number, filled, notes, on_step):
                                            _note(on_step, notes, "phone_field_not_typed")
                                            break
                                        if not _click_next(page, notes, on_step):
                                            if "process_error_stuck" in notes:
                                                submitted = False
                                                break
                                        page.wait_for_timeout(random.randint(900, 1600))
                                        for _rej_wait in range(10):
                                            if _email_or_otp_visible(page) == "otp":
                                                stage = "otp"
                                                break
                                            if _phone_rejected(page):
                                                break
                                            page.wait_for_timeout(400)
                                    if stage == "otp":
                                        break
                                    if not (already_rejected or _phone_rejected(page)):
                                        stage = _wait_for_stage(page, "otp", tries=12)
                                        if stage == "otp":
                                            break
                                    if already_rejected or _phone_rejected(page):
                                        _note(on_step, notes, "phone_rejected")
                                        if not phone_provider:
                                            break
                                        fresh = None
                                        try:
                                            fresh = phone_provider()
                                        except Exception as exc:
                                            _note(on_step, notes, f"phone_reorder_failed:{exc}")
                                            break
                                        if not (fresh and (fresh.get("phone_number") or fresh.get("phone"))):
                                            _note(on_step, notes, "phone_reorder_none")
                                            break
                                        phone_number = str(fresh.get("phone_number") or fresh.get("phone") or "")
                                        if fresh.get("otp_waiter"):
                                            otp_waiter = fresh["otp_waiter"]
                                        _note(on_step, notes, f"phone_reordered:{phone_number}")
                                        if "phone" in filled:
                                            filled.remove("phone")
                                        continue
                                    if _phone_step_visible(page):
                                        continue
                                    break
                            elif email and stage != "otp":
                                for _uei in range(4):
                                    if _use_email_instead(page, notes, on_step):
                                        stage = "email"
                                        break
                                    if not _phone_step_visible(page):
                                        break
                                    page.wait_for_timeout(600)

                            if email and not phone_number and stage != "otp":
                                for _email_try in range(4):
                                    # Confirm we're on an email field; if a phone
                                    # screen is (still) showing, click the link.
                                    if _first_visible(page, EMAIL_SELECTORS) is None:
                                        if not _use_email_instead(page, notes, on_step):
                                            break
                                    # Force a fresh (re)type of the email.
                                    if "email" in filled:
                                        filled.remove("email")
                                    if not _type_first(page, EMAIL_SELECTORS, email, "email", filled):
                                        break
                                    if not _click_next(page, notes, on_step):
                                        if "process_error_stuck" in notes:
                                            submitted = False
                                            break
                                    page.wait_for_timeout(random.randint(900, 1600))
                                    stage = _wait_for_stage(page, "otp")
                                    if stage == "otp":
                                        break
                                    # Address already used on another account →
                                    # rent/reorder a fresh email and try again.
                                    if _email_taken(page):
                                        _note(on_step, notes, "email_already_used")
                                        if not email_provider:
                                            break
                                        fresh = None
                                        try:
                                            fresh = email_provider()
                                        except Exception as exc:
                                            _note(on_step, notes, f"email_reorder_failed:{exc}")
                                            break
                                        if not (fresh and fresh.get("email")):
                                            _note(on_step, notes, "email_reorder_none")
                                            break
                                        email = fresh["email"]
                                        if fresh.get("otp_waiter"):
                                            otp_waiter = fresh["otp_waiter"]
                                        _note(on_step, notes, f"email_reordered:{email}")
                                        if "email" in filled:
                                            filled.remove("email")
                                        _clear_email_input(page)
                                        continue
                                    # Provider rejected the address entirely → stop.
                                    if _email_rejected(page):
                                        break
                                    # Phone screen came back → loop clicks the link
                                    # again on the next pass. Otherwise give up.
                                    if _first_visible(page, EMAIL_SELECTORS) is None:
                                        continue
                                    break

                            # If we typed an email but Snapchat still refuses it
                            # (unsupported provider / rejected address), flag it
                            # so the run auto-deletes this profile.
                            if (
                                email
                                and "email" in filled
                                and "process_error_stuck" not in notes
                                and stage != "otp"
                                and _email_rejected(page)
                            ):
                                _note(on_step, notes, "email_rejected")

                            if (
                                "process_error_stuck" not in notes
                                and "email_rejected" not in notes
                                and stage != "otp"
                                and not (phone_number and stage not in {"phone", "otp"})
                            ):
                                stage = _wait_for_stage(page, "otp", tries=20)

                            if "email_rejected" not in notes and stage != "otp" and email and "email" in filled and _email_rejected(page):
                                _note(on_step, notes, "email_rejected")

                            if "process_error_stuck" in notes or "email_rejected" in notes:
                                pass
                            elif stage == "otp":
                                _note(on_step, notes, "otp_field_visible")
                                code = ""
                                if otp_waiter:
                                    try:
                                        code = otp_waiter() or ""
                                    except Exception as exc:
                                        _note(on_step, notes, f"imap_otp_failed:{exc}")
                                if code:
                                    if _type_otp(page, code, filled):
                                        _note(on_step, notes, "typed_otp")
                                        if not _click_next(page, notes, on_step):
                                            pass
                                    else:
                                        _note(on_step, notes, "otp_field_not_typed")
                                elif not otp_waiter:
                                    _note(on_step, notes, "waiting_for_otp_no_imap")
                            elif email and "email" not in filled:
                                _note(on_step, notes, "email_step_not_seen")
                            else:
                                _note(on_step, notes, "otp_step_not_seen")

                            otp_done = "typed_otp" in notes
                            if "process_error_stuck" in notes:
                                pass
                            elif _wait_for_welcome(
                                page,
                                notes,
                                on_step,
                                identity.get("username") or "",
                                tries=40 if otp_done else 8,
                            ):
                                # signup → bitmoji → Snapchat-for-Web onboard (Chat now only after Bitmoji)
                                if _snapchat_bitmoji_placeholder(
                                    page, context, notes, on_step, gender=gender
                                ):
                                    if until == "bitmoji":
                                        _note(on_step, notes, "proxy_merge_ready")
                                    else:
                                        web_page = _snapchat_web_onboard(page, context, notes, on_step)
                                        if web_page is not None:
                                            page = web_page
                                else:
                                    _note(on_step, notes, "web_onboard_waiting_for_bitmoji")
                            else:
                                _note(on_step, notes, "welcome_page_not_seen")

        body = (page.inner_text("body") or "").lower()
        if any(word in body for word in ("captcha", "verify you", "unusual", "challenge")):
            notes.append("challenge_or_captcha_visible")

        if screenshot_path and until != "bitmoji":
            path = Path(screenshot_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(path), full_page=False, timeout=8000)
            except Exception as exc:
                notes.append(f"screenshot_failed:{exc}")

        # A signup that reached the OTP step but never completed (code never
        # typed, or the welcome page never appeared) is a dead account → delete.
        signup_failed = action == "snapchat_signup" and (
            "welcome_page_not_seen" in notes
            or ("otp_field_visible" in notes and "typed_otp" not in notes)
        )
        delete_profile = (
            "process_error_stuck" in notes
            or "email_rejected" in notes
            or ("phone_rejected" in notes and "typed_otp" not in notes)
            or signup_failed
        )
        close_profile = "close_profile" in notes or delete_profile
        if dwell_seconds > 0 and not close_profile:
            time.sleep(dwell_seconds)

        try:
            page_url = page.url
            page_title = page.title()
        except Exception:
            page_url = ""
            page_title = ""

        logged_out = any(
            str(n).startswith("account_logged_out:") for n in notes
        )
        return {
            "ok": not delete_profile and not logged_out,
            "action": action,
            "filled": filled,
            "notes": notes,
            "friend_results": friend_results,
            "logged_out": logged_out,
            "close_profile": close_profile,
            "delete_profile": delete_profile,
            "url": page_url,
            "title": page_title,
            "first_name": identity["first_name"],
            "last_name": identity.get("last_name") or "",
            "username": identity["username"],
            "password": identity["password"],
                            "email": email,
                            "phone_number": phone_number,
            "birth_year": identity["birth_year"],
            "birth_month": identity["birth_month"],
            "birth_day": identity["birth_day"],
            "screenshot": str(screenshot_path) if screenshot_path else None,
        }
    finally:
        # Do not browser.close() — AdsPower quits the profile when CDP drops.
        if browser is not None:
            try:
                browser._impl_obj._should_close_connection_on_close = False
            except Exception:
                pass
        try:
            playwright.stop()
        except Exception:
            pass
