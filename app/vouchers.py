"""Digikala voucher center: fetch public voucher codes and announce new or changed ones.

Source: the public voucher-center landing (no login), the same data the page
https://www.digikala.com/landing/voucher-center/ shows. Runs inside the bot job,
once per hour after minute VOUCHER_MINUTE. Writes only vouchers_*.json, which no
other job writes.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime

import requests

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
                "url": f"https://www.digikala.com{uri}" if uri.startswith("/") else PAGE_URL,
            })
    return out


def fresh(vouchers: list[dict], seen: dict) -> list[dict]:
    """New vouchers, or ones whose terms changed."""
    return [v for v in vouchers if v["key"] not in seen or seen[v["key"]].get("d") != v["desc"]]


def _line(v: dict) -> str:
    code = (f"<code>{html.escape(v['code'], quote=False)}</code>" if v["code"]
            else "کد را از صفحه‌ی کالاها بگیر")
    return (f"▫️ <b>{html.escape(v['title'], quote=False)}</b> — {html.escape(v['desc'], quote=False)}\n"
            f"‏   🎟 {code} · <a href=\"{html.escape(v['url'], quote=True)}\">مشاهده‌ی کالاها</a>")


def render(vouchers: list[dict], heading: str) -> list[str]:
    """Grouped by the page's own sections. Codes are tap-to-copy (<code>)."""
    groups: dict[str, list[dict]] = {}
    for v in vouchers:
        groups.setdefault(v["group"] or "سایر", []).append(v)
    blocks = [f"🏷 <b>{html.escape(g, quote=False)}</b>\n" + "\n".join(_line(v) for v in items)
              for g, items in groups.items()]
    footer = "👆 روی کد بزن تا کپی شود · منبع: وچر سنتر دیجی‌کالا"
    messages, cur = [], heading
    for b in blocks + [footer]:
        if len(cur) + len(b) + 2 > S.MESSAGE_LIMIT:
            messages.append(cur)
            cur = "<b>ادامه‌ی کدها 👇</b>"
        cur += "\n\n" + b
    messages.append(cur)
    return messages


CHANNEL_MARKUP = {"inline_keyboard": [[{"text": "🎟 همه‌ی کدهای تخفیف", "url": PAGE_URL}]]}


def due(now: datetime) -> bool:
    """Once per hour, after minute VOUCHER_MINUTE, outside quiet hours."""
    if now.hour in S.QUIET_HOURS or now.minute < S.VOUCHER_MINUTE:
        return False
    return state.read("vouchers_slot.json", {}).get("slot") != now.strftime("%Y-%m-%dT%H")


def run(tg: Telegram, users: dict, now: datetime | None = None) -> int:
    """Check the voucher center and post new codes. Returns the number of new vouchers."""
    now = now or datetime.now(S.TZ)
    if not due(now):
        return 0
    vouchers = fetch()
    if vouchers is None:
        return 0  # try again on the next bot run
    state.write("vouchers_latest.json", vouchers)
    seen = state.read("vouchers_seen.json", {})
    new = fresh(vouchers, seen)
    state.write("vouchers_slot.json", {"slot": now.strftime("%Y-%m-%dT%H")})
    if not new:
        log.info("vouchers: %s on the page, none new", len(vouchers))
        return 0

    heading = (f"🎟 <b>{len(new)} کد تخفیف تازه در دیجی‌کالا</b>\n"
               f"‏🕒 {now:%H:%M}" + ("" if S.LIVE else " · 🧪 پیش‌نمایش"))
    destination = S.CHANNEL_ID if S.LIVE else S.ADMIN_CHAT_ID
    try:
        msgs = render(new, heading)
        tg.send_all(destination, msgs, CHANNEL_MARKUP)
    except TelegramError as exc:
        log.warning("voucher post failed (%s)", exc.code)
        return 0

    # people who asked for codes in private (users.json is read-only here)
    for cid, u in users.items():
        if u.get("codes") and not u.get("paused"):
            try:
                tg.send_all(cid, render(new, f"🎟 <b>{len(new)} کد تخفیف تازه</b>"), CHANNEL_MARKUP)
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
