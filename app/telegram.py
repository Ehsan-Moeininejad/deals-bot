"""Minimal Telegram Bot API client with retries."""
from __future__ import annotations

import logging
import time

import requests

from . import settings as S

log = logging.getLogger("telegram")


class TelegramError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code  # HTTP status from Telegram, e.g. 403 = user blocked the bot


class Telegram:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}/"

    def _call(self, method: str, payload: dict, timeout: int = 30) -> dict:
        last = None
        for attempt in range(1, 5):
            try:
                r = requests.post(self.url + method, json=payload, timeout=timeout)
                data = r.json()
                if data.get("ok"):
                    return data["result"]
                if r.status_code == 429:
                    wait = int(data.get("parameters", {}).get("retry_after", 5)) + 1
                    log.warning("429 from Telegram, waiting %ss", wait)
                    time.sleep(wait)
                    continue
                if r.status_code >= 500:
                    time.sleep(2 * attempt)
                    continue
                raise TelegramError(f"{r.status_code}: {data.get('description')}", r.status_code)
            except requests.RequestException as exc:
                last = exc
                time.sleep(2 * attempt)
        raise TelegramError(f"gave up after retries: {last}")

    def send(self, chat_id: str, text: str, markup: dict | None = None) -> int:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if markup:
            payload["reply_markup"] = markup
        res = self._call("sendMessage", payload)
        return res["message_id"]

    def send_photo(self, chat_id: str, photo_url: str, caption: str, markup: dict | None = None) -> int:
        payload = {"chat_id": chat_id, "photo": photo_url, "caption": caption, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        return self._call("sendPhoto", payload)["message_id"]

    def get_me(self) -> dict:
        return self._call("getMe", {})

    def get_updates(self, offset: int, wait: int) -> list[dict]:
        return self._call("getUpdates", {
            "offset": offset, "timeout": wait,
            "allowed_updates": ["message", "callback_query"],
        }, timeout=wait + 15)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
        except TelegramError:
            pass  # callbacks older than ~15 minutes can no longer be answered

    def send_all(self, chat_id: str, messages: list[str], last_markup: dict | None = None) -> list[int]:
        """Send several messages; last_markup (buttons) goes under the last one."""
        ids = []
        for i, m in enumerate(messages):
            if i:
                time.sleep(S.SEND_DELAY)
            ids.append(self.send(chat_id, m, last_markup if i == len(messages) - 1 else None))
        return ids

    def call(self, method: str, payload: dict) -> dict:
        """Any other Bot API method (used for one-time bot profile setup)."""
        return self._call(method, payload)
