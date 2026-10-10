from decimal import Decimal as D
from pathlib import Path

import pytest

from quoteguard.gate import claim_status, ddp_check, freight_reference, zone_of
from quoteguard.params import load_snapshot
from quoteguard.schema import (DDP, Carton, Claim, Destination, Field, Goods, QuoteTerms, RateTier, Shipment)

SNAP_PATH = Path(__file__).resolve().parent.parent / "data" / "params" / "tariff_snapshot_2026-10-09.json"
OV = {"CNY_per_USD": "7", "mpf_informal_usd": "3", "hmf_rate": "0.00125"}
LEAF = lambda v, st="calibrated": {"value_cny_per_kg": v, "status": st, "source_seed_ids": []}
FLOOR = {"express": {"west": LEAF(10), "national": LEAF(10)},
         "sea": {"west": LEAF(3), "national": LEAF(3)}}


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(SNAP_PATH, OV)


def case(mode, price, zip_="90001"):
    """2 cartons 50x40x30, 20 kg each -> 40 kg chargeable; 200 tees at 5 USD = 1000 USD.
    duty 365 USD = 2555 CNY, informal MPF 3 USD = 21 CNY (fx 7); sea HMF 1.25 USD = 8.75 CNY."""
    s = Shipment("G1", mode, [Carton(2, 50, 40, 30, 20)], [Goods("tee", "6109.10.00", 5, 200)],
                 Destination("X", zip_, "commercial", False), 10, "2026-10-12")
    t = QuoteTerms.from_plain(channel="X", mode=mode, rate_tiers=[RateTier(0, None, price, "CNY")],
                              dim_divisor=D(5000), rounding_kg=D(1),
                              ddp=DDP(True, "all", "included", None, "forwarder"))
    return s, t


def test_snapshot_assumption(snap):
    assert snap.total_duty_rate("6109.10.00") == D("0.365")


def test_zone_of():
    d = lambda z: Destination("X", z, "commercial", False)
    assert [zone_of(d(z)) for z in ("08691", "30213", "46075", "79101", "85323", "99501")] == \
        ["east", "east", "central", "central", "west", "west"]


# floor (express) = (2555 + 21) / 40 = 64.4 ; verify threshold = 64.4 + 10 = 74.4
@pytest.mark.parametrize("price,level", [(60, "high"), (70, "verify"), (80, "none")])
def test_express_levels(snap, price, level):
    s, t = case("express", price)
    r = ddp_check(s, t, snap, FLOOR)
    assert r.floor_cny_per_kg == D("64.4")
    assert r.quote_cny_per_kg == D(price)
    assert r.level == level
    assert r.floor_source == "zone"
    assert r.docs_to_request == ([] if level == "none" else
                                 ["进口商主体（IOR）名称", "CBP 7501 报关单", "申报价值发票"])


def test_sea_hmf_in_floor(snap):
    # sea floor = (2555 + 21 + 8.75) / 40 = 64.61875 ; without HMF a price of 64.5 would not be high
    s, t = case("sea", D("64.5"))
    r = ddp_check(s, t, snap, FLOOR)
    assert r.floor_cny_per_kg == D("64.61875")
    assert r.level == "high"
    assert r.docs_to_request[-1] == "ISF 申报确认"
    s, t = case("sea", D("64.62"))
    r = ddp_check(s, t, snap, FLOOR)
    assert r.level == "verify"          # 64.62 < 64.61875 + 3
    s, t = case("sea", D("67.7"))
    assert ddp_check(s, t, snap, FLOOR).level == "none"


def test_non_ddp_is_none(snap):
    s, t = case("express", 1)
    t.ddp.value = DDP(False, "duty", "actual")
    assert ddp_check(s, t, snap, FLOOR).level == "none"


def test_quote_price_includes_own_surcharges(snap):
    from quoteguard.schema import Surcharge
    s, t = case("express", 62)
    t.surcharges = Field([Surcharge("X", "per_shpt", 160, "CNY")], "stated")   # +4 CNY/kg -> 66
    r = ddp_check(s, t, snap, FLOOR)
    assert r.quote_cny_per_kg == D(66) and r.level == "verify"


def test_scope_duty_excludes_seller_borne_mpf(snap):
    s, t = case("express", 70)
    t.ddp.value = DDP(True, "duty", "included", None, "forwarder")
    # seller still pays MPF (21), which is not part of the quote's own price
    assert ddp_check(s, t, snap, FLOOR).quote_cny_per_kg == D(70)


# ---- freight reference fallbacks
def test_fallback_zone_national_none(snap):
    s, t = case("express", 70, "10001")          # east: no east entry -> national
    r = ddp_check(s, t, snap, FLOOR)
    assert r.floor_source == "national" and r.freight_ref_cny_per_kg == D(10) and r.level == "verify"
    ff = {"express": {"east": None, "west": LEAF(10), "national": LEAF("12.5")}}
    assert freight_reference(ff, "express", "east") == (D("12.5"), "national")
    ff = {"express": {"east": LEAF(None, "no_qualifying_units"), "national": LEAF(None, "no_qualifying_units")}}
    s, t = case("express", 70, "10001")
    r = ddp_check(s, t, snap, ff)                # mode without values: only high or none
    assert (r.level, r.floor_source) == ("none", "none")
    s, t = case("express", 60, "10001")
    assert ddp_check(s, t, snap, ff).level == "high"
    assert freight_reference({"sea": None}, "sea", "west") == (None, "none")


def test_missing_freight_floor_raises(snap):
    s, t = case("express", 70)
    with pytest.raises(ValueError):
        ddp_check(s, t, snap, {"air": {}})
    with pytest.raises(ValueError):
        ddp_check(s, t, snap, {"express": {"west": None, "east": LEAF(5)}})   # no national, other zones set


# ---- claim evidence
REG = {
    "fmc": [{"name": "SYN-A", "license_no": "L1", "oti_type": "NVOCC", "status": "active"},
            {"name": "SYN-B", "license_no": "L2", "oti_type": "NVOCC", "status": "revoked"},
            {"name": "SYN-C", "license_no": "L3", "oti_type": "OFF", "status": "active"}],
    "cn": [{"name": "SYN-D", "reg_no": "M1", "cert_type": "mot_nvocc_filing", "status": "active"},
           {"name": "SYN-E", "reg_no": "A1", "cert_type": "aeo_advanced", "status": "active"}],
}


def test_claim_supported_both_databases_searched():
    # jurisdiction unstated: a hit in either database is enough
    assert claim_status(Claim("NVOCC", "SYN-A"), "sea", REG, "SYN-A") == "supported"
    assert claim_status(Claim("NVOCC", "SYN-D"), "sea", REG, "SYN-D") == "supported"
    assert claim_status(Claim("FMC_OTI", "SYN-A", "L1", "US"), "sea", REG, "SYN-A") == "supported"


def test_claim_insufficient_when_no_hit():
    assert claim_status(Claim("NVOCC", "SYN-Z"), "sea", REG, "SYN-Z") == "insufficient"
    # only the other jurisdiction would hit, but a stated jurisdiction limits the search
    assert claim_status(Claim("NVOCC", "SYN-D", None, "US"), "sea", REG, "SYN-D") == "insufficient"


def test_claim_contradicted():
    assert claim_status(Claim("FMC_OTI", "SYN-B"), "sea", REG, "SYN-B") == "contradicted"   # revoked
    assert claim_status(Claim("NVOCC", "SYN-C"), "sea", REG, "SYN-C") == "contradicted"     # wrong type
    assert claim_status(Claim("FMC_OTI", "SYN-A", "WRONG", "US"), "sea", REG, "SYN-A") == "contradicted"


def test_claim_irrelevant():
    assert claim_status(Claim("AEO_CN", "SYN-E"), "sea", REG, "SYN-E") == "irrelevant"
    assert claim_status(Claim("FMC_OTI", "SYN-A"), "air", REG, "SYN-A") == "irrelevant"
    assert claim_status(Claim("NVOCC", "SYN-A"), "express", REG, "SYN-A") == "irrelevant"
    assert claim_status(Claim("FMC_OTI", "SYN-A"), "sea", REG, "SYN-Q") == "irrelevant"    # entity mismatch
