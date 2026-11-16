"""The amazing/v1 listing (live since 2026-11-16) + product-detail completion, on real samples."""
import json
from pathlib import Path

from app import collector
from app import settings as S

SAMPLE = json.loads((Path(__file__).parent / "amazing_sample.json").read_text(encoding="utf-8"))


def test_listing_parses_slim_products():
    items, pager = collector.amazing_listing(SAMPLE["listing"]["data"])
    assert pager["total_items"] == 1240 and len(items) == 2
    o = collector.parse_amazing(items[0], now_ts=1000)
    assert o["dkp"] == 22956741 and o["price"] == 2_990_000 and o["rrp"] == 8_900_000
    assert o["declared_discount"] == 66 and o["ends_at"] == 1000 + 8400
    assert o["rating"] == 80.36363636363635 / 20 and o["votes"] == 55 and o["sold_pct"] == 14
    assert o["url"] == "https://www.digikala.com/product/dkp-22956741/" and o["image"]


def test_crawl_and_complete(monkeypatch):
    monkeypatch.setattr(S, "SOURCES", [{"name": "incredible", "kind": "amazing", "url": "u"}])
    monkeypatch.setattr(collector, "fetch_page",
                        lambda s, page, url=None: SAMPLE["listing"]["data"] if page == 1 else None)
    monkeypatch.setattr(S, "MAX_PAGES", 1)
    det = collector.parse_product(SAMPLE["det"]["data"]["product"])
    monkeypatch.setattr(collector, "_detail", lambda dkp: det if dkp == 22956741 else None)
    offers, expected = collector.crawl()
    assert expected == 1240 and len(offers) == 2
    a, b = offers
    assert a["brand"] == "والر" and a["cat1"] == "آرایشی و بهداشتی" and a["seller_stars"] == 4.25
    assert a["detail"] and a["price"] == 2_990_000          # listing price kept
    assert not b["detail"] and b["cat1"] == ""              # detail missing: listing data kept
