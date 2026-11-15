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

from . import digest
from . import rules as R
from . import settings as S
from . import state
from . import vouchers as V
from .telegram import Telegram, TelegramError

log = logging.getLogger("bot")

PRESETS = [
    ("📱 دیجیتال · 30%+", "c0_d30_r40_v20_mi"),
    ("🏠 لوازم خانگی · 25%+", "ca_d25_r40_v20_mi"),
    ("💄 آرایشی · 40%+", "c1_d40_r40_v20_mi"),
    ("🔥 هر چیزی · 50%+", "d50_r40_v50_mi"),
]

WAIT_NOTE = "⏳ تخفیف‌یاب هر چند دقیقه یک بار پیام‌ها را می‌خواند؛ جواب ممکن است کمی طول بکشد."

WELCOME = (
    "سلام 👋 من <b>تخفیف‌یاب</b> هستم.\n"
    "بگو دنبال چه هستی؛ هر وقت آفرش در دیجی‌کالا آمد، خبرت می‌کنم.\n\n"
    "<b>1.</b> یک قانون آماده بزن 👇\n"
    "<b>2.</b> یا با «🛠 ساخت قانون» شرط‌های خودت را بچین: دسته، قیمت، درصد یا مبلغ تخفیف، "
    "برند، امتیاز کالا و فروشنده، ارسال سریع یا 3 ساعته.\n\n"
    + WAIT_NOTE
)

HELP = (
    "📖 <b>راهنمای تخفیف‌یاب</b>\n\n"
    "🛠 <b>ساخت قانون</b> — دکمه‌ی منو یا پایین صفحه\n"
    "📋 /rules — قانون‌های من و حذف آن‌ها\n"
    "🎟 /codes — کدهای تخفیف فعال دیجی‌کالا\n"
    "🏷 /brand — برند شامل یا مستثنا، مثال: <code>/brand 1 سامسونگ -متفرقه</code>\n"
    "⏸ /pause · ▶️ /resume — توقف و ادامه‌ی ارسال\n"
    "🗂 /export — همه‌ی داده‌ی ذخیره‌شده‌ی من\n"
    "🗑 /delete_me — پاک‌کردن کامل داده‌ی من\n\n"
    + WAIT_NOTE
)


COMMANDS = [
    ("start", "شروع و قانون‌های آماده"),
    ("rules", "قانون‌های من"),
    ("codes", "کدهای تخفیف فعال دیجی‌کالا"),
    ("brand", "برند شامل یا مستثنا برای یک قانون"),
    ("pause", "توقف ارسال"),
    ("resume", "ادامه‌ی ارسال"),
    ("export", "دیدن داده‌ی من"),
    ("delete_me", "پاک‌کردن کامل داده‌ی من"),
    ("help", "راهنما"),
]
DESCRIPTION = (
    "تخفیف‌یاب بهترین آفرهای دیجی‌کالا را طبق شرایط خودت می‌فرستد: دسته، درصد و مبلغ تخفیف، "
    "بازه‌ی قیمت، برند، امتیاز کالا و فروشنده، ارسال سریع یا ۳ ساعته.\n"
    "رایگان و متن‌باز، لینک‌ها مستقیم و بدون افیلیت. Start را بزن."
)
SHORT_DESCRIPTION = "تخفیف‌یاب: آفرهای دیجی‌کالا طبق شرایط خودت — رایگان و بدون افیلیت"
BOT_NAME = "تخفیف‌یاب"


def setup_profile(tg: Telegram, username: str) -> None:
    """Menu button, command list and descriptions. Sent once per BOT_PROFILE_VERSION."""
    if state.read("bot_profile.json", {}).get("v") == S.BOT_PROFILE_VERSION:
        return
    tg.call("setMyName", {"name": BOT_NAME})
    tg.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
    tg.call("setMyDescription", {"description": DESCRIPTION})
    tg.call("setMyShortDescription", {"short_description": SHORT_DESCRIPTION})
    tg.call("setChatMenuButton", {"menu_button": {
        "type": "web_app", "text": "ساخت قانون", "web_app": {"url": builder_url(username)}}})
    state.write("bot_profile.json", {"v": S.BOT_PROFILE_VERSION})
    log.info("bot profile updated to version %s", S.BOT_PROFILE_VERSION)


def intro_text(username: str) -> str:
    return (
        "🛍 <b>تخفیف‌یاب | دستیار خرید از دیجی‌کالا</b>\n\n"
        "⚡️ <b>هر ساعت</b>: داغ‌ترین شگفت‌انگیزهای دیجی‌کالا، دسته‌بندی‌شده با قیمت قبل و بعد\n"
        "🎯 <b>فقط برای تو</b>: بگو دنبال چه هستی، هر وقت آمد خبرت می‌کنیم\n"
        "↳ دسته · قیمت · درصد یا مبلغ تخفیف · برند · امتیاز · ارسال سریع یا 3 ساعته\n\n"
        "<b>راهنمای علامت‌ها</b>\n"
        "🔻 درصد تخفیف · ⭐ امتیاز کالا · 🔥 درصد جذابیت · ⏰ زمان پایان آفر\n\n"
        f"👇 دستیار شخصی‌ات را روشن کن: @{username}\n"
        "<i>رایگان و متن‌باز · لینک‌ها مستقیم و بدون افیلیت · مستقل از دیجی‌کالا</i>"
    )


def pin_intro(tg: Telegram, username: str) -> None:
    """Post the intro once per CHANNEL_PIN_VERSION and pin it. If pinning fails
    (the bot lacks the right), the post is kept and only the pin is retried."""
    if not S.CHANNEL_ID or not username:
        return
    rec = state.read("channel_pin.json", {})
    if rec.get("v") != S.CHANNEL_PIN_VERSION:
        old_id = rec.get("id")
        markup = {"inline_keyboard": [[{"text": "🎯 روشن‌کردن دستیار خرید",
                                        "url": f"https://t.me/{username}?start=channel"}]]}
        rec = {"v": S.CHANNEL_PIN_VERSION, "id": tg.send(S.CHANNEL_ID, intro_text(username), markup),
               "pinned": False, "old": old_id}
        state.write("channel_pin.json", rec)
    if not rec.get("pinned"):
        try:
            tg.call("pinChatMessage", {"chat_id": S.CHANNEL_ID, "message_id": rec["id"],
                                       "disable_notification": True})
            rec["pinned"] = True
            if rec.get("old"):  # replace the previous intro instead of stacking pins
                try:
                    tg.call("deleteMessage", {"chat_id": S.CHANNEL_ID, "message_id": rec.pop("old")})
                except TelegramError:
                    pass
            state.write("channel_pin.json", rec)
            log.info("channel intro pinned")
        except TelegramError as exc:
            log.warning("pin failed (%s): give the bot the right to edit/pin messages", exc.code)


def builder_url(bot_username: str) -> str:
    return f"{S.PAGES_URL}?bot={bot_username}"


def app_keyboard(bot_username: str) -> dict:
    """Reply keyboard with a Mini App button: opens the rule builder inside Telegram
    and sends the full rule (brands included) straight back to the bot."""
    return {"keyboard": [[{"text": "🛠 ساخت قانون", "web_app": {"url": builder_url(bot_username)}}]],
            "resize_keyboard": True, "is_persistent": True}


def welcome_markup(bot_username: str) -> dict:
    presets = [{"text": t, "callback_data": f"p:{c}"} for t, c in PRESETS]
    rows = [presets[i:i + 2] for i in range(0, len(presets), 2)]
    rows.append([{"text": "🛠 ساخت قانون دلخواه", "url": builder_url(bot_username)},
                 {"text": "🎟 کدهای تخفیف", "callback_data": "c:"}])
    return {"inline_keyboard": rows}


def after_save_markup(bot_username: str) -> dict:
    return {"inline_keyboard": [[{"text": "📋 قانون‌های من", "callback_data": "l:"},
                                 {"text": "➕ قانون دیگر", "url": builder_url(bot_username)}]]}


def latest_offers() -> list[dict]:
    return state.read("latest_offers.json", [])


def preview(rule: dict) -> str:
    found = [o for o in latest_offers() if R.matches(rule, o)]
    if not found:
        return "🔎 فعلاً آفری با این شرط‌ها نیست؛ به محض پیدا شدن برایت می‌فرستم."
    lines = [f"🔎 <b>همین الان {len(found)} آفر با این شرط‌ها هست</b>؛ بهترین‌ها:"]
    lines += [digest.fmt_compact(o) for o in found[:3]]
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
        self.reply(cid, f"✅ <b>قانون {len(u['rules'])} ذخیره شد</b>\n{html.escape(R.describe(rule, chr(10)))}\n\n"
                        f"{preview(rule)}", after_save_markup(self.username))

    def list_rules(self, cid: str) -> None:
        u = self.user(cid, create=False)
        if not u or not u["rules"]:
            self.reply(cid, "هنوز قانونی نساخته‌ای.", welcome_markup(self.username))
            return
        lines = ["📋 <b>قانون‌های تو</b>" + (" · ⏸ ارسال متوقف است (/resume)" if u["paused"] else "") + "\n"]
        buttons = []
        for i, r in enumerate(u["rules"], 1):
            lines.append(f"<b>قانون {i}</b>\n{html.escape(R.describe(r, chr(10)))}")
            buttons.append({"text": f"🗑 حذف {i}", "callback_data": f"x:{r['id']}"})
        buttons = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
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
        self.reply(cid, f"✅ <b>برند به‌روز شد</b>\n{html.escape(R.describe(rule, chr(10)))}\n\n{preview(rule)}",
                   after_save_markup(self.username))

    def codes(self, cid: str) -> None:
        items = state.read("vouchers_latest.json", [])
        u = self.user(cid)
        on = bool(u.get("codes"))
        toggle = {"text": "🔕 کدهای جدید را دیگر نفرست" if on else "🔔 کدهای جدید را برایم بفرست",
                  "callback_data": "v:0" if on else "v:1"}
        markup = {"inline_keyboard": [[toggle], [{"text": "🎟 صفحه‌ی همه‌ی کدها", "url": V.PAGE_URL}]]}
        if not items:
            self.reply(cid, "🎟 فهرست کدها هنوز آماده نیست؛ کمی بعد دوباره /codes را بفرست.", markup)
            return
        msgs = V.render(items, f"🎟 <b>کدهای تخفیف فعال دیجی‌کالا</b> ({len(items)} کد)")
        for i, m in enumerate(msgs):
            self.reply(cid, m, markup if i == len(msgs) - 1 else None)

    def set_codes(self, cid: str, on: bool) -> None:
        u = self.user(cid)
        u["codes"] = on
        self.touch(u)
        self.reply(cid, "🔔 از این به بعد کدهای تخفیف تازه را برایت می‌فرستم." if on
                   else "🔕 دیگر کد تخفیف نمی‌فرستم. هر وقت خواستی /codes را بزن.")

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
            if args and args[0] != "channel":  # "channel" = came from the channel button
                self.add_rule(cid, args[0])
            else:
                self.reply(cid, "👇 دکمه‌ی «🛠 ساخت قانون» همیشه پایین صفحه است.",
                           app_keyboard(self.username))
                self.reply(cid, WELCOME, welcome_markup(self.username))
        elif cmd == "/add" and args:
            self.add_rule(cid, args[0])
        elif cmd == "/rules":
            self.list_rules(cid)
        elif cmd == "/codes":
            self.codes(cid)
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
        elif cmd == "/help":
            self.reply(cid, HELP, welcome_markup(self.username))
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
        elif data == "l:":
            self.list_rules(cid)
        elif data == "c:":
            self.codes(cid)
        elif data in ("v:0", "v:1"):
            self.set_codes(cid, data == "v:1")
        elif data == "z:":
            u = self.user(cid)
            u["paused"] = True
            self.touch(u)
            self.reply(cid, "⏸ ارسال متوقف شد. هر وقت خواستی /resume را بفرست.")

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
    try:
        setup_profile(tg, me.get("username", ""))
    except TelegramError as exc:
        log.warning("profile setup failed (%s)", exc.code)
    try:
        pin_intro(tg, me.get("username", ""))
    except TelegramError as exc:
        log.warning("channel intro failed (%s)", exc.code)
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
    try:
        V.run(tg, bot.users)
    except Exception:  # noqa: BLE001  vouchers must never break the bot
        log.exception("voucher check failed")
    log.info("bot: %s update(s), %s repl(ies), %s user(s)", handled, bot.sent, len(bot.users))
    return 0
