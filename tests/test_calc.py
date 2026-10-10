import random
import time
from decimal import Decimal as D
from pathlib import Path

import pytest

from quoteguard.calc_py import chargeable_weight, landed_cost
from quoteguard.calc_xlsx import landed_cost_xlsx
from quoteguard.params import load_snapshot
from quoteguard.schema import (DDP, Carton, Destination, Goods, QuoteTerms, RateTier, Shipment,
                               Surcharge)

SNAP_PATH = Path(__file__).resolve().parent.parent / "data" / "params" / "tariff_snapshot_2026-10-09.json"
TOL = D("0.01")
slow = pytest.mark.slow


def q(x):
    return x.quantize(D("0.01"), rounding="ROUND_HALF_UP")


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(SNAP_PATH, {"CNY_per_USD": "7.10", "mpf_informal_usd": "0"})


@pytest.fixture(scope="module")
def snap_plain():
    return load_snapshot(SNAP_PATH)


def dest(res=False, remote=False):
    return Destination("ONT8", "92337", "residential" if res else "commercial", remote)


def both(shipment, terms, snap, tmp_path):
    a = landed_cost(shipment, terms, snap)
    b = landed_cost_xlsx(shipment, terms, snap, tmp_path)
    assert abs(a.total_cny - b["total_cny"]) <= TOL, (a.total_cny, b["total_cny"])
    assert abs(a.duty_floor_cny - b["duty_floor_cny"]) <= TOL
    assert abs(a.per_unit_cny - b["per_unit_cny"]) <= TOL
    assert a.chargeable_kg == b["chargeable_kg"]
    assert [c for c, _ in a.lines] == [c for c, _ in b["lines"]]
    for (c, x), (_, y) in zip(a.lines, b["lines"]):
        assert abs(x - y) <= TOL, (c, x, y)
    assert [c for c, _ in a.contingent] == [c for c, _ in b["contingent"]]
    assert abs(sum((x for _, x in a.contingent), D(0)) - sum((x for _, x in b["contingent"]), D(0))) <= TOL
    for k in ("snapshot_id", "assumptions", "overrides", "defaulted", "fx_date"):
        assert getattr(a, k) == b[k], k
    assert Path(b["workbook"]).exists()
    return a, b


def hand_case():
    s = Shipment("HAND1", "express", [Carton(2, 60, 50, 40, 15)], [Goods("tee", "6109.10.00", 4, 200)],
                 dest(), 10, "2026-10-09")
    t = QuoteTerms.from_plain(
        channel="X", mode="express",
        rate_tiers=[RateTier(21, 44, 42, "CNY"), RateTier(45, 99, 40, "CNY")],
        min_kg_shipment=D(21), dim_divisor=D(6000), rounding_kg=D(1),
        surcharges=[Surcharge("clearance", "per_shpt", 150, "CNY"),
                    Surcharge("oversize", "per_ctn", 50, "CNY", "ctn_longest_side_gt", 60)],
        fuel_pct=D(0), ddp=DDP(False, "duty", "actual"))
    return s, t


def simple(sid, mode="air", cartons=None, goods=None, **kw):
    """One-tier terms with 1 CNY/kg unless overridden; handy for single-feature cases."""
    s = Shipment(sid, mode, cartons or [Carton(1, 30, 20, 20, 10)],
                 goods or [Goods("tee", "6109.10.00", 4, 200)], dest(), 10, "2026-10-09")
    base = dict(channel="X", mode=mode, rate_tiers=[RateTier(0, None, 1, "CNY")],
                dim_divisor=D(6000), rounding_kg=D(1))
    base.update(kw)
    return s, QuoteTerms.from_plain(**base)


# ------------------------------------------------------------------ hand-computed cases
@slow
def test_hand_case(snap, tmp_path):
    s, t = hand_case()
    a, b = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 40
    assert a.line("freight") == D("1680.00")
    assert a.line("duty") == D("2073.20")      # 292 USD * 7.10
    assert a.line("clearance") == D("150.00")
    assert a.line("oversize") == D("0.00")
    assert a.total_cny == D("3903.20")
    assert a.per_unit_cny == D("19.52")
    assert a.duty_floor_cny == D("2073.20")
    assert b["total_cny"] == D("3903.20") and b["per_unit_cny"] == D("19.52")
    # assumed parameters actually used: informal MPF was overridden, so it is reported as an override
    assert a.snapshot_id == "tariff-2026-10-09"
    assert a.assumptions == []
    assert a.overrides == [{"name": "CNY_per_USD", "original": "6.7330", "value": "7.10"},
                           {"name": "mpf_informal_usd", "original": "2.77", "value": "0"}]
    assert a.defaulted == ["min_kg_carton", "broker_fee_cny", "isf_fee_cny"]
    assert a.fx_date == "2026-10-09"


@slow
def test_min_shipment_binds(snap, tmp_path):
    s, t = simple("MS", min_kg_shipment=D(21))
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 21 and a.line("freight") == D("21.00")


@slow
def test_min_carton_binds(snap, tmp_path):
    s, t = simple("MC", cartons=[Carton(3, 20, 20, 20, 2)], min_kg_carton=D(5))
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 15 and a.line("freight") == D("15.00")


@slow
def test_informal_mpf_nonzero_and_no_assumptions(snap_plain, tmp_path):
    # official snapshot: fx 6.7330, informal MPF 2.77 USD (automated, not prepared by CBP)
    # MPF 2.77 * 6.7330 = 18.65041 -> 18.65; duty 292 USD * 6.7330 = 1966.036 -> 1966.04
    s, t = simple("INF")
    a, _ = both(s, t, snap_plain, tmp_path)
    assert a.line("mpf") == D("18.65")
    assert a.line("duty") == D("1966.04")
    assert a.duty_floor_cny == D("1984.69")      # 294.77 * 6.7330 = 1984.686
    assert a.assumptions == []
    assert a.overrides == []
    assert a.fx_date == "2026-10-09"


@slow
def test_fx_override_725(tmp_path):
    sn = load_snapshot(SNAP_PATH, {"CNY_per_USD": "7.25", "mpf_informal_usd": "0"})
    s, t = hand_case()
    a, _ = both(s, t, sn, tmp_path)
    assert a.line("duty") == D("2117.00")      # 292 * 7.25
    assert a.total_cny == D("1680.00") + D("150.00") + D("2117.00")
    assert a.overrides[0] == {"name": "CNY_per_USD", "original": "6.7330", "value": "7.25"}


@slow
def test_tier_by_lower_bound(snap, tmp_path):
    # 44.5 kg falls into the 21 tier because the tier is chosen by its lower bound only
    s, t = simple("TB", cartons=[Carton(1, 30, 20, 20, 44.5)], rounding_kg=D("0.5"),
                  rate_tiers=[RateTier(21, 44, 42, "CNY"), RateTier(45, 99, 40, "CNY")])
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == D("44.5") and a.line("freight") == D("1869.00")


@slow
def test_rounding_float_edge(snap, tmp_path):
    s, t = simple("EDGE", cartons=[Carton(1, "27.1", "30.1", "33.1", 1)], rounding_kg=D("0.5"))
    a, b = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == D("5.0") and b["chargeable_kg"] == D("5.0")


@slow
def test_ddp_included_usd_formal_mpf_contingent(snap, tmp_path):
    # value 8*500 = 4000 USD > 2500; MPF 13.856 -> minimum 33.58 USD
    s = Shipment("C2", "air", [Carton(3, 50, 40, 30, 12), Carton(1, 70, 30, 30, 8)],
                 [Goods("case", "3926.90.99", 8, 500)], dest(res=True), 10, "2026-10-09")
    t = QuoteTerms.from_plain(
        channel="Air", mode="air",
        rate_tiers=[RateTier(0, 49, "6.2", "USD"), RateTier("49.5", None, "5.9", "USD")],
        dim_divisor=D(5000), rounding_kg=D("0.5"), min_kg_carton=D(10),
        surcharges=[Surcharge("resi", "per_ctn", "2", "USD", "residential"),
                    Surcharge("remote", "per_kg", "1", "CNY", "remote"),
                    Surcharge("exam", "per_shpt", "300", "CNY", contingent=True)],
        fuel_pct=D("0.15"), ddp=DDP(True, "duty", "included", None, "forwarder"),
        broker_fee_cny=D(80), isf_fee_cny=D(35))
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 49
    assert a.line("freight") == q(D("49") * D("6.2") * D("7.10"))
    assert a.line("duty") == 0
    assert a.line("mpf") == q(D("34.58") * D("7.10"))
    assert a.line("hmf") == 0
    assert a.line("resi") == q(D("2") * 4 * D("7.10"))
    assert a.line("remote") == 0
    assert a.contingent == [("exam", D("300.00"))]
    assert a.duty_floor_cny == q((D("4000") * D("0.253") + D("34.58")) * D("7.10"))
    assert a.defaulted == ["min_kg_shipment"]


@slow
def test_sea_hmf_ddp_capped_mpf_max(snap, tmp_path):
    # value 40*5000 = 200000 USD -> MPF capped at 670.86
    s = Shipment("C3", "sea", [Carton(10, 100, 100, 100, 100)],
                 [Goods("bag", "4202.92.31", 40, 5000)], dest(), 40, "2026-10-09")
    t = QuoteTerms.from_plain(
        channel="Sea", mode="sea", rate_tiers=[RateTier(0, None, 1.5, "CNY")],
        dim_divisor=D(1000), rounding_kg=D(1), min_kg_shipment=D(100),
        surcharges=[Surcharge("handling", "pct_freight", "0.05", None)],
        fuel_pct=D("0.1"), ddp=DDP(True, "duty_mpf", "capped", "20", "forwarder"))
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 10 * 1000
    assert a.line("freight") == D("15000.00") and a.line("fuel") == D("1500.00")
    assert a.line("handling") == D("750.00")
    assert a.line("hmf") == D("1775.00")
    duty = D("200000") * D("0.551") * D("7.10")
    assert a.line("duty") == q(duty - 20 * 10000)
    assert a.line("mpf") == q(D("670.86") * D("7.10"))
    assert a.duty_floor_cny == q(duty + D("670.86") * D("7.10"))


@slow
def test_sea_duty_mpf_included_hmf_stays(snap, tmp_path):
    s, t = simple("SDM", mode="sea", goods=[Goods("bag", "4202.92.31", 40, 5000)],
                  ddp=DDP(True, "duty_mpf", "included"))
    a, _ = both(s, t, snap, tmp_path)
    assert a.line("duty") == 0 and a.line("mpf") == 0
    assert a.line("hmf") == D("1775.00")      # 200000 * 0.00125 * 7.10
    assert a.total_cny == D("10.00") + D("1775.00")
    assert a.duty_floor_cny == q(D("200000") * D("0.551") * D("7.10") + D("670.86") * D("7.10"))


@slow
def test_ddp_all_scope_waives_everything(snap, tmp_path):
    s = Shipment("C4", "sea", [Carton(4, 60, 40, 40, 30)], [Goods("toy", "9503.00.00", 5, 600)],
                 dest(remote=True), 40, "2026-10-09")
    t = QuoteTerms.from_plain(
        channel="Sea", mode="sea", rate_tiers=[RateTier(0, None, 2, "CNY")],
        dim_divisor=D(6000), rounding_kg=D(1),
        surcharges=[Surcharge("heavy", "per_ctn", 30, "CNY", "ctn_kg_gt", 25),
                    Surcharge("remote", "per_kg", "0.5", "CNY", "remote")],
        ddp=DDP(True, "all", "included"))
    a, _ = both(s, t, snap, tmp_path)
    assert a.chargeable_kg == 120
    assert a.line("heavy") == 120 and a.line("remote") == 60
    assert a.line("duty") == a.line("mpf") == a.line("hmf") == 0
    assert a.total_cny == D("240") + 120 + 60
    assert a.duty_floor_cny > 0


@slow
def test_cap_exceeds_duty(snap, tmp_path):
    # duty = 800*0.365*7.10 = 2073.20 < cap 100/kg * 40 kg = 4000, seller duty 0, MPF/HMF unchanged
    s, t = hand_case()
    t.ddp.value = DDP(True, "duty", "capped", "100")
    a, _ = both(s, t, snap, tmp_path)
    assert a.line("duty") == 0
    assert a.total_cny == D("1680.00") + D("150.00")
    assert a.duty_floor_cny == D("2073.20")


# ------------------------------------------------------------------ errors (calculator A only)
def test_no_tier_below_lowest(snap):
    s, t = hand_case()
    t.rate_tiers.value = [RateTier(50, None, 40, "CNY")]
    with pytest.raises(ValueError):
        landed_cost(s, t, snap)


@slow
def test_no_tier_below_lowest_xlsx(snap, tmp_path):
    s, t = hand_case()
    t.rate_tiers.value = [RateTier(50, None, 40, "CNY")]
    with pytest.raises(ValueError):
        landed_cost_xlsx(s, t, snap, tmp_path)


def test_missing_required_term(snap, tmp_path):
    s, t = hand_case()
    t.dim_divisor = QuoteTerms().dim_divisor
    with pytest.raises(ValueError):
        landed_cost(s, t, snap)
    with pytest.raises(ValueError):
        landed_cost_xlsx(s, t, snap, tmp_path)   # fails before soffice runs


def test_unknown_hts_keyerror(snap, tmp_path):
    s, t = hand_case()
    s.goods[0].hts = "0101.01.01"
    with pytest.raises(KeyError):
        landed_cost(s, t, snap)
    with pytest.raises(KeyError):
        landed_cost_xlsx(s, t, snap, tmp_path)   # fails before soffice runs


def test_mode_mismatch(snap, tmp_path):
    s, t = hand_case()
    s.mode = "air"
    with pytest.raises(ValueError):
        landed_cost(s, t, snap)
    with pytest.raises(ValueError):
        landed_cost_xlsx(s, t, snap, tmp_path)


def test_cross_field_validation():
    with pytest.raises(ValueError):
        DDP(False, "duty", "included").validate()
    DDP(False, "duty", None).validate()
    with pytest.raises(ValueError):
        QuoteTerms.from_plain(fuel_pct=D("1.5")).validate()
    with pytest.raises(ValueError):
        Surcharge("x", "pct_freight", D("5"), None).validate()
    Surcharge("x", "pct_freight", D("0.05"), None).validate()
    with pytest.raises(ValueError):
        QuoteTerms.from_plain(rate_tiers=[RateTier(0, 5, 1, "CNY"), RateTier(0, None, 2, "CNY")]).validate()


def test_defaulted_names():
    assert QuoteTerms().defaulted() == ["min_kg_shipment", "min_kg_carton", "surcharges", "fuel_pct", "ddp",
                                        "broker_fee_cny", "isf_fee_cny"]


@slow
def test_unrecalculated_workbook_raises(snap, tmp_path, monkeypatch):
    import quoteguard.calc_xlsx as cx
    s, t = hand_case()

    def fake(src, outdir):          # copy without recalculating
        out = outdir / src.name
        out.write_bytes(src.read_bytes())
        return out
    monkeypatch.setattr(cx, "_recalc", fake)
    with pytest.raises(RuntimeError):
        cx.landed_cost_xlsx(s, t, snap, tmp_path)


@slow
def test_workbook_names_distinct(snap, tmp_path):
    s, t = hand_case()
    b1 = landed_cost_xlsx(s, t, snap, tmp_path)
    t.fuel_pct.value = D("0.1")
    b2 = landed_cost_xlsx(s, t, snap, tmp_path)
    assert b1["workbook"] != b2["workbook"] and Path(b1["workbook"]).exists()
    assert not list(tmp_path.glob(".lo_profile_*"))


def random_case(rng, i):
    mode = rng.choice(["express", "air", "sea"])
    directed = i % 8 == 3          # directed case: sea + DDP duty_mpf included
    if directed:
        mode = "sea"
    cartons = [Carton(rng.randint(1, 5), rng.randint(20, 90), rng.randint(20, 70), rng.randint(15, 60),
                      D(rng.randint(30, 400)) / 10) for _ in range(rng.randint(1, 3))]
    small = i % 8 == 5             # directed case: light small cartons with a per-carton minimum
    if small:
        cartons = [Carton(2, 20, 20, 20, 2)]
    goods = [Goods("g", rng.choice(["6109.10.00", "3926.90.99", "4202.92.31", "9503.00.00"]),
                   D(rng.randint(100, 4000)) / 100, rng.randint(20, 3000)) for _ in range(rng.randint(1, 3))]
    r = rng.random()
    if r < 0.25:  # push customs value above the MPF maximum
        goods[0].qty = 9000
        goods[0].unit_value_usd = D("30")
    elif r < 0.5:  # informal entry
        goods = [Goods("g", goods[0].hts, D(rng.randint(100, 1500)) / 100, rng.randint(20, 100))]
    price = lambda lo, hi: D(rng.randint(lo, hi)) / 10
    cur = rng.choice(["CNY", "USD"])
    tiers = [RateTier(0, 30, price(300, 600) if cur == "CNY" else price(40, 80), cur),
             RateTier("30.01", 100, price(250, 500) if cur == "CNY" else price(30, 70), cur),
             RateTier("100.01", None, price(200, 400) if cur == "CNY" else price(25, 60), cur)]
    sur = []
    for j in range(rng.randint(0, 4)):
        basis = rng.choice(["per_kg", "per_ctn", "per_shpt", "pct_freight"])
        cond = rng.choice(["always", "residential", "remote", "ctn_longest_side_gt", "ctn_kg_gt"])
        if cond.startswith("ctn_"):
            basis = "per_ctn"
        amt = D("0.05") if basis == "pct_freight" else price(5, 400)
        thr = {"ctn_longest_side_gt": D(rng.randint(40, 80)), "ctn_kg_gt": D(rng.randint(10, 30))}.get(cond)
        cur = None if basis == "pct_freight" and rng.random() < 0.5 else rng.choice(["CNY", "USD"])
        sur.append(Surcharge(f"s{j}", basis, amt, cur, cond, thr, rng.random() < 0.3))
    ddp = DDP(True, rng.choice(["duty", "duty_mpf", "all"]), rng.choice(["included", "actual", "capped"]),
              D(rng.randint(0, 60)), "forwarder") if rng.random() < 0.6 else DDP(False, "duty", "actual")
    if directed:
        ddp = DDP(True, "duty_mpf", "included")
    terms = QuoteTerms.from_plain(
        channel="R", mode=mode, rate_tiers=tiers,
        min_kg_shipment=D(1500) if i % 8 == 6 else D(rng.choice([10, 21, 45])) if rng.random() < 0.5 else None,
        min_kg_carton=D(12) if small else D(rng.choice([1, 5, 12])) if rng.random() < 0.5 else None,
        dim_divisor=D(rng.choice([5000, 6000, 8000])), rounding_kg=D(rng.choice(["0.5", "1", "2"])),
        surcharges=sur, fuel_pct=D(rng.choice(["0", "0.08", "0.15"])), ddp=ddp,
        broker_fee_cny=D(rng.randint(0, 300)) if rng.random() < 0.5 else None,
        isf_fee_cny=D(rng.randint(0, 100)) if rng.random() < 0.5 else None)
    ship = Shipment(f"R{i}", mode, cartons, goods, dest(rng.random() < 0.5, rng.random() < 0.5), 20, "2026-10-09")
    return ship, terms


@slow
def test_random_consistency(snap, tmp_path):
    rng = random.Random(20261009)
    keys = ["sea", "res", "remote", "contingent", "capped", "included", "mpf_max", "informal",
            "min_shipment_binds", "min_carton_binds", "tier0", "duty_mpf_included_sea"]
    seen = dict.fromkeys(keys, 0)
    t0 = time.time()
    n = 40
    for i in range(n):
        s, t = random_case(rng, i)
        a, b = both(s, t, snap, tmp_path)
        ddp = t.ddp.value
        value = sum((g.unit_value_usd * g.qty for g in s.goods), D(0))
        seen["sea"] += s.mode == "sea"
        seen["res"] += s.dest.addr_type == "residential"
        seen["remote"] += s.dest.remote
        seen["contingent"] += bool(a.contingent)
        seen["capped"] += ddp.is_ddp and ddp.duty_terms == "capped"
        seen["included"] += ddp.is_ddp and ddp.duty_terms == "included"
        seen["mpf_max"] += value * D("0.003464") > D("670.86")
        seen["informal"] += value <= D("2500")
        t_nomin = QuoteTerms.from_dict(t.to_dict())
        t_nomin.min_kg_shipment.value = None
        t_nomin.min_kg_shipment.status = "missing"
        seen["min_shipment_binds"] += chargeable_weight(s, t_nomin) < a.chargeable_kg
        mc = t.plain("min_kg_carton")
        seen["min_carton_binds"] += mc is not None and any(
            max(c.kg, c.length_cm * c.width_cm * c.height_cm / t.plain("dim_divisor")) < mc for c in s.cartons)
        seen["tier0"] += a.chargeable_kg <= 30
        seen["duty_mpf_included_sea"] += (s.mode == "sea" and ddp.is_ddp and ddp.scope == "duty_mpf"
                                          and ddp.duty_terms == "included")
    assert all(v > 0 for v in seen.values()), str(seen)
    print(f"\n{n} xlsx recalculations in {time.time() - t0:.1f}s", seen)
