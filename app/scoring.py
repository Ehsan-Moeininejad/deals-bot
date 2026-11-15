"""Score and filter offers. Pure functions, no network, easy to test.

HONEST NOTE ON "REAL DISCOUNT"
The incredible-offers list only shows prices while a product is on offer, so a
history built from it cannot tell us the normal shelf price. What it CAN catch is
an inflated "before" price: if today's rrp is far above the product's own past
rrp, we use the past rrp as the baseline instead. Offers with too little history
are marked unverified and get a lower weight.
"""
from __future__ import annotations

import math
import statistics

from . import settings as S


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def rating_confidence(rating: float, votes: int) -> float:
    """4.8 with 3 votes must score lower than 4.4 with 2000 votes."""
    return (rating / 5) * (1 - math.exp(-votes / 50))


def urgency(offer: dict, now_ts: float) -> float:
    end = offer.get("ends_at")
    if end:
        left = end - now_ts
        if left <= 0:
            return 0.0
        if left <= 3 * 3600:
            return 1.0
        if left <= 12 * 3600:
            return 0.6
        if left <= 24 * 3600:
            return 0.3
        return 0.0
    return 0.5 if offer.get("lightning") else 0.0


def baseline(offer: dict, hist: list, today: int) -> tuple[float, bool]:
    """Price the discount is measured against, and whether we could verify it."""
    prior = [r[2] for r in hist if r[0] < today and r[2]]
    if len(prior) >= S.HISTORY_MIN_DAYS:
        return min(offer["rrp"], statistics.median(prior)), True
    return float(offer["rrp"]), False


def enrich(offer: dict, hist: list, today: int, now_ts: float) -> dict | None:
    """Score any valid live offer, WITHOUT the channel's quality thresholds.

    Personal rules use this, so a user may ask for e.g. 3.5 stars or 10% off.
    Returns None only for broken, sold-out or expired offers.
    """
    price, rrp = offer["price"], offer["rrp"]
    if not price or not rrp or rrp <= price:
        return None
    if offer["status"] and offer["status"] != "marketable":
        return None
    if offer["ends_at"] and offer["ends_at"] <= now_ts:
        return None
    base, verified = baseline(offer, hist, today)
    discount = 1 - price / base
    if discount <= 0:
        return None
    d = _clip(discount, 0, S.MAX_DISCOUNT) / S.MAX_DISCOUNT
    if not verified:
        d *= S.COLD_START_FACTOR
    score = (
        S.W_DISCOUNT * d
        + S.W_RATING * rating_confidence(offer["rating"] or 0, offer["votes"])
        + S.W_URGENCY * urgency(offer, now_ts)
    )
    out = dict(offer)
    out.update(score=score, discount=round(discount * 100), verified=verified,
               saving=max(0, round(base - price)))
    return out


def attractiveness(offer: dict) -> int:
    """The score as a 0-100 percentage, shown to readers as «جذابیت»."""
    return round(_clip(offer["score"], 0, 1) * 100)


def evaluate(offer: dict, hist: list, today: int, now_ts: float) -> dict | None:
    """Channel filter: enrich() plus the quality thresholds in settings."""
    if offer["rating"] is None or offer["rating"] < S.MIN_RATING or offer["votes"] < S.MIN_VOTES:
        return None
    e = enrich(offer, hist, today, now_ts)
    if e is None or e["discount"] < S.MIN_DISCOUNT:
        return None
    return e


def select(offers: list[dict], history: dict, today: int, now_ts: float, channel: bool = True) -> list[dict]:
    """Channel list (channel=True) or every valid offer for personal rules."""
    fn = evaluate if channel else enrich
    scored = []
    for o in offers:
        e = fn(o, history.get(str(o["dkp"]), []), today, now_ts)
        if e:
            scored.append(e)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored
