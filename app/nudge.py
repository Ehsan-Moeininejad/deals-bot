"""One-line copy for posts: a short, natural sentence that nudges without sounding like an ad.

Every line is built ONLY from facts in the data (units left, share sold, end time,
lowest price of the month, saving). Nothing is invented: an LLM line that mentions a
number not present in the facts is thrown away and the rule-based line is used.

LLMs: FREE ONLY. Model names are read from each provider's live model list on every run
and filtered to free text models (Gemini flash family, Groq free plan, OpenRouter ids
ending in ":free" with a listed price of 0). On a timeout, rate limit or server error
the next model is tried at once, and a failed model is skipped for the rest of the run.
No key at all = rule-based lines only (the bot works the same).
"""
from __future__ import annotations

import logging
import os
import re
import time

import requests

from . import settings as S

log = logging.getLogger("nudge")

_down: dict[str, float] = {}         # "provider:model" -> time it failed (skipped this run)
_models_cache: dict[str, list[str]] = {}  # provider -> free models found this run

PERSIAN = re.compile(r"[؀-ۿ]")
DIGITS = re.compile(r"\d[\d,.]*")


# ---- facts ------------------------------------------------------------------------------

def short_amount(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " میلیون"
    if n >= 1_000:
        return f"{n // 1_000:,} هزار"
    return f"{n:,}"


def urgency(o: dict, now_ts: float) -> str | None:
    """The strongest TRUE scarcity signal of an offer, or None."""
    if o.get("stock") and o["stock"] <= S.LOW_STOCK:
        return "stock"
    if o.get("ends_at") and 0 < o["ends_at"] - now_ts <= S.ENDING_SOON_HOURS * 3600:
        return "ends"
    if (o.get("sold_pct") or 0) >= S.MOSTLY_SOLD:
        return "sold"
    return None


def facts(o: dict, now_ts: float) -> dict:
    from datetime import datetime
    f = {"name": o["title"][:60], "discount": o["discount"], "price": o["price"]}
    if o.get("saving"):
        f["saving"] = short_amount(o["saving"])
    if o.get("stock") and o["stock"] <= S.LOW_STOCK:
        f["units_left"] = o["stock"]
    if o.get("sold_pct"):
        f["sold_percent"] = o["sold_pct"]
    if o.get("ends_at") and 0 < o["ends_at"] - now_ts <= 24 * 3600:
        f["ends_at"] = datetime.fromtimestamp(o["ends_at"], S.TZ).strftime("%H:%M")
    if o.get("lowest_30d"):
        f["lowest_price_of_month"] = True
    return f


# ---- rule-based lines (always available) --------------------------------------------------

def rule_line(o: dict, now_ts: float, salt: int = 0) -> str:
    f = facts(o, now_ts)
    options = []
    if "units_left" in f:
        options += [f"فقط {f['units_left']} عدد دیگر با این قیمت مانده.",
                    f"از این یکی {f['units_left']} تا بیشتر نمانده."]
    if "ends_at" in f and urgency(o, now_ts) == "ends":
        options += [f"این قیمت ساعت {f['ends_at']} تمام می‌شود.",
                    f"تا {f['ends_at']} فرصت هست، بعدش قیمت برمی‌گردد."]
    if (o.get("sold_pct") or 0) >= S.MOSTLY_SOLD:
        options += [f"{o['sold_pct']}% موجودی این آفر تا الان رفته.",
                    f"{o['sold_pct']}% این آفر فروش رفته؛ بقیه‌اش زیاد دوام نمی‌آورد."]
    if f.get("lowest_price_of_month"):
        options += ["ارزان‌ترین قیمت یک ماه اخیرش، همین الان.",
                    "پایین‌تر از این، در این ماه ندیده بودیمش."]
    if "saving" in f:
        options += [f"همین یکی {f['saving']} تومان توی جیبت می‌ماند.",
                    f"{f['saving']} تومان کمتر از قیمت معمولش."]
    if not options:
        options = [f"{f['discount']}% زیر قیمت معمول، با امتیاز خوب خریدارها."]
    return options[salt % len(options)]


def rule_header(n_new: int, salt: int = 0) -> str:
    return ["این ساعت چند تا آفر خوب آمده؛ بهترین‌ها اول.",
            f"{n_new} آفر تازه؛ اگر چیزی لازم داری، الان وقتش است.",
            "قیمت‌های این ساعت را از دست نده؛ بعضی‌ها زود تمام می‌شوند."][salt % 3]


def rule_codes(n: int, salt: int = 0) -> str:
    return [f"{n} کد تازه آمده؛ قبل از تمام‌شدن سقفشان استفاده کن.",
            "کد را کپی کن و موقع پرداخت بزن؛ همین.",
            "بعضی از این کدها سقف استفاده دارند و زود پر می‌شوند."][salt % 3]


# ---- free LLM router ----------------------------------------------------------------------

PROMPT = (
    "تو یک دوست باسلیقه هستی که در یک کانال تلگرام آفرهای خرید را معرفی می‌کند. "
    "فقط یک جمله‌ی کوتاه فارسی (حداکثر ۱۵ کلمه) بنویس که خواننده را به نگاه‌کردن تشویق کند، "
    "طبیعی و خودمانی، نه تبلیغاتی و بدون اغراق. فقط از همین اطلاعات استفاده کن و هیچ عدد یا "
    "ادعای دیگری اضافه نکن. بدون ایموجی، بدون نقل‌قول، بدون لینک. اطلاعات: "
)


def _keys() -> list[tuple[str, dict]]:
    out = []
    for p in S.LLM_PROVIDERS:
        key = os.environ.get(p["key_env"], "")
        if key and (not p.get("account_env") or os.environ.get(p["account_env"])):
            out.append((key, p))
    return out


def _version(model_id: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", model_id)[:3])


def _textual(model_id: str) -> bool:
    low = model_id.lower()
    return not any(w in low for w in S.LLM_SKIP_WORDS)


def _rank(ids: list[str], prefer: list[str]) -> list[str]:
    def score(m):
        low = m.lower()
        pos = next((i for i, w in enumerate(prefer) if w in low), len(prefer))
        return (pos, "preview" in low, tuple(-n for n in _version(m)))
    return sorted(ids, key=score)


def pick_gemini(models: list[dict]) -> list[str]:
    """Flash / flash-lite text models: these are the ones on the free tier."""
    ids = []
    for m in models:
        mid = m.get("name", "").split("/")[-1]
        if ("generateContent" in m.get("supportedGenerationMethods", [])
                and "flash" in mid and _textual(mid)
                and not re.search(r"exp|thinking|\d{3,}", mid.replace("flash", ""))):
            ids.append(mid)
    # newest first; stable models before previews; flash before flash-lite
    return sorted(ids, key=lambda m: ("preview" in m, "lite" in m, tuple(-n for n in _version(m))))


def pick_openrouter(models: list[dict], prefer: list[str]) -> list[str]:
    """Only ids ending in ':free' whose listed prompt AND completion price is exactly 0."""
    ids = []
    for m in models:
        mid, price = m.get("id", ""), m.get("pricing") or {}
        try:
            free = float(price.get("prompt", 1)) == 0 and float(price.get("completion", 1)) == 0
        except (TypeError, ValueError):
            free = False
        if mid.endswith(":free") and free and _textual(mid):
            ids.append(mid)
    return _rank(ids, prefer)


def pick_groq(models: list[dict], prefer: list[str]) -> list[str]:
    ids = [m.get("id", "") for m in models if m.get("active", True) and _textual(m.get("id", ""))]
    return _rank([i for i in ids if i], prefer)


def _models(p: dict, key: str) -> list[str]:
    name = p["name"]
    if name in _models_cache:
        return _models_cache[name]
    found: list[str] = []
    try:
        if p["kind"] == "gemini":
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": key, "pageSize": 1000}, timeout=S.LLM_TIMEOUT)
            r.raise_for_status()
            found = pick_gemini(r.json().get("models", []))
        elif p["kind"] == "cloudflare":
            found = [p["model"]]
        else:
            r = requests.get(p["models_url"], headers={"Authorization": f"Bearer {key}"},
                             timeout=S.LLM_TIMEOUT)
            r.raise_for_status()
            data = r.json().get("data", [])
            found = (pick_openrouter(data, p.get("prefer", [])) if p.get("free_only")
                     else pick_groq(data, p.get("prefer", [])))
    except Exception as exc:  # noqa: BLE001
        log.info("copy: %s model list unavailable (%s)", name, type(exc).__name__)
    _models_cache[name] = found[:S.LLM_MODELS_PER_PROVIDER]
    log.info("copy: %s free models: %s", name, ", ".join(_models_cache[name]) or "none")
    return _models_cache[name]


def _call(p: dict, key: str, model: str, prompt: str) -> str:
    kind = p["kind"]
    if kind == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = requests.post(url, params={"key": key}, timeout=S.LLM_TIMEOUT, json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.9}})
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return " ".join(x.get("text", "") for x in parts if not x.get("thought"))
    if kind == "cloudflare":
        acc = os.environ[p["account_env"]]
        url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{model}"
        r = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=S.LLM_TIMEOUT,
                          json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 80})
        r.raise_for_status()
        return r.json()["result"]["response"]
    # OpenAI-compatible (Groq, OpenRouter); room for models that reason before answering
    r = requests.post(p["url"], headers={"Authorization": f"Bearer {key}"}, timeout=S.LLM_TIMEOUT, json={
        "model": model, "max_tokens": 600, "temperature": 0.9,
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content") or ""


def clean(text: str, allowed: str) -> str | None:
    """Accept one short Persian line whose numbers all appear in the facts."""
    line = " ".join((text or "").strip().strip('"«»“”').split())
    line = line.split("\n")[0]
    if not line or len(line) > S.LLM_MAX_CHARS or not PERSIAN.search(line):
        return None
    if re.search(r"https?://|@|#|<|>", line):
        return None
    fa = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    line = line.translate(fa)
    for num in DIGITS.findall(line):
        if num.strip(",.") not in allowed:
            return None  # a number that is not in the data: possibly invented
    return line


def llm_line(context: dict) -> str | None:
    """One line from the first free model that answers well, or None."""
    allowed = " ".join(str(v) for v in context.values())
    prompt = PROMPT + str(context)
    start = time.time()
    for key, p in _keys():
        for model in _models(p, key):
            tag = f"{p['name']}:{model}"
            if tag in _down:
                continue
            if time.time() - start > S.LLM_BUDGET:
                return None
            t0 = time.time()
            try:
                line = clean(_call(p, key, model, prompt), allowed)
            except Exception as exc:  # noqa: BLE001  timeout, 429, 5xx, bad JSON: switch at once
                _down[tag] = time.time()
                code = getattr(getattr(exc, "response", None), "status_code", "")
                log.info("copy: %s unavailable (%s %s), switching", tag, type(exc).__name__, code)
                continue
            if line:
                log.info("copy: line from %s in %.1fs", tag, time.time() - t0)
                return line
    return None


# ---- public helpers -----------------------------------------------------------------------

def offer_line(o: dict, now_ts: float, salt: int = 0) -> str:
    return llm_line(facts(o, now_ts)) or rule_line(o, now_ts, salt)


def header_line(n_new: int, top: dict, now_ts: float, salt: int = 0) -> str:
    ctx = {"new_offers_this_hour": n_new, "best_discount": top["discount"]}
    return llm_line(ctx) or rule_header(n_new, salt)


def codes_line(vouchers: list[dict], salt: int = 0) -> str:
    ctx = {"new_codes": len(vouchers), "examples": [v["desc"] for v in vouchers[:3]]}
    return llm_line(ctx) or rule_codes(len(vouchers), salt)
