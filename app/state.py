"""Small JSON files that survive between runs (kept in the private deals-state repo)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import settings as S


def _path(name: str) -> Path:
    d = Path(S.STATE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def read(name: str, default):
    p = _path(name)
    if not p.exists():
        return default
    # A corrupt file must stop the run loudly, not silently reset history.
    return json.loads(p.read_text("utf-8"))


def write(name: str, obj) -> None:
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")
    os.replace(tmp, p)


# ---- price history: {dkp: [[day, price, rrp], ...]} one entry per day ----------

def record_history(history: dict, offers: list[dict], today: int) -> None:
    for o in offers:
        if not o["price"]:
            continue
        key = str(o["dkp"])
        rows = history.setdefault(key, [])
        if rows and rows[-1][0] == today:
            rows[-1][1] = min(rows[-1][1], o["price"])
            rows[-1][2] = o["rrp"] or rows[-1][2]
        else:
            rows.append([today, o["price"], o["rrp"] or 0])
    cutoff = today - S.HISTORY_KEEP_DAYS
    for key in list(history):
        history[key] = [r for r in history[key] if r[0] >= cutoff]
        if not history[key]:
            del history[key]


def prune_sent(sent: dict, now_ts: float) -> None:
    for key in list(sent):
        if now_ts - sent[key]["t"] > 48 * 3600:
            del sent[key]
