"""Digikala voucher center: fetch public voucher codes and announce new or changed ones.

Source: the public voucher-center landing (no login), the same data the page
https://www.digikala.com/landing/voucher-center/ shows. Runs inside the bot job,
once per hour after minute VOUCHER_MINUTE. Writes only vouchers_*.json, which no
other job writes.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from . import digest, nudge
from . import settings as S
from . import state
from .telegram import Telegram, TelegramError

log = logging.getLogger("vouchers")

PAGE_URL = "https://www.digikala.com/landing/voucher-center/"


def fetch() -> list[dict] | None:
    """All vouchers on the page, or None if the request failed."""
    try:
        r = requests.get(S.VOUCHER_API, timeout=S.REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        widgets = (r.json().get("data") or {}).get("widgets") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("voucher fetch failed: %s", exc)
        return None
    return parse(widgets)


def parse(widgets: list[dict]) -> list[dict]:
    out, seen = [], set()
    for w in widgets:
        if w.get("widget_type") != "vouchers":
            continue
        group = " ".join(((w.get("data") or {}).get("title") or "").split())
        for v in (w.get("data") or {}).get("vouchers") or []:
            key = v.get("code") or v.get("voucher_id")
            if not key or key in seen:
                continue
            seen.add(key)
            uri = (v.get("url") or {}).get("uri") or ""
            out.append({
                "key": key,
                "group": group,
                "title": " ".join((v.get("title") or "").split()),
                "desc": " ".join((v.get("description") or "").split()),
                "code": v.get("code") or "",  # empty for "serial" vouchers (code given on the site)
                "url": clean_url(f"https://www.digikala.com{uri}") if uri.startswith("/") else PAGE_URL,
            })
    return out


TRACKING = re.compile(r"^(utm_.*|camcode|cb|gclid|fbclid|ref|source)$", re.I)


def clean_url(url: str) -> str:
    """Plain product-list link: tracking parameters removed, sorting kept."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


# ---- readable terms ---------------------------------------------------------------------------

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٪", "0123456789%")


def _num(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text) or 0)


def terms(desc: str) -> tuple[str, str]:
    """(value badge, condition) from the page's free-text description.
    «۳۰٪ تخفیف تا سقف ۱۰۰,۰۰۰ تومان» -> ("30%", "تا سقف 100 هزار تومان")"""
    d = " ".join(desc.translate(FA_DIGITS).split())
    m = re.search(r"(\d+)\s*%\s*تخفیف\s*تا\s*سقف\s*([\d,]+)\s*(میلیون)?", d)
    if m:
        cap = _num(m.group(2)) * (1_000_000 if m.group(3) else 1)
        return f"{m.group(1)}%", f"تا سقف {nudge.short_amount(cap)} تومان"
    m = re.search(r"([\d,]+)\s*(?:تومان\s*)?تخفیف\s*برای\s*خرید\s*بالای\s*([\d,]+)\s*(میلیون)?", d)
    if m:
        low = _num(m.group(2)) * (1_000_000 if m.group(3) else 1)
        return f"{nudge.short_amount(_num(m.group(1)))} تومان", f"برای خرید بالای {nudge.short_amount(low)}"
    m = re.search(r"(\d+)\s*%\s*تخفیف\s*برای\s*خرید\s*بالای\s*([\d,]+)\s*(میلیون)?", d)
    if m:
        low = _num(m.group(2)) * (1_000_000 if m.group(3) else 1)
        return f"{m.group(1)}%", f"برای خرید بالای {nudge.short_amount(low)}"
    if "ارسال" in d and "رایگان" in d:
        return "ارسال رایگان", ""
    return "", d


def fresh(vouchers: list[dict], seen: dict) -> list[dict]:
    """New vouchers, or ones whose terms changed."""
    return [v for v in vouchers if v["key"] not in seen or seen[v["key"]].get("d") != v["desc"]]


RLM = "\u200f"


def _group_name(g: str) -> str:
    g = re.sub(r"^(کد\s*[\u200c ]?های\s*تخفیف|کد\s*تخفیف[\u200c ]?های|کدهای\s*تخفیف)\s*", "", g or "")
    return g.strip(" -–") or "سایر"


def card(v: dict, expired: bool = False) -> str:
    """Two lines: the value first (what you get), then the code (tap to copy) and a link."""
    value, cond = terms(v["desc"])
    head = ((f"<b>{html.escape(value, quote=False)}</b>" if value else "")
            + (f" {html.escape(cond, quote=False)}" if cond else "")).strip()
    head = head or html.escape(v["desc"], quote=False)
    title = html.escape(v["title"], quote=False)
    if expired:
        return f"⌛️ <s>{head}</s> · تمام شد\n{RLM}<s>🛍 {title}</s>"
    code = f"<code>{html.escape(v['code'], quote=False)}</code>" if v["code"] else "کد در صفحه‌ی کالاها"
    link = f'<a href="{html.escape(v["url"], quote=True)}">خرید</a>'
    return f"🎟 {head}\n{RLM}🛍 {title} · {code} · {link}"


def render(vouchers: list[dict], heading: str, expired: set | None = None,
           footer_line: str = "") -> list[str]:
    """Grouped by the page's sections: 3 codes per group visible, the rest collapsed.
    Codes are tap-to-copy (<code>). Expired codes stay, struck through."""
    expired = expired or set()
    groups: dict[str, list[dict]] = {}
    for v in vouchers:
        groups.setdefault(_group_name(v["group"]), []).append(v)
    blocks = []
    for g, items in groups.items():
        lines = [f"{digest.emoji_for(g)} <b>{html.escape(g, quote=False)}</b>"]
        lines += [card(v, v["key"] in expired) for v in items[:3]]
        if items[3:]:
            lines.append(f"<blockquote expandable>➕ {len(items) - 3} کد دیگر\n"
                         + "\n".join(card(v, v["key"] in expired) for v in items[3:]) + "</blockquote>")
        blocks.append("\n".join(lines))
    footer = "👆 روی کد بزن تا کپی شود، موقع پرداخت وارد کن."
    if footer_line:
        footer = f"<i>{html.escape(footer_line, quote=False)}</i>\n" + footer
    messages, cur = [], heading
    for blk in blocks + [footer]:
        if digest.vlen(cur) + digest.vlen(blk) + 2 > S.MESSAGE_LIMIT:
            messages.append(cur)
            cur = "<b>ادامه‌ی کدها 👇</b>"
        cur += "\n\n" + blk
    messages.append(cur)
    return messages


def channel_markup(bot_name: str = "") -> dict:
    row = [{"text": "🎟 همه‌ی کدها", "url": PAGE_URL}]
    if bot_name:
        row.append({"text": "🔔 کدهای تازه در پی‌وی", "url": f"https://t.me/{bot_name}?start=codes"})
    return {"inline_keyboard": [row]}


CHANNEL_MARKUP = channel_markup()


def due(now: datetime) -> bool:
    """Once per hour, after minute VOUCHER_MINUTE, outside quiet hours."""
    if now.hour in S.QUIET_HOURS or now.minute < S.VOUCHER_MINUTE:
        return False
    return state.read("vouchers_slot.json", {}).get("slot") != now.strftime("%Y-%m-%dT%H")


def mark_expired(tg: Telegram, live_keys: set) -> int:
    """Strike through codes that left the page, in posts of the last 48 hours."""
    posts = state.read("vouchers_posts.json", [])
    now_ts, edited = int(time.time()), 0
    for p in posts:
        gone = {v["key"] for v in p["items"]} - live_keys - set(p.get("exp", []))
        if not gone:
            continue
        p["exp"] = sorted(set(p.get("exp", [])) | gone)
        text = render(p["items"], p["heading"], set(p["exp"]), p.get("line", ""))[0]
        try:
            tg.call("editMessageText", {"chat_id": p["chat"], "message_id": p["id"], "text": text,
                                        "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
                                        "reply_markup": p.get("markup") or CHANNEL_MARKUP})
            edited += 1
        except TelegramError:
            pass
    posts = [p for p in posts if now_ts - p["ts"] < 48 * 3600]
    state.write("vouchers_posts.json", posts)
    return edited


def _post(tg: Telegram, chat: str, items: list[dict], heading: str, line: str, markup: dict) -> None:
    """Send; remember single-message posts so expired codes can be struck through later."""
    msgs = render(items, heading, None, line)
    ids = tg.send_all(chat, msgs, markup)
    if len(msgs) == 1 and ids:
        posts = state.read("vouchers_posts.json", [])
        posts.append({"chat": chat, "id": ids[0], "items": items, "heading": heading, "line": line,
                      "ts": int(time.time()), "exp": [], "markup": markup})
        state.write("vouchers_posts.json", posts)


def run(tg: Telegram, users: dict, now: datetime | None = None, bot_name: str = "") -> int:
    """Check the voucher center and post new codes. Returns the number of new vouchers."""
    now = now or datetime.now(S.TZ)
    if not due(now):
        return 0
    vouchers = fetch()
    if vouchers is None:
        return 0  # try again on the next bot run
    first = state.read("vouchers_seen.json", {})
    for v in vouchers:  # when each code first appeared (for «🆕» in /codes)
        v["since"] = first.get(v["key"], {}).get("t", int(time.time()))
    state.write("vouchers_latest.json", vouchers)
    try:
        mark_expired(tg, {v["key"] for v in vouchers})
    except Exception:  # noqa: BLE001
        log.exception("expiry marking failed")
    seen = state.read("vouchers_seen.json", {})
    new = fresh(vouchers, seen)
    state.write("vouchers_slot.json", {"slot": now.strftime("%Y-%m-%dT%H")})
    if not new:
        log.info("vouchers: %s on the page, none new", len(vouchers))
        return 0

    heading = (f"🎟 <b>{len(new)} کد تخفیف تازه</b> · دیجی‌کالا\n"
               f"{RLM}🕒 {now:%H:%M} · ✅ {len(vouchers)} کد فعال" + ("" if S.LIVE else " · 🧪 پیش‌نمایش"))
    destination = S.CHANNEL_ID if S.LIVE else S.ADMIN_CHAT_ID
    line = nudge.codes_line(new, now.hour)
    try:
        _post(tg, destination, new, heading, line, channel_markup(bot_name))
    except TelegramError as exc:
        log.warning("voucher post failed (%s)", exc.code)
        return 0

    # people who asked for codes in private (users.json is read-only here)
    for cid, u in users.items():
        if u.get("codes") and not u.get("paused"):
            try:
                tg.send_all(cid, render(new, f"🎟 <b>{len(new)} کد تخفیف تازه</b>", None, line),
                            channel_markup())
                time.sleep(S.SEND_DELAY / 3)
            except TelegramError:
                pass

    ts = int(time.time())
    for v in new:
        seen[v["key"]] = {"t": ts, "d": v["desc"]}
    live_keys = {v["key"] for v in vouchers}
    seen = {k: x for k, x in seen.items() if k in live_keys or ts - x["t"] < 7 * 86400}
    state.write("vouchers_seen.json", seen)
    log.info("vouchers: %s on the page, %s new posted", len(vouchers), len(new))
    return len(new)
