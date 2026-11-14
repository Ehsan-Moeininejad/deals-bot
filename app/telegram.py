"""Minimal Telegram Bot API client with retries."""
from __future__ import annotations

import logging
import time

import requests

from . import settings as S

log = logging.getLogger("telegram")


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str):
        self.url = f"https://api.telegram.org/bot{token}/"

    def _call(self, method: str, payload: dict) -> dict:
        last = None
        for attempt in range(1, 5):
            try:
                r = requests.post(self.url + method, json=payload, timeout=30)
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
                raise TelegramError(f"{r.status_code}: {data.get('description')}")
            except requests.RequestException as exc:
                last = exc
                time.sleep(2 * attempt)
        raise TelegramError(f"gave up after retries: {last}")

    def send(self, chat_id: str, text: str) -> int:
        res = self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
        return res["message_id"]

    def send_all(self, chat_id: str, messages: list[str]) -> list[int]:
        ids = []
        for i, m in enumerate(messages):
            if i:
                time.sleep(S.SEND_DELAY)
            ids.append(self.send(chat_id, m))
        return ids
