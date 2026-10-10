import json
from decimal import Decimal

import pytest

from quoteguard.schema import (
    DDP, Candidate, Carton, Claim, Destination, Evidence, Field, Gold, Goods, QuoteTerms,
    LabelRecord, RateTier, SeedQuote, Shipment, Span, Surcharge, Task)


def rt(x):
    """round trip through JSON text to prove the dict is serialisable"""
    d = json.loads(json.dumps(x.to_dict()))
    y = type(x).from_dict(d)
    assert y == x
    y.validate()
    return y


def ship():
    return Shipment("S1", "air", [Carton(2, "60", 50, 40, "15.5")],
                    [Goods("tee", "6109.10.00", "4", 200)],
                    Destination("ONT8", "92337", "commercial", False), 14, "2026-10-09")


def terms():
    return QuoteTerms.from_plain(
        channel="Air-A", mode="air",
        rate_tiers=[RateTier(21, 44, "42", "CNY"), RateTier(45, None, "5.5", "USD")],
        min_kg_shipment=Decimal("21"), min_kg_carton=Decimal("1"), dim_divisor=Decimal("6000"),
        rounding_kg=Decimal("0.5"),
        surcharges=[Surcharge("clr", "per_shpt", "150", "CNY"),
                    Surcharge("long", "per_ctn", "50", "CNY", "ctn_longest_side_gt", 60, True)],
        fuel_pct=Decimal("0.1"), includes=["freight"], excludes=["duty"],
        ddp=DDP(True, "duty", "capped", "3", "forwarder"),
        broker_fee_cny=Decimal("100"), isf_fee_cny=None, transit_days=[7, 10],
        validity=["2026-10-01", "2026-12-31"], liability="RMB 20/kg",
        claims=[Claim("NVOCC", "F01", "123", "US")])


def test_shipment_roundtrip():
    s = rt(ship())
    assert isinstance(s.cartons[0].kg, Decimal) and s.cartons[0].kg == Decimal("15.5")
    assert ship().to_dict()["cartons"][0]["length_cm"] == "60"


def test_terms_roundtrip():
    t = rt(terms())
    assert t.plain("rate_tiers")[1].max_kg is None
    assert t.plain("isf_fee_cny") is None and t.isf_fee_cny.status == "missing"
    assert t.plain("fuel_pct") == Decimal("0.1")
    with pytest.raises(KeyError):
        t.plain("nope")


def test_terms_with_spans():
    t = terms()
    t.channel = Field("Air-A", "stated", [Span("d1", "text", "Air-A", 0, 5)])
    t.dim_divisor = Field(Decimal("6000"), "implied", [Span("d1", "cell", "6000", sheet="S", cell="B2")])
    rt(t)


def test_other_classes_roundtrip():
    rt(Span("d", "region", "q", page=1, bbox=[0, 0, 1, 1]))
    rt(Field("x", "stated", [Span("d", "text", "x", 0, 1)]))
    c = Candidate("c1", "t1", "q1", {"fmt": "text"}, ["a.txt"], True, ["seed1"])
    rt(c)
    task = Task("t1", "dev", "explicit", ship(), [c], "rule1", "snap1", "g1", 7)
    rt(task)
    rt(Evidence("e1", "c1", Claim("NVOCC", "F01"), {"date": "2026-10-09"}, "supported"))
    rt(Gold("t1", {"c1": {"total_cny": "100.00"}},
            {"type": "choose", "choice": "c1", "opt_cost_cny": "100.00"}, [], {"c1": "ab"}))
    rt(SeedQuote("s1", "web_page", "http://x", "2026-10-01", None, "F01", "image", "air", "ab", None, True))


def test_invalid_values():
    with pytest.raises(ValueError):
        Shipment("S", "rail", ship().cartons, ship().goods, ship().dest, 14, "2026-10-09").validate()
    with pytest.raises(ValueError):
        Destination("X", "1", "office", False).validate()
    with pytest.raises(ValueError):
        Carton(0, 1, 1, 1, 1).validate()
    with pytest.raises(ValueError):
        Carton("1", "abc", 1, 1, 1)
    with pytest.raises(ValueError):
        Shipment("S", "air", ship().cartons, ship().goods, ship().dest, 14, "10/09/2026").validate()
    with pytest.raises(ValueError):
        Surcharge("x", "per_kg", 1, "EUR").validate()
    with pytest.raises(ValueError):
        Surcharge("x", "per_ctn", 1, "CNY", "ctn_kg_gt", None).validate()
    with pytest.raises(ValueError):
        RateTier(10, 5, 1, "CNY").validate()
    with pytest.raises(ValueError):
        DDP(True, "duty", "capped", None).validate()
    with pytest.raises(ValueError):
        Span("d", "audio", "q").validate()
    with pytest.raises(ValueError):
        Task("t", "val", "explicit", ship(), [], "r", "s", "g", 1).validate()
    with pytest.raises(ValueError):
        Gold("t", {}, {"type": "ask", "choice": "c1", "opt_cost_cny": None}, [], {}).validate()
    with pytest.raises(ValueError):
        Shipment.from_dict({"shipment_id": "x"})


def test_field_missing_with_value():
    with pytest.raises(ValueError):
        Field("v", "missing").validate()
    with pytest.raises(ValueError):
        Field("v", "guess").validate()
    t = terms()
    t.channel = Field("x", "missing")
    with pytest.raises(ValueError):
        t.validate()


def test_terms_validation():
    t = terms()
    t.mode = Field("rail", "stated")
    with pytest.raises(ValueError):
        t.validate()
    t = terms()
    t.rate_tiers = Field([RateTier(0, 30, 1, "CNY"), RateTier(0, None, 1, "CNY")], "stated")
    with pytest.raises(ValueError):
        t.validate()
    t = terms()
    t.transit_days = Field([10, 7], "stated")
    with pytest.raises(ValueError):
        t.validate()


def seed(**kw):
    base = dict(seed_id="Q16-u1", source_type="web_page", source_url="F05-src-1", captured_at="2026-10-09",
                page_date="2026-09-20", forwarder_alias="F05", modality="text", channel="air", raw_sha256="ab",
                anon_sha256="cd", is_real=True)
    base.update(kw)
    return SeedQuote(**base)


def test_seedquote_new_fields():
    old = rt(SeedQuote("s1", "image", "u", "2026-10-01", None, "F1", "table", "air", "ab", None, True))
    assert old.source_id is None and old.price_form is None
    full = rt(seed(source_id="Q16", source_class="forwarder", publisher_alias="F05",
                   price_form="published_tier", date_status="current", dest_region="US West"))
    assert full.source_class == "forwarder"
    # dicts from before the new fields still load
    d = seed().to_dict()
    for k in ("source_id", "source_class", "publisher_alias", "price_form", "date_status", "dest_region"):
        d.pop(k)
    assert SeedQuote.from_dict(d) == seed()
    for bad in (dict(source_type="web"), dict(source_class="agent"), dict(price_form="fixed"),
                dict(date_status="old"), dict(modality="video")):
        with pytest.raises(ValueError):
            seed(**bad).validate()


def test_ratetier_unit():
    assert RateTier(0, None, 1, "CNY").unit == "per_kg"
    t = rt(RateTier(0, None, 1, "CNY", "per_cbm"))
    assert t.unit == "per_cbm"
    old = RateTier.from_dict({"min_kg": "0", "max_kg": None, "price": "1", "currency": "CNY"})
    assert old.unit == "per_kg"
    with pytest.raises(ValueError):
        RateTier(0, None, 1, "CNY", "per_pallet").validate()


def label_terms():
    t = terms().to_dict()
    from quoteguard.schema import QuoteTerms as QT
    out = QT.from_dict(t)
    d = {n: getattr(out, n) for n in t}
    d["min_kg_shipment"] = Field([Decimal("12"), Decimal("6")], "conflicting",
                                 [Span("d", "text", "min 12kg"), Span("d", "text", "min 6kg")])
    d["rate_tiers"] = Field([[RateTier(0, 30, 40, "CNY")], [RateTier(0, 50, 40, "CNY")]], "conflicting")
    d["validity"] = Field(["2026-10-01", None], "stated")
    return d


def test_label_record_roundtrip():
    rec = LabelRecord("Q16-u1", "A", "2026-10-10", "12.5", None, seed(seed_id="Q16-u1"), label_terms(),
                      {"range_max": "12", "rate_tiers_other": [{"unit": "per_cbm"}]})
    r = rt(rec)
    assert r.minutes_spent == Decimal("12.5")
    assert r.terms["min_kg_shipment"].value == [Decimal("12"), Decimal("6")]
    assert r.terms["rate_tiers"].value[1][0].max_kg == 50
    assert r.has_conflicts()
    clean = {k: v for k, v in label_terms().items() if v.status != "conflicting"}
    rec2 = LabelRecord("Q16-u1", "B", "2026-10-10", 3, "model-x", seed(), clean)
    assert rt(rec2).terms["min_kg_shipment"].status == "missing"


def test_label_record_to_quote_terms():
    with pytest.raises(ValueError):
        LabelRecord("Q16-u1", "A", "2026-10-10", 1, None, seed(), label_terms()).to_quote_terms()
    clean = {k: v for k, v in label_terms().items() if v.status != "conflicting"}
    qt = LabelRecord("Q16-u1", "A", "2026-10-10", 1, None, seed(), clean).to_quote_terms()
    assert isinstance(qt, QuoteTerms)
    qt.validate()
    assert qt.plain("fuel_pct") == Decimal("0.1") and qt.plain("validity") == ["2026-10-01", None]
    assert qt.min_kg_shipment.status == "missing"


def test_label_record_invalid():
    def rec(**kw):
        base = dict(seed_id="Q16-u1", annotator="A", annotated_at="2026-10-10", minutes_spent=1,
                    initial_label_model=None, meta=seed(), terms=label_terms(), notes={})
        base.update(kw)
        return LabelRecord(**base)
    rec().validate()
    with pytest.raises(ValueError):
        rec(seed_id="other").validate()
    with pytest.raises(ValueError):
        rec(annotated_at="yesterday").validate()
    with pytest.raises(ValueError):
        rec(notes=[]).validate()
    for name, bad_field in (
            ("min_kg_shipment", Field([Decimal("12")], "conflicting")),               # wrong length
            ("min_kg_shipment", Field(Decimal("12"), "conflicting")),                 # not a list
            ("min_kg_shipment", Field([Decimal("12"), Decimal("-1")], "conflicting")),  # candidate out of range
            ("rate_tiers", Field([[RateTier(0, 5, 1, "CNY")], [1]], "conflicting"))):
        t = label_terms()
        t[name] = bad_field
        with pytest.raises(ValueError):
            rec(terms=t).validate()
    t = label_terms()
    t["bogus"] = Field(1, "stated")
    with pytest.raises(ValueError):
        rec(terms=t).validate()
