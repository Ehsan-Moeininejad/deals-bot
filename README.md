# deals-bot — بات آفرهای دیجی‌کالا

رایگان و متن‌باز. هر ساعت بهترین آفرهای شگفت‌انگیز دیجی‌کالا را دسته‌بندی‌شده در یک کانال تلگرام می‌گذارد.
لینک‌ها مستقیم است و افیلیت ندارد. این پروژه وابسته به دیجی‌کالا نیست و قیمت‌ها ممکن است تغییر کرده باشد.

Free and open source. Posts a categorized hourly digest of Digikala's best incredible offers to a Telegram channel.
Links are plain (no affiliate). Not affiliated with Digikala; prices may change.

## How it works
`collector` fetches the live list → `scoring` filters and ranks → `digest` groups and renders → `telegram` posts.
Everything runs on GitHub Actions; state lives in a private repo. All numbers are in `app/settings.py`.

## Personal rules / قانون‌های شخصی
Users open the bot, pick a preset or build a rule on the page in `docs/` (GitHub Pages), and get matching offers privately.
A rule: categories, min discount %, min discount amount, price range, min rating and votes, brands, instant or daily.
Commands: `/rules` `/add <code>` `/brand <n> name -name` `/pause` `/resume` `/export` `/delete_me`.
The bot is not always online: it runs every few minutes, so replies can take a few minutes.

کاربر بات را Start می‌کند، یک قانون آماده انتخاب می‌کند یا در صفحه‌ی ساخت قانون شرایطش را می‌سازد، و آفرهای مطابق را خصوصی می‌گیرد.

## Adding a landing page / افزودن لندینگ
Append one entry to `SOURCES` in `app/settings.py` (name, label, API url, list key or `None`). Offers found in several sources count once.

## Run the tests
`pip install -r requirements.txt pytest && python -m pytest`

License: MIT

## Privacy / حریم خصوصی
- Public repo = code only. User data (chat ids, saved rules) lives in a separate private repo or database, never here.
- Only the Telegram `chat_id` and the user's rules are stored. No names, no usernames.
- Run logs of a public repo are public: the code never prints user data.
- Anyone can self-host with their own bot token and their own state storage.

- ریپوی عمومی فقط کد دارد. داده‌ی کاربران در یک ریپوی خصوصی یا دیتابیس جدا نگهداری می‌شود.
- فقط `chat_id` و قانون‌های تنظیم‌شده‌ی کاربر ذخیره می‌شود، بدون نام و username.
