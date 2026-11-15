import time
from datetime import datetime

from app import collector, digest, scoring, state
from app import settings as S

NOW = datetime(2026, 11, 14, 13, 0, tzinfo=S.TZ)
NOW_TS = NOW.timestamp()
TODAY = NOW.date().toordinal()


def raw(dkp=1, selling=8_000_000, rrp=12_000_000, rate=88, count=300, title="گوشی تست",
        cat="موبایل", end=None, status="marketable"):
    return {
        "id": dkp, "title_fa": title, "status": status,
        "url": {"uri": f"/product/dkp-{dkp}/x/"},
        "rating": {"rate": rate, "count": count},
        "data_layer": {"brand": "B", "item_category2": cat, "item_category3": "c2"},
        "default_variant": {"seller": {"title": "S"}, "price": {
            "selling_price": selling, "rrp_price": rrp, "discount_percent": 33,
            "time": end, "is_lightening_deal": False}},
    }


def offer(**kw):
    return collector.parse_product(raw(**kw))


def test_parse_converts_rial_to_toman_and_rating_scale():
    o = offer()
    assert o["price"] == 800_000 and o["rrp"] == 1_200_000
    assert o["rating"] == 4.4          # 88/100 -> 4.4/5
    assert o["url"] == "https://www.digikala.com/product/dkp-1/"
    assert offer(rate=4.5)["rating"] == 4.5


def test_end_time_formats():
    assert collector._epoch(1_800_000_000) == 1_800_000_000
    assert collector._epoch(1_800_000_000_000) == 1_800_000_000
    assert collector._epoch("") is None and collector._epoch("garbage") is None


def test_rating_confidence_prefers_many_votes():
    assert scoring.rating_confidence(4.4, 2000) > scoring.rating_confidence(4.8, 3)


def test_filters():
    assert scoring.evaluate(offer(), [], TODAY, NOW_TS) is not None
    assert scoring.evaluate(offer(rate=70), [], TODAY, NOW_TS) is None        # 3.5 stars
    assert scoring.evaluate(offer(count=5), [], TODAY, NOW_TS) is None        # few votes
    assert scoring.evaluate(offer(status="out_of_stock"), [], TODAY, NOW_TS) is None
    assert scoring.evaluate(offer(selling=11_500_000), [], TODAY, NOW_TS) is None  # ~4% off
    assert scoring.evaluate(offer(end=NOW_TS - 10), [], TODAY, NOW_TS) is None    # expired


def test_inflated_rrp_is_corrected_by_history():
    o = offer(selling=8_000_000, rrp=12_000_000)        # looks like 33% off
    hist = [[TODAY - d, 800_000, 900_000] for d in range(1, 6)]   # past rrp was 900k
    e = scoring.evaluate(o, hist, TODAY, NOW_TS)
    assert e is None or e["discount"] < 15              # real discount ~11%


def test_verified_beats_unverified_with_same_numbers():
    o = offer()
    hist = [[TODAY - d, 800_000, 1_200_000] for d in range(1, 6)]
    a = scoring.evaluate(o, hist, TODAY, NOW_TS)
    b = scoring.evaluate(o, [], TODAY, NOW_TS)
    assert a["verified"] and not b["verified"] and a["score"] > b["score"]


def test_history_record_prune_and_daily_min():
    h = {}
    o1, o2 = offer(selling=8_000_000), offer(selling=7_000_000)
    state.record_history(h, [o1], TODAY)
    state.record_history(h, [o2], TODAY)
    assert h["1"] == [[TODAY, 700_000, 1_200_000]]
    state.record_history(h, [], TODAY + 200)
    assert h == {}


def test_new_vs_ongoing_and_reprice():
    scored = [scoring.evaluate(offer(dkp=i), [], TODAY, NOW_TS) for i in (1, 2, 3)]
    sent = {"1": {"t": NOW_TS - 3600, "p": 800_000},          # sent 1h ago, same price
            "2": {"t": NOW_TS - 3600, "p": 1_000_000}}        # sent 1h ago, price fell 20%
    new, ongoing = digest.split_new_ongoing(scored, sent, NOW_TS)
    assert {o["dkp"] for o in new} == {2, 3}
    assert [o["dkp"] for o in ongoing] == [1]
    sent["3"] = {"t": NOW_TS - 30 * 3600, "p": 800_000}       # older than 24h -> new again
    new, _ = digest.split_new_ongoing(scored, sent, NOW_TS)
    assert 3 in {o["dkp"] for o in new}


def test_render_escapes_html_and_respects_limit():
    many = []
    for i in range(60):
        many.append(scoring.evaluate(offer(dkp=i, title="<b>تست</b> & " + "ا" * 200,
                                           cat=f"دسته {i % 10}", end=NOW_TS + 3600), [], TODAY, NOW_TS))
    groups = digest.group(many)
    assert len(groups) <= S.MAX_GROUPS and all(len(i) <= S.ITEMS_PER_GROUP for _, i in groups)
    msgs = digest.render(groups, many[:3], NOW, NOW, {"new": 60, "checked": 1182})
    assert all(digest.vlen(m) <= S.MESSAGE_LIMIT + 200 for m in msgs)
    joined = "\n".join(msgs)
    assert "&lt;b&gt;" in joined and "<b>تست</b>" not in joined
    assert "شگفت‌انگیزهای تازه‌ی ساعت 13:00" in msgs[0]
    assert "<blockquote expandable>" in joined  # the rest of each category is collapsed


def test_target_post_time():
    from app.main import target_post_time
    early = datetime(2026, 11, 14, 12, 48, tzinfo=S.TZ)
    assert target_post_time(early).strftime("%H:%M") == "13:00"
    late = datetime(2026, 11, 14, 13, 4, tzinfo=S.TZ)
    assert target_post_time(late) == late
    midnight = datetime(2026, 11, 14, 23, 48, tzinfo=S.TZ)
    assert target_post_time(midnight).hour == 0


def test_end_time_live_format_is_tehran_local():
    # live API sends a zone-less Tehran time string plus `timer` (seconds left)
    from datetime import datetime
    ts = collector._epoch("2026-11-15 00:00:00")
    assert ts == datetime(2026, 11, 15, 0, 0, tzinfo=S.TZ).timestamp()
    assert collector._ends_at({"timer": 3600, "time": "x"}, now_ts=1000) == 4600
    assert collector._ends_at({"timer": 0, "time": "2026-11-15 00:00:00"}) == ts


def test_slot_guard_blocks_second_run_same_hour(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(S, "STATE_DIR", str(tmp_path))
    t = datetime(2026, 11, 14, 13, 0, tzinfo=S.TZ)
    assert not main.slot_done(t)
    main.mark_slot(t, posted=True)
    assert main.slot_done(t)
    assert not main.slot_done(datetime(2026, 11, 14, 14, 0, tzinfo=S.TZ))


def test_end_time_shown_only_when_close():
    soon = scoring.evaluate(offer(end=NOW_TS + 2 * 3600), [], TODAY, NOW_TS)
    late = scoring.evaluate(offer(dkp=2, end=NOW_TS + 11 * 3600), [], TODAY, NOW_TS)
    assert "⏰ تا 15:00" in digest._fmt_item(1, soon, NOW)
    assert "⏰" not in digest._fmt_item(1, late, NOW)
