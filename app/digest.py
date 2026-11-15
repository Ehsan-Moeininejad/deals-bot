"""Decide what is new, group it by category, and render Telegram HTML messages."""
from __future__ import annotations

import html
import re
from datetime import datetime

from . import settings as S
from .scoring import attractiveness

CATEGORY_EMOJI = [
    ("موبایل", "📱"), ("گوشی", "📱"), ("لپ", "💻"), ("کامپیوتر", "💻"),
    ("دیجیتال", "🔌"), ("لوازم خانگی", "🏠"), ("خانه", "🏠"), ("آشپزخانه", "🍳"),
    ("مد", "👗"), ("پوشاک", "👗"), ("کیف", "👜"), ("آرایشی", "💄"), ("زیبایی", "💄"),
    ("سلامت", "💊"), ("کتاب", "📚"), ("ورزش", "⚽"), ("خودرو", "🚗"),
    ("اسباب", "🧸"), ("کودک", "🧸"), ("سوپرمارکت", "🛒"), ("خوار", "🛒"),
]


SOURCE_LABELS = {src["name"]: src.get("label", "") for src in S.SOURCES}


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


# ---- item formats ------------------------------------------------------------------
# Design notes: the product name is the link (one clear tap target); the old price is
# struck through next to the new one, so the saving is visible at a glance; the rest
# sits on one quiet line. Rich format for the top items, one-line format for the rest
# (inside a collapsed quote, so the post stays short but nothing is hidden).

RLM = "\u200f"  # keeps lines that start with digits right-to-left


def short_title(title: str, limit: int = S.TITLE_MAX) -> str:
    title = " ".join((title or "").split())
    if len(title) <= limit:
        return title
    cut = title[:limit].rsplit(" ", 1)[0]
    return (cut if len(cut) > limit * 0.6 else title[:limit]).rstrip("،,-– ") + "…"


def _link(o: dict, limit: int = S.TITLE_MAX) -> str:
    return (f'<a href="{html.escape(o["url"], quote=True)}">'
            f'{html.escape(short_title(o["title"], limit), quote=False)}</a>')


def _ends(o: dict, now: datetime) -> str:
    if o.get("ends_at"):
        left = o["ends_at"] - now.timestamp()
        if 0 < left <= S.END_SHOW_HOURS * 3600:
            return f"⏰ تا {datetime.fromtimestamp(o['ends_at'], S.TZ):%H:%M}"
    return ""


def fmt_rich(i: int, o: dict, now: datetime) -> str:
    """Two lines: rank + name, then old → new price and the key facts."""
    facts = [f"<s>{o['rrp']:,}</s> ← <b>{o['price']:,}</b> تومان", f"🔻{o['discount']}%"]
    if o.get("rating"):
        facts.append(f"⭐{o['rating']:.1f}")
    facts.append(f"🔥{attractiveness(o)}")
    for extra in (_ends(o, now), "📉 افت قیمت" if o.get("repriced") else "",
                  html.escape(SOURCE_LABELS.get(o.get("source", ""), ""), quote=False)):
        if extra:
            facts.append(extra)
    return f"{i}. {_link(o)}\n{RLM}" + " · ".join(facts)


def fmt_compact(o: dict) -> str:
    """One line for the collapsed part of a category."""
    return f"{RLM}▫️ {_link(o, S.TITLE_MAX_COMPACT)} · 🔻{o['discount']}% · <b>{o['price']:,}</b>"


_fmt_item = fmt_rich  # older name, used by personal messages and tests


def _block(heading: str, items: list[dict], now: datetime) -> str:
    """Category block: the best RICH_PER_GROUP items in full, the rest collapsed."""
    lines = [heading]
    lines += [fmt_rich(n, o, now) for n, o in enumerate(items[: S.RICH_PER_GROUP], 1)]
    rest = items[S.RICH_PER_GROUP:]
    if rest:
        lines.append(f"<blockquote expandable>➕ {len(rest)} آفر دیگر در این دسته\n"
                     + "\n".join(fmt_compact(o) for o in rest) + "</blockquote>")
    return "\n".join(lines)


def hero(o: dict, post_time: datetime, now: datetime, line: str = "") -> str:
    """Caption for the photo post of the hour's best deal (Telegram caption limit: 1024)."""
    lines = [f"⚡️ <b>داغ‌ترین آفر ساعت {post_time:%H:%M}</b>", "",
             f"<b>{html.escape(short_title(o['title'], 90), quote=False)}</b>"]
    if line:
        lines.append(f"<i>{html.escape(line, quote=False)}</i>")
    lines += [
             f"{RLM}<s>{o['rrp']:,}</s> ← <b>{o['price']:,} تومان</b>"]
    facts = [f"🔻 {o['discount']}% تخفیف"]
    if o.get("saving"):
        facts.append(f"💰 {o['saving']:,} تومان صرفه‌جویی")
    lines.append(RLM + " · ".join(facts))
    second = []
    if o.get("rating"):
        second.append(f"⭐ {o['rating']:.1f}" + (f" ({o['votes']:,} رأی)" if o.get("votes") else ""))
    second.append(f"🔥 جذابیت {attractiveness(o)}%")
    if _ends(o, now):
        second.append(_ends(o, now))
    lines.append(RLM + " · ".join(second))
    return "\n".join(lines)


def hero_markup(o: dict) -> dict:
    return {"inline_keyboard": [[{"text": "🛒 مشاهده و خرید در دیجی‌کالا", "url": o["url"]}]]}


def share_url(target: str, text: str) -> str:
    from urllib.parse import quote
    return f"https://t.me/share/url?url={quote(target, safe='')}&text={quote(text, safe='')}"


def channel_markup(bot_name: str, share: bool = False) -> dict | None:
    row = [{"text": "🔥 همه‌ی شگفت‌انگیزها", "url": "https://www.digikala.com/incredible-offers/"}]
    if bot_name:
        row.insert(0, {"text": "🎯 آفر دلخواهم را بفرست", "url": f"https://t.me/{bot_name}?start=channel"})
    rows = [row]
    channel = S.CHANNEL_ID.lstrip("@")
    if share and channel and not channel.lstrip("-").isdigit():
        rows.append([{"text": "📤 برای دوستانت بفرست",
                      "url": share_url(f"https://t.me/{channel}",
                                       "هر ساعت بهترین آفرهای دیجی‌کالا، اینجا جمع می‌شود 👇")}])
    return {"inline_keyboard": rows}


_TAG = re.compile(r"<[^>]+>")


def vlen(text: str) -> int:
    """Length as Telegram counts it: visible text only (tags and link targets are
    free), in UTF-16 units (an emoji counts as 2)."""
    visible = html.unescape(_TAG.sub("", text))
    return len(visible.encode("utf-16-le")) // 2


def _hard_split(text: str, limit: int) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and vlen(cur) + vlen(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def urgency_badge(o: dict, now: datetime) -> str:
    """A short TRUE scarcity note: units left, share sold or end time."""
    if o.get("stock") and o["stock"] <= S.LOW_STOCK:
        return f"⚠️ فقط {o['stock']} عدد"
    if o.get("ends_at") and 0 < o["ends_at"] - now.timestamp() <= S.ENDING_SOON_HOURS * 3600:
        return f"⏰ تا {datetime.fromtimestamp(o['ends_at'], S.TZ):%H:%M}"
    if (o.get("sold_pct") or 0) >= S.MOSTLY_SOLD:
        return f"🔥 {o['sold_pct']}% فروش رفته"
    return ""


def last_chance(offers: list[dict], now: datetime) -> list[dict]:
    """Offers about to run out (by real stock / end time / share sold), best first."""
    return [o for o in offers if urgency_badge(o, now)][: S.LAST_CHANCE_MAX]


def render(groups, ongoing, post_time: datetime, now: datetime, stats: dict,
           urgent: list[dict] | None = None, line: str = "") -> list[str]:
    header = f"🛍 <b>شگفت‌انگیزهای تازه‌ی ساعت {post_time:%H:%M}</b>"
    if line:
        header += f"\n<i>{html.escape(line, quote=False)}</i>"
    header += f"\n{RLM}🆕 {stats['new']} آفر تازه از {stats['checked']:,} کالای بررسی‌شده"
    blocks = []
    if urgent:
        blocks.append("⏳ <b>رو به اتمام</b>\n" + "\n".join(
            f"{fmt_compact(o)} · {urgency_badge(o, now)}" for o in urgent))
    for name, items in groups:
        blocks.append(_block(f"{emoji_for(name)} <b>{html.escape(name, quote=False)}</b>", items, now))
    if ongoing:
        blocks.append("⏳ <b>همچنان برقرار</b>\n<blockquote expandable>"
                      + "\n".join(fmt_compact(o) for o in ongoing) + "</blockquote>")
    footer = (f"{RLM}🔻 تخفیف · ⭐ امتیاز کالا · 🔥 درصد جذابیت · ⏰ پایان آفر\n"
              f"🔄 {now:%H:%M} · قیمت‌ها ممکن است تغییر کند")

    limit = S.MESSAGE_LIMIT
    pieces = []
    for b in blocks + [footer]:
        pieces += _hard_split(b, limit) if vlen(b) > limit else [b]

    messages, cur = [], header
    for p in pieces:
        if vlen(cur) + vlen(p) + 2 > limit:
            messages.append(cur)
            cur = "<b>ادامه‌ی آفرها 👇</b>"
        cur += "\n\n" + p
    messages.append(cur)
    return messages
