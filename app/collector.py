"""Fetch the live incredible-offers list from Digikala's public JSON API.

One function does the work: crawl() -> (offers, expected_total).
Each offer is a plain dict (see parse_product). Nothing is stored here.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import requests

from . import settings as S

log = logging.getLogger("collector")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}


def fetch_page(session: requests.Session, page: int, url: str | None = None) -> dict | None:
    for attempt in range(1, S.MAX_RETRIES + 1):
        try:
            resp = session.get(url or S.API_URL, params={"page": page}, timeout=S.REQUEST_TIMEOUT)
            if resp.status_code == 429:
                time.sleep(5 * attempt)
                continue
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("page %s attempt %s/%s failed: %s", page, attempt, S.MAX_RETRIES, exc)
            time.sleep(2 ** attempt)
    log.error("page %s: giving up", page)
    return None


def _toman(value) -> int | None:
    try:
        return int(value) // S.PRICE_DIVISOR
    except (TypeError, ValueError):
        return None


def _rating5(rate) -> float | None:
    """Digikala lists ratings out of 100 in some responses and out of 5 in others."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return None
    return r / 20 if r > 5 else r


def _epoch(value) -> float | None:
    """Deal end time: accepts epoch seconds, epoch milliseconds or an ISO string.

    Verified on the live API (2026-11-14): `time` is a string like
    "2026-11-15 00:00:00" in Tehran local time with no zone, so a naive
    string is read as Tehran time (not the runner's UTC).
    """
    if value in (None, "", 0):
        return None
    try:
        v = float(value)
        return v / 1000 if v > 1e12 else v
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=S.TZ)
    return dt.timestamp()


def _ends_at(price: dict, now_ts: float | None = None) -> float | None:
    """Prefer `timer` (seconds left, verified live); fall back to `time`."""
    timer = price.get("timer")
    try:
        if timer is not None and int(timer) > 0:
            return (now_ts if now_ts is not None else time.time()) + int(timer)
    except (TypeError, ValueError):
        pass
    return _epoch(price.get("time"))


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extras(p: dict, variant: dict) -> dict:
    """Seller and delivery facts used by personal rules (all verified on the live API)."""
    seller = variant.get("seller") or {}
    vprops = variant.get("properties") or {}
    providers = (variant.get("shipment_methods") or {}).get("providers") or []
    modes = {pr.get("shipping_mode") for pr in providers}
    try:
        stars = float(seller.get("stars")) if seller.get("stars") is not None else None
    except (TypeError, ValueError):
        stars = None
    main_img = ((p.get("images") or {}).get("main") or {}).get("url") or []
    return {
        "image": main_img[0] if isinstance(main_img, list) and main_img else "",
        "seller_stars": stars,                                          # out of 5
        "official": bool((seller.get("properties") or {}).get("is_official")),
        "jet": "jet" in modes,                                          # fast same-day (Tehran/Karaj)
        "nearby": bool(vprops.get("nearby_seller_shipment")),           # 3-hour nearby delivery
        "dk_stock": bool(vprops.get("in_digikala_warehouse")),
        "lowest_30d": bool(variant.get("has_best_price_in_last_month")),
        "sold_pct": _int((variant.get("price") or {}).get("sold_percentage")),   # share of the offer stock sold
        "stock": _int((variant.get("price") or {}).get("marketable_stock")),     # units left (only when low)
    }


def parse_product(p: dict) -> dict:
    variant = p.get("default_variant") or {}
    price = variant.get("price") or {}
    layer = p.get("data_layer") or {}
    rating = p.get("rating") or {}
    uri = (p.get("url") or {}).get("uri") or ""
    return {
        "dkp": p.get("id"),
        "title": (p.get("title_fa") or "").strip(),
        "brand": layer.get("brand") or "",
        "cat1": layer.get("item_category2") or "",
        "cat2": layer.get("item_category3") or "",
        "price": _toman(price.get("selling_price")),
        "rrp": _toman(price.get("rrp_price")),
        "declared_discount": price.get("discount_percent") or 0,
        "status": p.get("status") or "",
        "seller": (variant.get("seller") or {}).get("title", ""),
        "rating": _rating5(rating.get("rate")),
        "votes": int(rating.get("count") or 0),
        "ends_at": _ends_at(price),
        "lightning": bool(price.get("is_lightening_deal")),
        **_extras(p, variant),
        # short product link (verified to open the same page): keeps messages compact
        "url": f"https://www.digikala.com/product/dkp-{p.get('id')}/" if p.get("id") else
               (f"https://www.digikala.com{uri}" if uri else ""),
    }


def find_listing(data: dict, list_key: str | None) -> dict:
    """Return the {products, pager} block of a response.

    With list_key the block is taken by name; without it (unknown landing pages)
    the first dict that holds a non-empty "products" list is used.
    """
    if list_key:
        return data.get(list_key) or {}
    stack = [data]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            if isinstance(node.get("products"), list) and node["products"]:
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(x for x in node if isinstance(x, (dict, list)))
    return {}


def crawl() -> tuple[list[dict], int]:
    """Crawl every source. Returns (offers, expected total of the FIRST source)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    offers: list[dict] = []
    seen: set = set()
    first_expected = 0
    for i, src in enumerate(S.SOURCES):
        found, expected = crawl_source(session, src, seen)
        offers += found
        if i == 0:
            first_expected = expected
        log.info("source %s: %s new unique products", src["name"], len(found))
    log.info("collected %s unique products from %s source(s)", len(offers), len(S.SOURCES))
    return offers, first_expected


def crawl_source(session: requests.Session, src: dict, seen: set) -> tuple[list[dict], int]:
    offers: list[dict] = []
    page, total_pages, expected = 1, None, 0

    while page <= S.MAX_PAGES:
        data = fetch_page(session, page, src["url"])
        if data is None:
            page += 1
            if total_pages and page > total_pages:
                break
            continue
        listing = find_listing(data, src.get("list_key"))
        products = listing.get("products") or []
        pager = listing.get("pager") or {}
        if total_pages is None and pager.get("total_pages"):
            total_pages = int(pager["total_pages"])
            expected = int(pager.get("total_items") or 0)
            log.info("%s pager: %s products, %s pages", src["name"], expected, total_pages)
        if not products:
            break
        for raw in products:
            dkp = raw.get("id")
            if dkp in seen:
                continue
            seen.add(dkp)
            item = parse_product(raw)
            item["source"] = src["name"]
            offers.append(item)
        if total_pages and page >= total_pages:
            break
        page += 1
        time.sleep(S.DELAY_BETWEEN_PAGES)

    return offers, expected
