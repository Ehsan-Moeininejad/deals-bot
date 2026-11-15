"""Send each user the offers that match their personal rules (runs inside the digest job).

Reads users.json (written only by the bot job). Writes user_sent.json and
blocked.json (written only here), so the two jobs never edit the same file.
Logs print counts only.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from . import digest, nudge
from . import rules as R
from . import settings as S
from . import state
from .telegram import Telegram, TelegramError

log = logging.getLogger("delivery")


def is_fresh(prev: dict | None, o: dict, now_ts: float) -> bool:
    """Same rule as the channel: once per 24h unless the price dropped >= 5%."""
    if prev is None or now_ts - prev["t"] > S.RESEND_HOURS * 3600:
        return True
    return o["price"] <= prev["p"] * (1 - S.REPRICE_DROP)


def pick(user: dict, offers: list[dict], sent: dict, post_time: datetime, now_ts: float) -> list[dict]:
    rules = user.get("rules") or []
    found = R.match_user(rules, offers, "i")
    if post_time.hour == S.DAILY_HOUR:
        seen = {o["dkp"] for o in found}
        found += [o for o in R.match_user(rules, offers, "d") if o["dkp"] not in seen]
        found.sort(key=lambda o: o["score"], reverse=True)
    return [o for o in found if is_fresh(sent.get(str(o["dkp"])), o, now_ts)][: S.USER_MAX_ITEMS]


def render(items: list[dict], now: datetime) -> str:
    head = "آفر تازه" if len(items) == 1 else f"{len(items)} آفر تازه"
    lines = [f"🎯 <b>{head} مطابق شرط‌های تو</b>",
             f"<i>{nudge.rule_line(items[0], now.timestamp(), now.hour)}</i>", ""]
    lines += [digest.fmt_rich(i, o, now) for i, o in enumerate(items, 1)]
    lines.append(f"\n{digest.RLM}🔻 تخفیف · ⭐ امتیاز · 🔥 جذابیت")
    return "\n".join(lines)


PERSONAL_MARKUP = {"inline_keyboard": [[{"text": "📋 قانون‌های من", "callback_data": "l:"},
                                        {"text": "⏸ توقف ارسال", "callback_data": "z:"}]]}


def compact_offers(scored: list[dict]) -> list[dict]:
    keep = ("dkp", "title", "url", "price", "discount", "saving", "rating", "votes",
            "cat1", "brand", "score", "ends_at", "source",
            "seller_stars", "official", "jet", "nearby", "dk_stock", "lowest_30d",
            "sold_pct", "stock", "rrp", "image")
    return [{k: o.get(k) for k in keep} for o in scored[: S.LATEST_OFFERS_KEEP]]


def deliver(tg: Telegram, offers: list[dict], now: datetime, post_time: datetime) -> int:
    users: dict = state.read("users.json", {})
    sent_all: dict = state.read("user_sent.json", {})
    blocked: dict = state.read("blocked.json", {})
    now_ts = now.timestamp()
    today = now.date().toordinal()
    messages = 0

    for cid, user in users.items():
        if user.get("paused") or not user.get("rules"):
            continue
        if blocked.get(cid, 0) > user.get("u", 0):  # blocked after their last /start
            continue
        box = sent_all.setdefault(cid, {})
        day, count = box.get("_n", [today, 0])
        if day != today:
            day, count = today, 0
        if count >= S.USER_DAILY_MESSAGES:
            continue
        items = pick(user, offers, box, post_time, now_ts)
        if not items:
            continue
        try:
            tg.send(cid, render(items, now), PERSONAL_MARKUP)
        except TelegramError as exc:
            if exc.code == 403:
                blocked[cid] = int(now_ts)
            else:
                log.warning("personal send failed (%s)", exc.code)
            continue
        for o in items:
            box[str(o["dkp"])] = {"t": now_ts, "p": o["price"]}
        box["_n"] = [day, count + 1]
        messages += 1
        time.sleep(S.SEND_DELAY / 3)

    # prune: old entries and users that no longer exist
    for cid in list(sent_all):
        if cid not in users:
            del sent_all[cid]
            continue
        box = sent_all[cid]
        for key in list(box):
            if key != "_n" and now_ts - box[key]["t"] > 48 * 3600:
                del box[key]
    for cid in list(blocked):
        if cid not in users:
            del blocked[cid]

    state.write("user_sent.json", sent_all)
    state.write("blocked.json", blocked)
    log.info("personal: %s user(s), %s message(s)", len(users), messages)
    return messages
