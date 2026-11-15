"""Tests for sources, attractiveness, personal rules, the bot and personal delivery."""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from app import bot as B
from app import collector, delivery, digest, scoring
from app import rules as R
from app import settings as S
from app.telegram import TelegramError

from tests.test_core import NOW, NOW_TS, TODAY, offer


# ---- sources -----------------------------------------------------------------------

def test_find_listing_by_key_and_auto_detect():
    data = {"a": {"x": 1}, "deep": {"block": {"products": [{"id": 1}], "pager": {"total_pages": 2}}}}
    assert collector.find_listing(data, None)["pager"]["total_pages"] == 2
    assert collector.find_listing({"k": {"products": [1]}}, "k") == {"products": [1]}
    assert collector.find_listing({"k": {}}, None) == {}


def test_sources_have_required_fields():
    names = [s["name"] for s in S.SOURCES]
    assert len(names) == len(set(names))
    assert all(s["url"].startswith("https://") for s in S.SOURCES)


# ---- channel shape ---------------------------------------------------------------------

def test_attractiveness_is_a_percentage_and_shown():
    e = scoring.evaluate(offer(), [], TODAY, NOW_TS)
    pct = scoring.attractiveness(e)
    assert 0 <= pct <= 100
    assert f"جذابیت {pct}%" in digest._fmt_item(1, e, NOW)


def test_up_to_ten_per_category():
    many = [scoring.evaluate(offer(dkp=i), [], TODAY, NOW_TS) for i in range(25)]
    groups = digest.group(many)
    assert len(groups[0][1]) == 10 == S.ITEMS_PER_GROUP


def test_enrich_keeps_low_rated_offers_for_rules():
    low = offer(rate=70, count=3)  # 3.5 stars, 3 votes: not for the channel
    assert scoring.evaluate(low, [], TODAY, NOW_TS) is None
    e = scoring.enrich(low, [], TODAY, NOW_TS)
    assert e is not None and e["saving"] == 400_000  # 1.2M - 0.8M Toman


# ---- rule format -------------------------------------------------------------------------

def test_compact_roundtrip():
    rule = R.parse_compact("c0a_d30_a20_p5-200_r42_v50_md")
    assert rule["cats"] == [0, 10]
    assert rule["dmin"] == 30 and rule["amin"] == 2_000_000
    assert (rule["pmin"], rule["pmax"]) == (500_000, 20_000_000)
    assert rule["rmin"] == 4.2 and rule["vmin"] == 50 and rule["mode"] == "d"
    assert R.parse_compact(R.to_compact(rule)) == rule


@pytest.mark.parametrize("bad", ["", "x5", "d30_mq", "p9-1", "cz", "d30;rm", "a" * 70])
def test_compact_rejects_bad_input(bad):
    with pytest.raises(R.RuleError):
        R.parse_compact(bad)


def test_presets_are_valid_and_short():
    for _, code in B.PRESETS:
        R.parse_compact(code)
        assert len("p:" + code) <= 64  # Telegram callback_data limit


def test_page_categories_match_settings():
    page = Path(__file__).resolve().parent.parent / "docs" / "index.html"
    m = re.search(r"const CATS = (\[.*?\]);", page.read_text("utf-8"))
    assert json.loads(m.group(1)) == S.CATEGORIES


def test_matching():
    e = scoring.enrich(offer(cat="کالای دیجیتال"), [], TODAY, NOW_TS)  # 33% off, saves 400k
    ok = R.parse_compact("c0_d30_r40")
    assert R.matches(ok, e)
    assert not R.matches(R.parse_compact("c1"), e)              # other category
    assert not R.matches(R.parse_compact("d40"), e)             # discount too low
    assert not R.matches(R.parse_compact("a5"), e)              # saving < 500k
    assert not R.matches(R.parse_compact("p0-5"), e)            # price 800k > 500k
    ok["bout"] = ["B"]
    assert not R.matches(ok, e)                                 # brand "B" excluded


def test_describe_is_readable():
    text = R.describe(R.parse_compact("c0_d30_a20_p0-200_r40_mi"))
    assert "کالای دیجیتال" in text and "تخفیف ≥ 30%" in text and "2 میلیون" in text


# ---- bot -----------------------------------------------------------------------------------

class FakeTG:
    def __init__(self, fail_code=None):
        self.out = []
        self.fail_code = fail_code

    def send(self, chat_id, text, markup=None):
        if self.fail_code:
            raise TelegramError("x", self.fail_code)
        self.out.append((chat_id, text, markup))
        return 1

    def answer_callback(self, *_):
        pass


def msg(text, cid=42, kind="private"):
    return {"update_id": 1, "message": {"chat": {"id": cid, "type": kind}, "text": text}}


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE_DIR", str(tmp_path))
    return tmp_path


def test_bot_start_with_payload_saves_rule(tmp_state):
    tg = FakeTG()
    b = B.Bot(tg, "dk_bot")
    b.handle(msg("/start c0_d30_mi"))
    assert b.users["42"]["rules"][0]["dmin"] == 30
    assert "ذخیره شد" in tg.out[-1][1]


def test_bot_limits_rules_and_deletes(tmp_state):
    b = B.Bot(FakeTG(), "dk_bot")
    for _ in range(S.MAX_RULES_PER_USER + 2):
        b.handle(msg("/add d20"))
    assert len(b.users["42"]["rules"]) == S.MAX_RULES_PER_USER
    rid = b.users["42"]["rules"][0]["id"]
    b.handle({"update_id": 2, "callback_query": {"id": "c", "data": f"x:{rid}",
                                                   "message": {"chat": {"id": 42, "type": "private"}}}})
    assert len(b.users["42"]["rules"]) == S.MAX_RULES_PER_USER - 1


def test_bot_brand_pause_export_delete(tmp_state):
    tg = FakeTG()
    b = B.Bot(tg, "dk_bot")
    b.handle(msg("/add d20"))
    b.handle(msg("/brand 1 سامسونگ -متفرقه"))
    r = b.users["42"]["rules"][0]
    assert r["bin"] == ["سامسونگ"] and r["bout"] == ["متفرقه"]
    b.handle(msg("/pause"))
    assert b.users["42"]["paused"]
    b.handle(msg("/export"))
    assert "سامسونگ" in tg.out[-1][1]
    b.handle(msg("/delete_me"))
    assert "42" not in b.users


def test_bot_ignores_groups_and_bad_codes(tmp_state):
    tg = FakeTG()
    b = B.Bot(tg, "dk_bot")
    b.handle(msg("/add d30", kind="group"))
    assert not b.users and not tg.out
    b.handle(msg("/add ;;;"))
    assert b.users["42"]["rules"] == [] and "معتبر نیست" in tg.out[-1][1]


# ---- personal delivery ------------------------------------------------------------------------

def _users(tmp_state, mode="i", **extra):
    users = {"42": {"rules": [dict(R.parse_compact(f"c0_d30_m{mode}"), id=1)],
                    "paused": False, "u": 0, **extra}}
    (tmp_state / "users.json").write_text(json.dumps(users, ensure_ascii=False), "utf-8")


def _offers():
    return [scoring.enrich(offer(dkp=i, cat="کالای دیجیتال"), [], TODAY, NOW_TS) for i in range(15)]


def test_delivery_sends_once_and_caps_items(tmp_state):
    _users(tmp_state)
    tg = FakeTG()
    assert delivery.deliver(tg, _offers(), NOW, NOW) == 1
    assert tg.out[0][1].count("<a href") == S.USER_MAX_ITEMS
    assert delivery.deliver(tg, _offers(), NOW, NOW) == 1  # 5 not yet sent
    assert delivery.deliver(tg, _offers(), NOW, NOW) == 0  # nothing new


def test_delivery_daily_mode_waits_for_daily_hour(tmp_state):
    _users(tmp_state, mode="d")
    tg = FakeTG()
    assert delivery.deliver(tg, _offers(), NOW, NOW) == 0
    at21 = datetime(2026, 11, 14, S.DAILY_HOUR, 0, tzinfo=S.TZ)
    assert delivery.deliver(tg, _offers(), NOW, at21) == 1


def test_delivery_skips_paused_and_marks_blocked(tmp_state):
    _users(tmp_state, paused=True)
    assert delivery.deliver(FakeTG(), _offers(), NOW, NOW) == 0
    _users(tmp_state)
    delivery.deliver(FakeTG(fail_code=403), _offers(), NOW, NOW)
    blocked = json.loads((tmp_state / "blocked.json").read_text("utf-8"))
    assert "42" in blocked
    assert delivery.deliver(FakeTG(), _offers(), NOW, NOW) == 0  # stays blocked until /start


def test_compact_offers_keeps_fields_for_preview():
    out = delivery.compact_offers(_offers())
    assert set(out[0]) >= {"dkp", "title", "url", "price", "discount", "saving", "cat1", "brand"}
    assert R.matches(R.parse_compact("c0_d30"), out[0])


# ---- seller, delivery and flag filters ---------------------------------------------------------

def _live_like(**kw):
    r = offer(cat="کالای دیجیتال")
    r.update(seller_stars=4.25, official=False, jet=True, nearby=False, dk_stock=True, lowest_30d=False)
    r.update(kw)
    return scoring.enrich(r, [], TODAY, NOW_TS)


def test_parse_extracts_seller_and_delivery():
    raw_p = {"id": 5, "default_variant": {
        "seller": {"stars": 4.25, "properties": {"is_official": True}},
        "properties": {"nearby_seller_shipment": True, "in_digikala_warehouse": False},
        "shipment_methods": {"providers": [{"shipping_mode": "jet"}, {"shipping_mode": "seller"}]},
        "has_best_price_in_last_month": True, "price": {}}}
    o = collector.parse_product(raw_p)
    assert (o["seller_stars"], o["official"], o["jet"], o["nearby"], o["dk_stock"], o["lowest_30d"]) == \
        (4.25, True, True, True, False, True)


def test_seller_delivery_and_flag_matching():
    e = _live_like()
    assert R.matches(R.parse_compact("s40_fj"), e)
    assert not R.matches(R.parse_compact("s45"), e)          # seller 4.25 < 4.5
    assert not R.matches(R.parse_compact("fn"), e)           # no 3-hour delivery
    assert R.matches(R.parse_compact("fnw"), e)              # any of: 3-hour or in stock
    assert not R.matches(R.parse_compact("qo"), e)           # not an official seller
    assert R.matches(R.parse_compact("ql"), _live_like(lowest_30d=True))
    assert R.matches(R.parse_compact("mi"), e)               # empty rule matches everything


def test_rule_from_mini_app_json():
    raw_json = json.dumps({"code": "c0_d30_fn_mi", "bin": ["سامسونگ", " "], "bout": ["متفرقه"]})
    rule = R.from_json(raw_json)
    assert rule["ship"] == "n" and rule["bin"] == ["سامسونگ"] and rule["bout"] == ["متفرقه"]
    with pytest.raises(R.RuleError):
        R.from_json("{not json")
    with pytest.raises(R.RuleError):
        R.parse_compact("fx")


def test_bot_accepts_web_app_data(tmp_state):
    tg = FakeTG()
    b = B.Bot(tg, "dk_bot")
    data = json.dumps({"code": "d20_s40_mi", "bin": ["اپل"], "bout": []})
    b.handle({"update_id": 3, "message": {"chat": {"id": 42, "type": "private"},
                                          "web_app_data": {"data": data}}})
    rule = b.users["42"]["rules"][0]
    assert rule["smin"] == 4.0 and rule["bin"] == ["اپل"]


def test_channel_start_payload_is_a_plain_welcome(tmp_state):
    tg = FakeTG()
    b = B.Bot(tg, "dk_bot")
    b.handle(msg("/start channel"))
    assert b.users["42"]["rules"] == [] and "سلام" in tg.out[-1][1]


def test_profile_setup_runs_once(tmp_state):
    calls = []

    class TG(FakeTG):
        def call(self, method, payload):
            calls.append(method)
            return {}

    B.setup_profile(TG(), "dk_bot")
    B.setup_profile(TG(), "dk_bot")
    assert calls.count("setMyCommands") == 1 and "setChatMenuButton" in calls


def test_channel_intro_posted_once_and_pin_retried(tmp_state, monkeypatch):
    monkeypatch.setattr(S, "CHANNEL_ID", "@ch")
    pins = {"fail": True, "calls": 0}

    class TG(FakeTG):
        def call(self, method, payload):
            pins["calls"] += 1
            if pins["fail"]:
                raise TelegramError("no right", 400)
            return {}

    tg = TG()
    B.pin_intro(tg, "dk_bot")
    B.pin_intro(tg, "dk_bot")
    assert len(tg.out) == 1 and "دستیار خرید" in tg.out[0][1]   # posted once
    pins["fail"] = False
    B.pin_intro(tg, "dk_bot")
    assert json.loads((tmp_state / "channel_pin.json").read_text("utf-8"))["pinned"]
    assert len(tg.out) == 1
