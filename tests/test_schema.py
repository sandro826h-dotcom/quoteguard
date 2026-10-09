import json
from decimal import Decimal

import pytest

from quoteguard.schema import (
    DDP, Candidate, Carton, Claim, Destination, Evidence, Field, Gold, Goods, QuoteTerms,
    RateTier, SeedQuote, Shipment, Span, Surcharge, Task)


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
        claims=[Claim("NVOCC", "FWD-01", "123", "US")])


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
    rt(Evidence("e1", "c1", Claim("NVOCC", "FWD-01"), {"date": "2026-10-09"}, "supported"))
    rt(Gold("t1", {"c1": {"total_cny": "100.00"}},
            {"type": "choose", "choice": "c1", "opt_cost_cny": "100.00"}, [], {"c1": "ab"}))
    rt(SeedQuote("s1", "web", "http://x", "2026-10-01", None, "FWD-01", "image", "air", "ab", None, True))


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
