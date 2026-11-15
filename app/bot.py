"""Personal rules bot: reads new Telegram messages and button taps, updates users.json.

Runs as its own short job (python -m app.main bot), so the hourly digest is never
blocked. It only writes users.json and bot_offset.json; the digest never writes
those two files, so both jobs can push to the state repo without conflicts.

Privacy: only the numeric chat_id, the rules and two timestamps are stored.
No names or usernames. Logs print counts only, never chat ids or rules.
"""
from __future__ import annotations

import html
import json
import logging
import time

from . import rules as R
from . import settings as S
from . import state
from .telegram import Telegram, TelegramError

log = logging.getLogger("bot")

PRESETS = [
    ("📱 کالای دیجیتال، تخفیف ۳۰٪+", "c0_d30_r40_v20_mi"),
    ("🏠 لوازم خانگی برقی، تخفیف ۲۵٪+", "ca_d25_r40_v20_mi"),
    ("💄 آرایشی و بهداشتی، تخفیف ۴۰٪+", "c1_d40_r40_v20_mi"),
    ("🔥 همه‌ی دسته‌ها، تخفیف ۵۰٪+", "d50_r40_v50_mi"),
]

HELP = (
    "<b>راهنما</b>\n"
    "/rules قانون‌های من و حذف آن‌ها\n"
    "/add کد — افزودن قانون با کد صفحه‌ی ساخت قانون\n"
    "/brand شماره سامسونگ -متفرقه — برند شامل یا مستثنا برای یک قانون\n"
    "/pause و /resume — توقف و ادامه‌ی ارسال\n"
    "/export — دیدن همه‌ی داده‌ی من\n"
    "/delete_me — پاک‌کردن کامل داده‌ی من\n\n"
    "⏳ بات همیشه روشن نیست؛ جواب‌ها ممکن است چند دقیقه طول بکشد."
)


def builder_url(bot_username: str) -> str:
    return f"{S.PAGES_URL}?bot={bot_username}"


def app_keyboard(bot_username: str) -> dict:
    """Reply keyboard with a Mini App button: opens the rule builder inside Telegram
    and sends the full rule (brands included) straight back to the bot."""
    return {"keyboard": [[{"text": "🛠 ساخت قانون", "web_app": {"url": builder_url(bot_username)}}]],
            "resize_keyboard": True, "is_persistent": True}


def welcome_markup(bot_username: str) -> dict:
    rows = [[{"text": "🛠 ساخت قانون دلخواه", "url": builder_url(bot_username)}]]
    rows += [[{"text": t, "callback_data": f"p:{c}"}] for t, c in PRESETS]
    return {"inline_keyboard": rows}


def latest_offers() -> list[dict]:
    return state.read("latest_offers.json", [])


def preview(rule: dict) -> str:
    found = [o for o in latest_offers() if R.matches(rule, o)]
    if not found:
        return "فعلاً آفری با این شرایط نیست؛ به محض پیدا شدن برایت می‌فرستم."
    lines = [f"الان {len(found)} آفر با این شرایط هست. نمونه:"]
    for o in found[:3]:
        title = html.escape(o["title"][:60], quote=False)
        lines.append(f"• <a href=\"{html.escape(o['url'], quote=True)}\">{title}</a> — "
                     f"−{o['discount']}% | {o['price']:,} تومان")
    return "\n".join(lines)


class Bot:
    def __init__(self, tg: Telegram, username: str):
        self.tg = tg
        self.username = username
        self.users: dict = state.read("users.json", {})
        self.changed = False
        self.sent = 0

    # ---- storage helpers ------------------------------------------------------
    def user(self, cid: str, create: bool = True) -> dict | None:
        u = self.users.get(cid)
        if u is None and create:
            u = {"rules": [], "paused": False, "next": 1, "t": int(time.time()), "u": int(time.time())}
            self.users[cid] = u
            self.changed = True
        return u

    def touch(self, u: dict) -> None:
        u["u"] = int(time.time())
        self.changed = True

    def reply(self, cid: str, text: str, markup: dict | None = None) -> None:
        try:
            self.tg.send(cid, text, markup)
            self.sent += 1
        except TelegramError as exc:
            log.warning("reply failed (%s)", exc.code)

    # ---- actions ---------------------------------------------------------------
    def add_rule(self, cid: str, compact: str, raw_json: str | None = None) -> None:
        u = self.user(cid)
        try:
            rule = R.from_json(raw_json) if raw_json else R.parse_compact(compact)
        except R.RuleError:
            self.reply(cid, "این کد قانون معتبر نیست. از دکمه‌ی «ساخت قانون دلخواه» استفاده کن.",
                       welcome_markup(self.username))
            return
        if len(u["rules"]) >= S.MAX_RULES_PER_USER:
            self.reply(cid, f"حداکثر {S.MAX_RULES_PER_USER} قانون مجاز است. با /rules یکی را حذف کن.")
            return
        rule["id"] = u["next"]
        u["next"] += 1
        u["rules"].append(rule)
        u["paused"] = False
        self.touch(u)
        self.reply(cid, f"✅ قانون {len(u['rules'])} ذخیره شد:\n{html.escape(R.describe(rule))}\n\n{preview(rule)}")

    def list_rules(self, cid: str) -> None:
        u = self.user(cid, create=False)
        if not u or not u["rules"]:
            self.reply(cid, "هنوز قانونی نساخته‌ای.", welcome_markup(self.username))
            return
        lines = ["<b>قانون‌های تو</b>" + (" (⏸ متوقف)" if u["paused"] else "")]
        buttons = []
        for i, r in enumerate(u["rules"], 1):
            lines.append(f"{i}. {html.escape(R.describe(r))}")
            buttons.append([{"text": f"🗑 حذف قانون {i}", "callback_data": f"x:{r['id']}"}])
        buttons.append([{"text": "➕ قانون جدید", "url": builder_url(self.username)}])
        self.reply(cid, "\n".join(lines), {"inline_keyboard": buttons})

    def delete_rule(self, cid: str, rule_id: int | None = None, index: int | None = None) -> None:
        u = self.user(cid, create=False)
        if not u:
            return
        before = len(u["rules"])
        if rule_id is not None:
            u["rules"] = [r for r in u["rules"] if r["id"] != rule_id]
        elif index is not None and 1 <= index <= before:
            u["rules"].pop(index - 1)
        if len(u["rules"]) < before:
            self.touch(u)
            self.reply(cid, "🗑 قانون حذف شد.")
        else:
            self.reply(cid, "این قانون پیدا نشد. /rules را ببین.")

    def set_brands(self, cid: str, args: list[str]) -> None:
        u = self.user(cid, create=False)
        if not u or not args or not args[0].isdigit() or not (1 <= int(args[0]) <= len(u["rules"])):
            self.reply(cid, "نمونه: <code>/brand 1 سامسونگ شیائومی -متفرقه</code>")
            return
        rule = u["rules"][int(args[0]) - 1]
        rule["bin"] = [a for a in args[1:] if not a.startswith("-")][:10]
        rule["bout"] = [a[1:] for a in args[1:] if a.startswith("-") and len(a) > 1][:10]
        self.touch(u)
        self.reply(cid, f"✅ به‌روز شد:\n{html.escape(R.describe(rule))}\n\n{preview(rule)}")

    def export(self, cid: str) -> None:
        u = self.user(cid, create=False)
        data = {"chat_id": cid, **u} if u else {"chat_id": cid, "rules": []}
        self.reply(cid, "<b>همه‌ی داده‌ای که از تو ذخیره شده:</b>\n<pre>"
                   + html.escape(json.dumps(data, ensure_ascii=False, indent=1)) + "</pre>")

    def delete_me(self, cid: str) -> None:
        if cid in self.users:
            del self.users[cid]
            self.changed = True
        self.reply(cid, "همه‌ی داده‌ی تو پاک شد. هر وقت خواستی دوباره /start بزن.")

    # ---- dispatch ---------------------------------------------------------------
    def handle_message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return
        cid = str(chat["id"])
        web = (msg.get("web_app_data") or {}).get("data")
        if web:  # rule sent from the Mini App (keyboard button)
            self.add_rule(cid, "", raw_json=web[:4096])
            return
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            self.reply(cid, HELP, welcome_markup(self.username))
            return
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        if cmd == "/start":
            u = self.user(cid)
            self.touch(u)  # also clears an earlier "blocked" mark
            if args:
                self.add_rule(cid, args[0])
            else:
                self.reply(cid, "دکمه‌ی «🛠 ساخت قانون» پایین صفحه، صفحه‌ی ساخت قانون را داخل تلگرام باز می‌کند.",
                           app_keyboard(self.username))
                self.reply(cid, "سلام 👋\nبهترین آفرهای دیجی‌کالا را طبق شرایط خودت برایت می‌فرستم.\n"
                                "یک قانون آماده انتخاب کن یا قانون دلخواهت را بساز.\n\n" + HELP,
                           welcome_markup(self.username))
        elif cmd == "/add" and args:
            self.add_rule(cid, args[0])
        elif cmd == "/rules":
            self.list_rules(cid)
        elif cmd == "/del" and args and args[0].isdigit():
            self.delete_rule(cid, index=int(args[0]))
        elif cmd == "/brand":
            self.set_brands(cid, args)
        elif cmd in ("/pause", "/resume"):
            u = self.user(cid)
            u["paused"] = cmd == "/pause"
            self.touch(u)
            self.reply(cid, "⏸ ارسال متوقف شد. /resume برای ادامه." if u["paused"] else "▶️ ارسال ادامه پیدا کرد.")
        elif cmd == "/export":
            self.export(cid)
        elif cmd == "/delete_me":
            self.delete_me(cid)
        else:
            self.reply(cid, HELP, welcome_markup(self.username))

    def handle_callback(self, cb: dict) -> None:
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return
        cid = str(chat["id"])
        data = cb.get("data") or ""
        self.tg.answer_callback(cb["id"])
        if data.startswith("p:"):
            self.add_rule(cid, data[2:])
        elif data.startswith("x:") and data[2:].isdigit():
            self.delete_rule(cid, rule_id=int(data[2:]))

    def handle(self, update: dict) -> None:
        try:
            if "message" in update:
                self.handle_message(update["message"])
            elif "callback_query" in update:
                self.handle_callback(update["callback_query"])
        except Exception:  # noqa: BLE001  one bad update must not stop the others
            log.exception("update %s failed", update.get("update_id"))


def run(tg: Telegram) -> int:
    me = tg.get_me()
    bot = Bot(tg, me.get("username", ""))
    offset = state.read("bot_offset.json", {}).get("offset", 0)
    deadline = time.time() + S.BOT_LISTEN_SECONDS
    handled = 0
    while True:
        wait = int(max(0, min(25, deadline - time.time())))
        for upd in tg.get_updates(offset, wait):
            offset = max(offset, upd["update_id"] + 1)
            bot.handle(upd)
            handled += 1
        state.write("bot_offset.json", {"offset": offset})
        if bot.changed:
            state.write("users.json", bot.users)
        if time.time() >= deadline:
            break
    log.info("bot: %s update(s), %s repl(ies), %s user(s)", handled, bot.sent, len(bot.users))
    return 0
