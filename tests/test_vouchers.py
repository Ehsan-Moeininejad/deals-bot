"""Voucher center: parsing (shape verified on the live API), change detection, posting."""
import json
from datetime import datetime

import pytest

from app import settings as S
from app import vouchers as V
from tests.test_m3 import FakeTG as _Base


class FakeTG(_Base):
    def send_all(self, chat_id, messages, last_markup=None):
        for i, m in enumerate(messages):
            self.send(chat_id, m, last_markup if i == len(messages) - 1 else None)

WIDGETS = [
    {"widget_type": "banner", "data": {"title": ""}},
    {"widget_type": "vouchers", "data": {"title": "کدهای تخفیف مد و پوشاک", "vouchers": [
        {"code": "DSKKD_0408_1008", "code_type": "promo", "title": "بهترین برندهای ساعت",
         "description": "۵۰۰,۰۰۰ تومان تخفیف برای خرید بالای ۱,۵۰۰,۰۰۰ ",
         "url": {"base": None, "uri": "/product-list/plp_351199923/?sort=7&camCode=2703"}}]}},
    {"widget_type": "vouchers", "data": {"title": "کالای دیجیتال", "vouchers": [
        {"code_type": "serial", "title": "کالای دیجیتال", "description": "تخفیف تا 10 میلیون تومان",
         "url": {"uri": "/product-list/plp_464089826/"}, "voucher_id": "RVFGQTZZ"},
        {"code": "DSKKD_0408_1008", "title": "تکراری", "description": "x", "url": {}}]}},
]


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE_DIR", str(tmp_path))
    return tmp_path


def test_parse_promo_and_serial_and_dedupe():
    v = V.parse(WIDGETS)
    assert [x["key"] for x in v] == ["DSKKD_0408_1008", "RVFGQTZZ"]
    assert v[0]["group"] == "کدهای تخفیف مد و پوشاک"
    assert v[0]["url"].startswith("https://www.digikala.com/product-list/")
    assert v[1]["code"] == ""  # serial: no public code


def test_fresh_detects_new_and_changed_terms():
    v = V.parse(WIDGETS)
    seen = {"DSKKD_0408_1008": {"t": 1, "d": v[0]["desc"]}, "RVFGQTZZ": {"t": 1, "d": "old terms"}}
    assert [x["key"] for x in V.fresh(v, seen)] == ["RVFGQTZZ"]


def test_render_has_copyable_codes_and_groups():
    text = "\n".join(V.render(V.parse(WIDGETS), "🎟 test"))
    assert "<code>DSKKD_0408_1008</code>" in text
    assert "🔌 <b>کالای دیجیتال</b>" in text and "کد در صفحه‌ی کالاها" in text
    assert "<b>500 هزار تومان</b> برای خرید بالای 1.5 میلیون" in text


def test_due_once_per_hour_after_minute(tmp_state):
    t = datetime(2026, 11, 15, 15, S.VOUCHER_MINUTE + 2, tzinfo=S.TZ)
    assert V.due(t)
    assert not V.due(t.replace(minute=S.VOUCHER_MINUTE - 5))
    assert not V.due(t.replace(hour=3))  # quiet hours
    (tmp_state / "vouchers_slot.json").write_text(json.dumps({"slot": "2026-11-15T15"}))
    assert not V.due(t)


def test_run_posts_new_once_and_sends_to_subscribers(tmp_state, monkeypatch):
    monkeypatch.setattr(S, "ADMIN_CHAT_ID", "1")
    monkeypatch.setattr(V, "fetch", lambda: V.parse(WIDGETS))
    tg = FakeTG()
    users = {"42": {"codes": True, "paused": False}, "43": {"codes": False}}
    t = datetime(2026, 11, 15, 15, 35, tzinfo=S.TZ)
    assert V.run(tg, users, t) == 2
    targets = [c for c, *_ in tg.out]
    assert "1" in targets and "42" in targets and "43" not in targets
    assert V.run(tg, users, t.replace(hour=16)) == 0  # nothing new next hour


def test_terms_and_clean_links():
    assert V.terms("۳۰٪ تخفیف تا سقف ۱۰۰,۰۰۰ تومان") == ("30%", "تا سقف 100 هزار تومان")
    assert V.terms("3% تخفیف برای خرید بالای 120میلیون تومان") == ("3%", "برای خرید بالای 120 میلیون")
    assert V.terms("ارسال فروشنده رایگان") == ("ارسال رایگان", "")
    url = V.clean_url("https://www.digikala.com/product-list/plp_1/?sort=7&camCode=2703&utm_campaign=x")
    assert url == "https://www.digikala.com/product-list/plp_1/?sort=7"


def test_expired_codes_are_struck_through(tmp_state):
    calls = []

    class TG(FakeTG):
        def call(self, method, payload):
            calls.append(payload)
            return {}

        def send_all(self, chat_id, messages, last_markup=None):
            return [77]

    items = V.parse(WIDGETS)
    V._post(TG(), "@ch", items, "head", "", V.CHANNEL_MARKUP)
    assert V.mark_expired(TG(), {"RVFGQTZZ"}) == 1          # the promo code left the page
    assert "⌛️" in calls[0]["text"] and calls[0]["message_id"] == 77
    assert V.mark_expired(TG(), {"RVFGQTZZ"}) == 0          # not edited twice


def test_codes_only_between_8_and_midnight(tmp_state):
    from datetime import datetime
    assert not V.due(datetime(2026, 11, 16, 0, 35))
    assert not V.due(datetime(2026, 11, 16, 7, 40))
    assert V.due(datetime(2026, 11, 16, 8, 31)) and V.due(datetime(2026, 11, 16, 23, 31))
