"""Entry point:  python -m app.main digest [--force]"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta

from . import bot, collector, delivery, digest, nudge, scoring, state
from . import settings as S
from .telegram import Telegram, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
log = logging.getLogger("main")


def target_post_time(now: datetime) -> datetime:
    """Next top of the hour if we are early (minute >= 40); otherwise right now."""
    if now.minute >= 40:
        return (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return now


def slot_of(post_time: datetime) -> str:
    """One posting slot per hour, e.g. 2026-11-14T13."""
    return post_time.strftime("%Y-%m-%dT%H")


def slot_done(post_time: datetime) -> bool:
    """True if this hour's slot was already handled (posted or skipped on purpose).

    Lets the backup GitHub schedule run safely next to cron-job.org: whichever
    run comes second does nothing.
    """
    return state.read("last_slot.json", {}).get("slot") == slot_of(post_time)


def mark_slot(post_time: datetime, posted: bool) -> None:
    state.write("last_slot.json", {"slot": slot_of(post_time), "posted": posted, "live": S.LIVE})


def alert(tg: Telegram | None, text: str) -> None:
    if tg and S.ADMIN_CHAT_ID:
        try:
            tg.send(S.ADMIN_CHAT_ID, f"⚠️ {text}")
        except Exception as exc:  # noqa: BLE001
            log.error("could not alert admin: %s", exc)


def run_digest(force: bool = False) -> int:
    if S.PAUSED:
        log.info("PAUSED is true - nothing to do")
        return 0
    if not S.BOT_TOKEN:
        log.error("BOT_TOKEN is not set")
        return 1
    tg = Telegram(S.BOT_TOKEN)
    destination = S.CHANNEL_ID if S.LIVE else S.ADMIN_CHAT_ID
    if not destination:
        log.error("destination chat id is empty (LIVE=%s)", S.LIVE)
        return 1

    now = datetime.now(S.TZ)
    post_time = target_post_time(now)
    if post_time.hour in S.QUIET_HOURS and not force:
        log.info("quiet hours (%s) - skipping", post_time.hour)
        return 0
    if slot_done(post_time) and not force:
        log.info("slot %s already handled by another run - skipping", slot_of(post_time))
        return 0

    offers, expected = collector.crawl()
    if len(offers) < S.MIN_PRODUCTS or (expected and len(offers) < expected * S.MIN_COMPLETENESS):
        alert(tg, f"کراول ناقص یا خالی بود ({len(offers)} از {expected or '?'}). چیزی منتشر نشد.")
        return 2

    today = now.date().toordinal()
    history = state.read("price_history.json", {})
    sent = state.read("offers_sent.json", {})

    scored = scoring.select(offers, history, today, now.timestamp())
    personal = scoring.select(offers, history, today, now.timestamp(), channel=False)
    new, ongoing = digest.split_new_ongoing(scored, sent, now.timestamp())
    log.info("products=%s eligible=%s new=%s ongoing=%s", len(offers), len(scored), len(new), len(ongoing))

    # history is data collection: always recorded, also in preview mode
    state.record_history(history, offers, today)
    state.write("price_history.json", history)
    state.write("latest_offers.json", delivery.compact_offers(personal))

    code, posted = 0, False
    if len(new) < S.MIN_NEW_TO_POST and not force:
        log.info("only %s new offers - no channel post this hour", len(new))
    else:
        try:
            bot_name = tg.get_me().get("username", "")
        except TelegramError:
            bot_name = ""
        groups = digest.group(new)
        shown = [o for _, items in groups for o in items]
        urgent = digest.last_chance(new + ongoing, now)
        salt = post_time.hour
        line = nudge.header_line(len(new), new[0], now.timestamp(), salt) if new else ""
        messages = digest.render(groups, ongoing, post_time, now,
                                 {"new": len(new), "checked": len(offers), "bot": bot_name},
                                 urgent, line)
        if not S.LIVE:
            messages[0] = "🧪 <b>پیش‌نمایش (حالت آزمایشی، به کانال ارسال نشد)</b>\n\n" + messages[0]

        # land exactly on the hour
        wait = (post_time - datetime.now(S.TZ)).total_seconds()
        if S.LIVE and 0 < wait <= S.MAX_WAIT_FOR_HOUR:
            log.info("waiting %.0fs to post at %s", wait, post_time.strftime("%H:%M"))
            time.sleep(wait)

        try:
            if S.HERO_PHOTO and shown:
                top = max(shown, key=lambda o: o["score"])
                caption = digest.hero(top, post_time, now, nudge.offer_line(top, now.timestamp(), salt))
                if not S.LIVE:
                    caption = "🧪 <b>پیش‌نمایش</b>\n\n" + caption
                try:
                    if not top.get("image"):
                        raise TelegramError("no image")
                    tg.send_photo(destination, top["image"], caption, digest.hero_markup(top))
                except TelegramError:
                    tg.send(destination, caption, digest.hero_markup(top))  # same card, without the photo
                time.sleep(S.SEND_DELAY)
            tg.send_all(destination, messages,
                        digest.channel_markup(bot_name, share=post_time.hour in S.SHARE_HOURS))
            posted = True
            if S.LIVE:
                ts = time.time()
                for o in shown:
                    sent[str(o["dkp"])] = {"t": ts, "p": o["price"]}
            log.info("posted %s message(s) to %s", len(messages), "channel" if S.LIVE else "admin preview")
        except TelegramError as exc:
            alert(tg, f"ارسال ناموفق: {exc}")
            code = 3

    state.prune_sent(sent, time.time())
    state.write("offers_sent.json", sent)

    # personal rules: independent of the channel post
    try:
        delivery.deliver(tg, personal, datetime.now(S.TZ), post_time)
    except Exception as exc:  # noqa: BLE001
        log.exception("personal delivery failed")
        alert(tg, f"ارسال شخصی با خطا متوقف شد: {type(exc).__name__}")

    if code == 0:
        mark_slot(post_time, posted=posted)
    return code


def run_bot() -> int:
    if S.PAUSED:
        log.info("PAUSED is true - nothing to do")
        return 0
    if not S.BOT_TOKEN:
        log.error("BOT_TOKEN is not set")
        return 1
    return bot.run(Telegram(S.BOT_TOKEN))


def main() -> int:
    args = sys.argv[1:]
    job = (args[0] if args else "") or "digest"
    force = "--force" in args
    try:
        if job == "digest":
            return run_digest(force)
        if job == "bot":
            return run_bot()
        log.error("unknown job: %s", job)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("run crashed")
        if S.BOT_TOKEN:
            alert(Telegram(S.BOT_TOKEN), f"اجرا با خطا متوقف شد: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
