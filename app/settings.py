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
    {"name": "incredible", "label": "", "url": "https://api.digikala.com/v1/incredible-offers/",
     "list_key": "incredible_products_list"},
]
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
MAX_ONGOING = 5
MIN_NEW_TO_POST = 3         # fewer new offers than this = skip this hour
RESEND_HOURS = 24           # an offer is not re-posted within this window...
REPRICE_DROP = 0.05         # ...unless its price dropped by at least 5%
QUIET_HOURS = range(1, 8)   # no posts between 01:00 and 07:59 Tehran
MESSAGE_LIMIT = 3800        # Telegram's hard limit is 4096
SEND_DELAY = 1.5            # seconds between messages
MAX_WAIT_FOR_HOUR = 15 * 60 # wait at most this long to land exactly on the hour
END_SHOW_HOURS = 6          # show the deal end time only when it ends within this many hours

# ---- personal rules bot (M3) -----------------------------------------------------
PAGES_URL = "https://ehsan-moeininejad.github.io/deals-bot/"  # rule builder page (GitHub Pages)
BOT_LISTEN_SECONDS = 60     # each bot run listens this long for new messages
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
