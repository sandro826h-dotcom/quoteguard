from decimal import Decimal as D
from pathlib import Path

import pytest

from quoteguard.decide import CandView, TaskView, decide, regret
from quoteguard.params import load_snapshot
from quoteguard.schema import (DDP, Carton, Destination, Field, Gold, Goods, QuoteTerms, RateTier, Shipment)

SNAP_PATH = Path(__file__).resolve().parent.parent / "data" / "params" / "tariff_snapshot_2026-10-09.json"
FLOOR = {"express": {"national": {"value_cny_per_kg": 10, "status": "calibrated", "source_seed_ids": []}}}
VS = {"dim_divisor": [5000, 6000], "fuel_pct": [0, 0.1, 0.2],
      "ddp": [{"id": "none", "is_ddp": False, "scope": "duty", "duty_terms": "actual", "ior": "seller"},
              {"id": "all", "is_ddp": True, "scope": "all", "duty_terms": "included", "ior": "forwarder"}]}


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(SNAP_PATH, {"CNY_per_USD": "7", "mpf_informal_usd": "3"})


def ship(deadline=10):
    # 1 carton 60x50x40, 10 kg: 20 kg at /6000, 24 kg at /5000; goods 10 USD -> duty and MPF are the same for all quotes
    return Shipment("S", "express", [Carton(1, 60, 50, 40, 10)], [Goods("tee", "6109.10.00", 1, 10)],
                    Destination("X", "90001", "commercial", False), deadline, "2026-10-12")


def cand(cid, price, div=6000, ddp=None, transit=(3, 5), fuel=0):
    t = QuoteTerms.from_plain(channel=cid, mode="express", rate_tiers=[RateTier(0, None, price, "CNY")],
                              dim_divisor=D(div), rounding_kg=D(1), fuel_pct=D(str(fuel)),
                              ddp=ddp or DDP(False, "duty", "actual"), transit_days=list(transit))
    return CandView(cid, t)


def view(snap, cands, deadline=10):
    return TaskView(ship(deadline), cands, snap, FLOOR, VS)


def test_cheapest_feasible_chosen(snap):
    d = decide(view(snap, [cand("A", 42), cand("B", 38)]))
    assert (d.type, d.choice, d.ask_fields) == ("choose", "B", [])
    assert d.opt_cost_cny == D("760.00") + D("25.55") + D("21.00") * 0 + D("0") or d.opt_cost_cny > 0
    # total = freight 760 + duty 10*0.365*7 = 25.55 + informal MPF 21
    assert d.opt_cost_cny == D("806.55")


def test_high_ddp_excluded_from_feasible_set(snap):
    # DDP quote far below the tax floor (25.55+21)/20 = 2.3275 CNY/kg is "high": excluded although cheapest
    ddp = DDP(True, "all", "included", None, "forwarder")
    d = decide(view(snap, [cand("D", 1, ddp=ddp), cand("A", 42), cand("B", 50)]))
    assert d.choice == "A"


def test_verify_level_stays_feasible(snap):
    # floor 2.3275; price 8 -> quote 8 > floor but < floor + 10 -> verify, kept in the feasible set
    ddp = DDP(True, "all", "included", None, "forwarder")
    d = decide(view(snap, [cand("D", 8, ddp=ddp), cand("A", 42)]))
    assert d.choice == "D"


def test_timeout_excluded(snap):
    d = decide(view(snap, [cand("A", 42, transit=(3, 5)), cand("B", 30, transit=(5, 12))], deadline=10))
    assert d.choice == "A"


def test_empty_feasible_set_is_ask(snap):
    d = decide(view(snap, [cand("A", 42, transit=(3, 12)), cand("B", 30, transit=(5, 12))], deadline=10))
    assert (d.type, d.choice, d.ask_fields) == ("ask", None, [])


def test_missing_decisive_asks_with_fields(snap):
    # B at /6000: 20 kg * 38 = 760 < A 840; at /5000: 24 kg * 38 = 912 > 840
    b = cand("B", 38)
    b.terms.dim_divisor = Field(None, "missing")
    d = decide(view(snap, [cand("A", 42), b]))
    assert d.type == "ask" and d.ask_fields == ["B.dim_divisor"] and d.grid_size == 2


def test_missing_non_decisive_chooses(snap):
    b = cand("B", 60)
    b.terms.fuel_pct = Field(None, "missing")
    d = decide(view(snap, [cand("A", 42), b]))
    assert (d.type, d.choice, d.ask_fields) == ("choose", "A", [])
    assert d.grid_size == 3


def test_conflicting_uses_the_two_stated_values(snap):
    b = cand("B", 38)
    b.terms.dim_divisor = Field(D(6000), "conflicting")
    b.conflicts["dim_divisor"] = [D(6000), D(6000)]            # identical values: nothing changes
    assert decide(view(snap, [cand("A", 42), b])).type == "choose"
    b.conflicts["dim_divisor"] = [D(6000), D(5000)]
    d = decide(view(snap, [cand("A", 42), b]))
    assert d.type == "ask" and d.ask_fields == ["B.dim_divisor"]


def test_cartesian_product_and_only_decisive_fields_listed(snap):
    b = cand("B", 38)
    b.terms.dim_divisor = Field(None, "missing")
    b.terms.fuel_pct = Field(None, "missing")
    v = view(snap, [cand("A", 42), b])
    v.value_sets = {**VS, "fuel_pct": [0, 0.01]}      # a 1% fuel charge cannot flip the optimum
    d = decide(v)
    assert d.grid_size == 4
    assert d.type == "ask" and d.ask_fields == ["B.dim_divisor"]


def test_margin_reported(snap):
    d = decide(view(snap, [cand("A", 42), cand("B", 38)]))
    assert d.min_feasible == 2
    assert d.min_margin == (D("886.55") - D("806.55")) / D("806.55")


def gold(choice_feasible=True, typ="choose"):
    return Gold("T", {"A": {"total_cny": "100.00", "feasible": True},
                      "B": {"total_cny": "110.00", "feasible": True},
                      "X": {"total_cny": "90.00", "feasible": choice_feasible}},
                {"type": typ, "choice": "A" if typ == "choose" else None,
                 "opt_cost_cny": "100.00" if typ == "choose" else None}, [], {})


def test_regret_values():
    g = gold()
    assert regret("A", g) == (D("0.00"), D("0"))
    assert regret("B", g) == (D("10.00"), D("0.1"))


def test_regret_out_of_feasible_and_ask():
    assert regret("X", gold(choice_feasible=False)) == "out_of_feasible"
    assert regret("A", gold(typ="ask")) is None
    assert regret("X", gold(choice_feasible=False, typ="ask")) is None     # follow-up tasks are judged first
    assert regret(None, gold()) is None
    with pytest.raises(ValueError):
        regret("Z", gold())
