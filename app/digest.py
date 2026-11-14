"""Decide what is new, group it by category, and render Telegram HTML messages."""
from __future__ import annotations

import html
from datetime import datetime

from . import settings as S

CATEGORY_EMOJI = [
    ("موبایل", "📱"), ("گوشی", "📱"), ("لپ", "💻"), ("کامپیوتر", "💻"),
    ("دیجیتال", "🔌"), ("لوازم خانگی", "🏠"), ("خانه", "🏠"), ("آشپزخانه", "🍳"),
    ("مد", "👗"), ("پوشاک", "👗"), ("کیف", "👜"), ("آرایشی", "💄"), ("زیبایی", "💄"),
    ("سلامت", "💊"), ("کتاب", "📚"), ("ورزش", "⚽"), ("خودرو", "🚗"),
    ("اسباب", "🧸"), ("کودک", "🧸"), ("سوپرمارکت", "🛒"), ("خوار", "🛒"),
]


def emoji_for(name: str) -> str:
    for key, e in CATEGORY_EMOJI:
        if key in name:
            return e
    return "🛍"


def split_new_ongoing(scored: list[dict], sent: dict, now_ts: float):
    window = S.RESEND_HOURS * 3600
    new, ongoing = [], []
    for o in scored:
        prev = sent.get(str(o["dkp"]))
        if prev is None or now_ts - prev["t"] > window:
            new.append(o)
        elif o["price"] <= prev["p"] * (1 - S.REPRICE_DROP):
            o = dict(o, repriced=True)
            new.append(o)
        else:
            ongoing.append(o)
    return new, ongoing[: S.MAX_ONGOING]


def group(new: list[dict]) -> list[tuple[str, list[dict]]]:
    buckets: dict[str, list[dict]] = {}
    for o in new:
        buckets.setdefault(o["cat1"] or "سایر", []).append(o)
    groups = []
    for name, items in buckets.items():
        items = items[: S.ITEMS_PER_GROUP]
        groups.append((name, items, sum(i["score"] for i in items)))
    groups.sort(key=lambda g: g[2], reverse=True)
    return [(n, items) for n, items, _ in groups[: S.MAX_GROUPS]]


def _fmt_item(i: int, o: dict, now: datetime) -> str:
    title = html.escape(o["title"][:70], quote=False)
    url = html.escape(o["url"], quote=True)
    parts = [f"−{o['discount']}%", f"{o['price']:,} تومان", f"⭐ {o['rating']:.1f}"]
    if o["ends_at"]:
        left = o["ends_at"] - now.timestamp()
        if 0 < left <= 24 * 3600:
            end = datetime.fromtimestamp(o["ends_at"], S.TZ)
            parts.append(f"⏰ {end:%H:%M}")
    if o.get("repriced"):
        parts.append("📉 افت قیمت")
    return f"{i}. <a href=\"{url}\">{title}</a>\n\u200f" + " | ".join(parts)


def _block(heading: str, items: list[dict], now: datetime) -> str:
    lines = [heading]
    lines += [_fmt_item(n, o, now) for n, o in enumerate(items, 1)]
    return "\n".join(lines)


def _hard_split(text: str, limit: int) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def render(groups, ongoing, post_time: datetime, now: datetime, stats: dict) -> list[str]:
    header = f"🔥 <b>بهترین آفرهای ساعت {post_time:%H:%M}</b>"
    blocks = []
    for name, items in groups:
        blocks.append(_block(f"{emoji_for(name)} <b>{html.escape(name, quote=False)}</b>", items, now))
    if ongoing:
        blocks.append(_block("⏳ <b>همچنان برقرار</b>", ongoing, now))
    footer = (
        f"🆕 جدید این ساعت: {stats['new']}  |  بررسی‌شده: {stats['checked']:,}\n"
        f"🔄 به‌روزرسانی {now:%H:%M} — قیمت‌ها ممکن است تغییر کنند. لینک‌ها مستقیم و بدون افیلیت است."
    )

    limit = S.MESSAGE_LIMIT
    pieces = []
    for b in blocks + [footer]:
        pieces += _hard_split(b, limit) if len(b) > limit else [b]

    messages, cur = [], header
    for p in pieces:
        if len(cur) + len(p) + 2 > limit:
            messages.append(cur)
            cur = "<b>ادامه…</b>"
        cur += "\n\n" + p
    messages.append(cur)
    return messages
