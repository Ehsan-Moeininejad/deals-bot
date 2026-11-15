"""Personal rules: model, compact link format, text command parsing and matching.

A rule is a small dict, stored only in the private state repo:
    {"id": 3, "cats": [0, 5], "pmin": 0, "pmax": 20000000, "dmin": 30,
     "amin": 2000000, "rmin": 4.0, "vmin": 20, "bin": ["سامسونگ"], "bout": [],
     "mode": "i"}
    cats   positions in settings.CATEGORIES (empty = all categories)
    pmin / pmax   price range in Toman (0 = no limit)
    dmin   minimum discount percent
    amin   minimum discount amount in Toman
    rmin / vmin   minimum rating (out of 5) and minimum number of votes
    bin / bout    brand names to include / exclude (substring match)
    mode   "i" = with every run (instant), "d" = once a day at DAILY_HOUR
    smin   minimum seller rating (out of 5)
    ship   delivery: any of "j" (fast same-day), "n" (3-hour nearby), "w" (in Digikala stock)
    flags  all of "o" (official seller), "l" (lowest price of the last 30 days)
Every field is optional; an empty rule matches every offer.

Compact form (used in t.me/<bot>?start=<payload> and in /add):
    c05_d30_a20_p0-200_r40_v20_md
    c   category positions in base36, one character each
    d   min discount %          a   min discount amount, in units of 100,000 Toman
    p   price range min-max, in units of 100,000 Toman (max 0 = no limit)
    r   min rating x10          v   min votes          m   i or d
    s   min seller rating x10   f   delivery letters (j n w)   q   flag letters (o l)
Only [A-Za-z0-9_-] is used and it stays under Telegram's 64-character limit.
Brands are not in the link (Persian text is not allowed there); /brand adds them.
"""
from __future__ import annotations

import json
import re

from . import settings as S

UNIT = 100_000  # Toman per unit in the compact form
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

EMPTY = {"cats": [], "pmin": 0, "pmax": 0, "dmin": 0, "amin": 0,
         "rmin": 0.0, "vmin": 0, "bin": [], "bout": [], "mode": "i",
         "smin": 0.0, "ship": "", "flags": ""}
SHIP = {"j": "ارسال سریع امروز", "n": "ارسال ۳ ساعته", "w": "موجود در انبار دیجی‌کالا"}
FLAGS = {"o": "فروشنده‌ی رسمی", "l": "کمترین قیمت ۳۰ روز"}


class RuleError(ValueError):
    pass


def parse_compact(text: str) -> dict:
    """Compact text -> rule dict (without id). Raises RuleError on bad input."""
    text = (text or "").strip()
    if not text or len(text) > 64 or not re.fullmatch(r"[A-Za-z0-9_\-]+", text):
        raise RuleError("format")
    rule = {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY.items()}
    for tok in text.split("_"):
        key, val = tok[:1], tok[1:]
        try:
            if key == "c":
                cats = sorted({B36.index(ch) for ch in val.lower()})
                if any(c >= len(S.CATEGORIES) for c in cats):
                    raise RuleError("category")
                rule["cats"] = cats
            elif key == "d":
                rule["dmin"] = max(0, min(99, int(val)))
            elif key == "a":
                rule["amin"] = max(0, int(val)) * UNIT
            elif key == "p":
                lo, hi = val.split("-")
                rule["pmin"], rule["pmax"] = int(lo or 0) * UNIT, int(hi or 0) * UNIT
            elif key == "r":
                rule["rmin"] = max(0, min(50, int(val))) / 10
            elif key == "v":
                rule["vmin"] = max(0, int(val))
            elif key == "s":
                rule["smin"] = max(0, min(50, int(val))) / 10
            elif key == "f":
                if not val or any(ch not in SHIP for ch in val):
                    raise RuleError("ship")
                rule["ship"] = "".join(sorted(set(val)))
            elif key == "q":
                if not val or any(ch not in FLAGS for ch in val):
                    raise RuleError("flags")
                rule["flags"] = "".join(sorted(set(val)))
            elif key == "m":
                if val not in ("i", "d"):
                    raise RuleError("mode")
                rule["mode"] = val
            else:
                raise RuleError("field")
        except (ValueError, IndexError) as exc:
            if isinstance(exc, RuleError):
                raise
            raise RuleError("value") from exc
    if rule["pmax"] and rule["pmin"] > rule["pmax"]:
        raise RuleError("price")
    return rule


def to_compact(rule: dict) -> str:
    parts = []
    if rule["cats"]:
        parts.append("c" + "".join(B36[c] for c in rule["cats"]))
    if rule["dmin"]:
        parts.append(f"d{rule['dmin']}")
    if rule["amin"]:
        parts.append(f"a{rule['amin'] // UNIT}")
    if rule["pmin"] or rule["pmax"]:
        parts.append(f"p{rule['pmin'] // UNIT}-{rule['pmax'] // UNIT}")
    if rule["rmin"]:
        parts.append(f"r{round(rule['rmin'] * 10)}")
    if rule["vmin"]:
        parts.append(f"v{rule['vmin']}")
    if rule.get("smin"):
        parts.append(f"s{round(rule['smin'] * 10)}")
    if rule.get("ship"):
        parts.append(f"f{rule['ship']}")
    if rule.get("flags"):
        parts.append(f"q{rule['flags']}")
    parts.append(f"m{rule['mode']}")
    return "_".join(parts)


def from_json(raw: str) -> dict:
    """Rule sent by the Telegram Mini App (web_app_data). Same limits as the compact form,
    plus brand lists, which the 64-character link cannot carry."""
    try:
        d = json.loads(raw)
        rule = parse_compact(str(d.get("code", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, RuleError):
            raise
        raise RuleError("json") from exc

    def names(key):
        vals = d.get(key) or []
        if not isinstance(vals, list):
            raise RuleError("brands")
        return [str(v).strip()[:30] for v in vals if str(v).strip()][:10]

    rule["bin"], rule["bout"] = names("bin"), names("bout")
    return rule


def _toman(n: int) -> str:
    if n >= 1_000_000 and n % 100_000 == 0:
        v = n / 1_000_000
        return f"{v:g} میلیون"
    return f"{n:,}"


def describe(rule: dict) -> str:
    """One readable Persian line, for /rules and confirmations."""
    bits = []
    bits.append("، ".join(S.CATEGORIES[c] for c in rule["cats"]) if rule["cats"] else "همه‌ی دسته‌ها")
    if rule["dmin"]:
        bits.append(f"تخفیف ≥ {rule['dmin']}%")
    if rule["amin"]:
        bits.append(f"مبلغ تخفیف ≥ {_toman(rule['amin'])} تومان")
    if rule["pmin"] and rule["pmax"]:
        bits.append(f"قیمت {_toman(rule['pmin'])} تا {_toman(rule['pmax'])} تومان")
    elif rule["pmax"]:
        bits.append(f"قیمت تا {_toman(rule['pmax'])} تومان")
    elif rule["pmin"]:
        bits.append(f"قیمت از {_toman(rule['pmin'])} تومان")
    if rule["rmin"]:
        bits.append(f"امتیاز ≥ {rule['rmin']:g}")
    if rule["vmin"]:
        bits.append(f"رأی ≥ {rule['vmin']}")
    if rule["bin"]:
        bits.append("برند: " + "، ".join(rule["bin"]))
    if rule["bout"]:
        bits.append("به‌جز: " + "، ".join(rule["bout"]))
    if rule.get("smin"):
        bits.append(f"امتیاز فروشنده ≥ {rule['smin']:g}")
    if rule.get("ship"):
        bits.append(" یا ".join(SHIP[c] for c in rule["ship"]))
    for c in rule.get("flags", ""):
        bits.append(FLAGS[c])
    bits.append("ارسال روزانه" if rule["mode"] == "d" else "ارسال فوری")
    return " | ".join(bits)


def matches(rule: dict, o: dict) -> bool:
    """o is an offer from scoring.enrich()."""
    if rule["cats"]:
        names = {S.CATEGORIES[c] for c in rule["cats"] if c < len(S.CATEGORIES)}
        if o.get("cat1") not in names:
            return False
    price = o["price"] or 0
    if rule["pmin"] and price < rule["pmin"]:
        return False
    if rule["pmax"] and price > rule["pmax"]:
        return False
    if o["discount"] < rule["dmin"]:
        return False
    if rule["amin"] and o.get("saving", 0) < rule["amin"]:
        return False
    if rule["rmin"] and (o.get("rating") or 0) < rule["rmin"]:
        return False
    if o.get("votes", 0) < rule["vmin"]:
        return False
    if rule.get("smin") and (o.get("seller_stars") or 0) < rule["smin"]:
        return False
    ship = rule.get("ship") or ""
    if ship and not (("j" in ship and o.get("jet")) or ("n" in ship and o.get("nearby"))
                     or ("w" in ship and o.get("dk_stock"))):
        return False
    flags = rule.get("flags") or ""
    if "o" in flags and not o.get("official"):
        return False
    if "l" in flags and not o.get("lowest_30d"):
        return False
    brand = o.get("brand") or ""
    if rule["bin"] and not any(b in brand for b in rule["bin"]):
        return False
    if rule["bout"] and any(b in brand for b in rule["bout"]):
        return False
    return True


def match_user(rules: list[dict], offers: list[dict], mode: str) -> list[dict]:
    """Offers (already sorted best-first) matching any active rule of this mode."""
    active = [r for r in rules if r.get("mode", "i") == mode]
    if not active:
        return []
    return [o for o in offers if any(matches(r, o) for r in active)]
