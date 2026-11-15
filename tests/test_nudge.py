"""One-line nudges: only true facts, LLM output checked, instant switch between providers."""
from datetime import datetime

from app import digest, nudge, scoring
from app import settings as S
from tests.test_core import NOW, NOW_TS, TODAY, offer


def _o(**kw):
    r = offer()
    r.update(kw)
    return scoring.enrich(r, [], TODAY, NOW_TS)


def test_rule_lines_use_real_signals_only():
    assert "فقط 3 عدد" in nudge.rule_line(_o(stock=3), NOW_TS)
    assert "82%" in nudge.rule_line(_o(sold_pct=82), NOW_TS)
    plain = nudge.rule_line(_o(), NOW_TS)
    assert "عدد" not in plain and "فروش رفته" not in plain


def test_llm_output_with_invented_numbers_is_rejected():
    allowed = "33 800000 400 هزار"
    assert nudge.clean("«همین الان 33% ارزان‌تر است»", allowed) == "همین الان 33% ارزان‌تر است"
    assert nudge.clean("فقط 2 عدد مانده!", allowed) is None          # 2 is not in the facts
    assert nudge.clean("Buy now", allowed) is None                    # not Persian
    assert nudge.clean("ببین https://x.y", allowed) is None


def test_router_switches_instantly_and_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    nudge._down.clear()
    tried = []

    def fake(p, key, prompt):
        tried.append(p["name"])
        if p["kind"] == "gemini":
            raise TimeoutError
        return "این یکی را از دست نده."

    monkeypatch.setattr(nudge, "_call", fake)
    assert nudge.llm_line({"discount": 33}) == "این یکی را از دست نده."
    assert tried[:2] == ["gemini-flash", "gemini-flash-lite"] and "groq" in tried
    monkeypatch.setattr(nudge, "_call", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    nudge._down.clear()
    assert nudge.offer_line(_o(stock=2), NOW_TS)  # all down -> rule line, never empty


def test_last_chance_strip_and_share_button(monkeypatch):
    items = [_o(dkp=1, stock=2), _o(dkp=2), _o(dkp=3, sold_pct=90)]
    urgent = digest.last_chance(items, NOW)
    assert [o["dkp"] for o in urgent] == [1, 3]
    msgs = digest.render([], [], NOW, NOW, {"new": 3, "checked": 10}, urgent, "یک جمله")
    assert "⏳ <b>رو به اتمام</b>" in msgs[0] and "<i>یک جمله</i>" in msgs[0]
    monkeypatch.setattr(S, "CHANNEL_ID", "@dk_deals")
    assert len(digest.channel_markup("bot", share=True)["inline_keyboard"]) == 2
    assert len(digest.channel_markup("bot", share=False)["inline_keyboard"]) == 1
    _ = datetime
