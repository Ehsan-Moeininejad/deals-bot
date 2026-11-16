"""All tunable numbers live here. Change a value, commit, done.

Secrets are NEVER written in this file. They come from GitHub Secrets
(environment variables) at run time.
"""
import os
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tehran")

# ---- secrets / switches (from GitHub Secrets and Variables) ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")        # e.g. @my_channel
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # your private chat id
LIVE = os.environ.get("LIVE", "").strip().lower() == "true"      # false = preview to admin only
PAUSED = os.environ.get("PAUSED", "").strip().lower() == "true"  # true = do nothing
STATE_DIR = os.environ.get("STATE_DIR", "state")

# ---- data sources --------------------------------------------------------------
# Every source is crawled each run; offers found in several sources count once.
# To add a Black Friday landing later, append one entry:
#   {"name": "bf-mobile", "label": "بلک فرایدی", "url": "<API url of the landing>",
#    "list_key": "<key of the product list inside data, or None to auto-detect>"}
SOURCES = [
    # 2026-11-16: Digikala moved the list to amazing/v1 (the old v1/incredible-offers/ now
    # returns only a banner). Listing items are slim, so each product is completed from
    # the product API below (brand, category, seller, delivery).
    {"name": "incredible", "label": "", "kind": "amazing",
     "url": "https://api.digikala.com/amazing/v1/page/0"},
]
DETAIL_URL = "https://api.digikala.com/product/v1/products/{id}/"
DETAIL_WORKERS = 6          # parallel product-detail requests
DETAIL_BUDGET = 300         # seconds; products not completed in time keep the listing data
API_URL = SOURCES[0]["url"]  # kept for backward compatibility
REQUEST_TIMEOUT = 20
DELAY_BETWEEN_PAGES = 0.7
MAX_RETRIES = 4
MAX_PAGES = 200
PRICE_DIVISOR = 10          # API gives Rial, we show Toman
MIN_COMPLETENESS = 0.70     # fewer products than this share of the pager total = bad run
MIN_PRODUCTS = 200          # fewer products than this = bad run (site changed / blocked)
            # (checked on the first source only; extra landings may be small)

# ---- quality filter (an offer must pass ALL of these) ------------------------
MIN_RATING = 4.0            # out of 5
MIN_VOTES = 20
MIN_DISCOUNT = 15           # percent, after the price-history correction

# ---- score weights (must add up to 1.0) --------------------------------------
W_DISCOUNT = 0.55
W_RATING = 0.30
W_URGENCY = 0.15
MAX_DISCOUNT = 0.60         # discounts above 60% count as 60%
HISTORY_MIN_DAYS = 3        # days of history needed to "verify" an offer
COLD_START_FACTOR = 0.80    # discount weight for offers without enough history
HISTORY_KEEP_DAYS = 90

# ---- digest shape ------------------------------------------------------------
MAX_GROUPS = 8
ITEMS_PER_GROUP = 10        # top offers per category in the channel post
RICH_PER_GROUP = 3          # shown in full; the rest of the category sits in a collapsed quote
TITLE_MAX = 48              # product name length in the full format
TITLE_MAX_COMPACT = 38      # product name length in the one-line format
HERO_PHOTO = True           # post the hour's best deal as a photo before the list
MAX_ONGOING = 5
MIN_NEW_TO_POST = 3         # fewer new offers than this = skip this hour
RESEND_HOURS = 24           # an offer is not re-posted within this window...
REPRICE_DROP = 0.05         # ...unless its price dropped by at least 5%
QUIET_HOURS = range(1, 8)   # channel + personal: from 08:00 to the 00:00 post (last one), nothing 01:00-07:59
VOUCHER_QUIET_HOURS = range(0, 8)  # codes: 08:30 ... 23:30 (none after midnight)
VOUCHER_ROUNDUP_HOURS = [11, 20]   # if no new codes, a roundup of all active codes at HH:30 (codes rarely change)
MESSAGE_LIMIT = 3800        # Telegram's hard limit is 4096
SEND_DELAY = 1.5            # seconds between messages
MAX_WAIT_FOR_HOUR = 15 * 60 # wait at most this long to land exactly on the hour
END_SHOW_HOURS = 6          # show the deal end time only when it ends within this many hours

# ---- personal rules bot (M3) -----------------------------------------------------
PAGES_URL = "https://ehsan-moeininejad.github.io/deals-bot/"  # rule builder page (GitHub Pages)
BOT_LISTEN_SECONDS = 60     # each bot run listens this long for new messages
BOT_PROFILE_VERSION = 3     # bump to re-send the bot's menu, commands and description
CHANNEL_PIN_VERSION = 3     # bump to post and pin a new intro message in the channel

# ---- voucher codes (M2) -------------------------------------------------------------
VOUCHER_API = "https://api.digikala.com/v1/dynamic-landing/voucher-center/"
VOUCHER_MINUTE = 30         # check once per hour, from this minute on (Tehran)
MAX_RULES_PER_USER = 5
USER_MAX_ITEMS = 10         # offers per personal message
USER_DAILY_MESSAGES = 20    # hard cap of personal messages per user per day
DAILY_HOUR = 21             # Tehran hour for "daily" rules
LATEST_OFFERS_KEEP = 400    # offers kept for instant previews in the bot

# Top-level categories, in a FIXED order: the rule link stores their position.
# Only append new names at the end, never reorder.
CATEGORIES = [
    "کالای دیجیتال", "آرایشی و بهداشتی", "کالاهای سوپرمارکتی", "مد و پوشاک",
    "طلا و نقره", "سلامت و پزشکی", "پت شاپ", "مادر و کودک",
    "محصولات بومی و محلی", "خانه و آشپزخانه", "لوازم خانگی برقی", "ورزش و سفر",
    "خودرو و موتورسیکلت", "ابزار آلات و تجهیزات", "کتاب، لوازم تحریر و هنر", "اسباب بازی",
]

# ---- one-line nudges (true scarcity signals + optional free LLMs) ---------------------
LOW_STOCK = 5               # "only N left" when the offer has this many units or fewer
ENDING_SOON_HOURS = 2       # "ends at HH:MM" when the offer ends within this many hours
MOSTLY_SOLD = 70            # "N% sold" from this share on
LAST_CHANCE_MAX = 3         # items in the «رو به اتمام» strip at the top of the list
LLM_TIMEOUT = 8             # seconds per request before switching to the next model
LLM_BUDGET = 20             # seconds per line in total; then the rule-based line is used
LLM_MAX_CHARS = 110
LLM_MODELS_PER_PROVIDER = 2  # how many models of one provider to try before moving on
# FREE ONLY. Model names change often, so they are not hardcoded: each run reads the
# provider's live model list and keeps only free text models (see app/nudge.py).
#   gemini     - free tier of Google AI Studio (flash / flash-lite text models only)
#   groq       - free plan (no card on the account = nothing can be charged)
#   openrouter - only models whose id ends in ":free" AND whose listed price is 0
# Tried top to bottom (fastest free first; Gemini's free thinking models often time out);
# a provider is used only if its key secret is set.
LLM_PROVIDERS = [
    {"name": "groq", "kind": "openai", "key_env": "GROQ_API_KEY",
     "url": "https://api.groq.com/openai/v1/chat/completions",
     "models_url": "https://api.groq.com/openai/v1/models",
     "prefer": ["gpt-oss-120b", "qwen", "gpt-oss-20b", "llama", "kimi"]},
    {"name": "openrouter", "kind": "openai", "key_env": "OPENROUTER_API_KEY", "free_only": True,
     "url": "https://openrouter.ai/api/v1/chat/completions",
     "models_url": "https://openrouter.ai/api/v1/models",
     "prefer": ["llama", "gemma", "qwen", "mistral", "deepseek", "gpt-oss"]},
    {"name": "gemini", "kind": "gemini", "key_env": "GEMINI_API_KEY"},
    {"name": "cloudflare", "kind": "cloudflare", "model": "@cf/meta/llama-3.1-8b-instruct",
     "key_env": "CF_API_TOKEN", "account_env": "CF_ACCOUNT_ID"},
]
# words that mark a model as not a plain chat/text model (skipped)
LLM_SKIP_WORDS = ["image", "vision", "tts", "audio", "live", "embed", "whisper", "guard",
                  "orpheus", "playai", "transcribe", "compound", "search", "robotics", "omni"]

# ---- sharing (kept light: the bot is for finding offers, not for promoting itself) ------
SHARE_HOURS = [21]          # the channel list carries a «send to friends» button only at these hours
