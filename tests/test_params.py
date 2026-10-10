from decimal import Decimal
from pathlib import Path

import pytest

from quoteguard.params import load_snapshot

SNAP = Path(__file__).resolve().parent.parent / "data" / "params" / "tariff_snapshot_2026-10-09.json"


def test_load_and_rates():
    s = load_snapshot(SNAP)
    assert s.snapshot_id == "tariff-2026-10-09"
    assert s.total_duty_rate("6109.10.00") == Decimal("0.365")
    assert s.total_duty_rate("3926.90.99") == Decimal("0.253")
    assert s.total_duty_rate("4202.92.31") == Decimal("0.551")
    assert s.total_duty_rate("9503.00.00") == Decimal("0.125")


def test_components_tuple_shape():
    comps = load_snapshot(SNAP).duty_components("6109.10.00")
    assert [c[0] for c in comps] == ["mfn", "s301_list4A", "s301_2026"]
    comp, rate, cite, status = comps[0]
    assert rate == Decimal("0.165") and status == "verified" and "6109.10.00" in cite


def test_unknown_hts():
    s = load_snapshot(SNAP)
    with pytest.raises(KeyError):
        s.duty_components("0000.00.00")
    with pytest.raises(KeyError):
        s.total_duty_rate("0000.00.00")


def test_fees_fx():
    s = load_snapshot(SNAP)
    assert s.fee("mpf_rate") == Decimal("0.003464")
    assert s.fee("formal_entry_threshold_usd") == Decimal("2500")
    assert s.fx_cny_per_usd() == Decimal("6.7330")
    assert s.fee("mpf_min_usd") == Decimal("34.58")
    assert s.fee("mpf_max_usd") == Decimal("670.86")
    assert s.fee("mpf_informal_usd") == Decimal("2.77")
    with pytest.raises(KeyError):
        s.fee("nope")


def test_no_assumed_parameters():
    s = load_snapshot(SNAP)
    assert s.assumptions() == []


def test_assumptions_listed_from_modified_snapshot(tmp_path):
    import json
    raw = json.loads(SNAP.read_text())
    raw["fees"]["mpf_min_usd"]["status"] = "assumed"
    raw["fx"]["CNY_per_USD"]["status"] = "assumed"
    raw["hts"]["9503.00.00"]["components"][0]["status"] = "assumed"
    p = tmp_path / "a.json"
    p.write_text(json.dumps(raw))
    s = load_snapshot(p)
    assert s.assumptions() == ["mpf_min_usd", "fx:CNY_per_USD", "hts:9503.00.00:mfn"]
    assert s.assumed_among(["mpf_rate"], ["6109.10.00"]) == ["fx:CNY_per_USD"]


def test_snapshot_provenance():
    import json
    raw = json.loads(SNAP.read_text())
    assert raw["snapshot_id"] == "tariff-2026-10-09" and raw["version"] == "2026-10-10.1"
    names = {x["name"] for x in raw["sources"]}
    assert {"FR-2026-07-31_2026-15530_FY2027_user_fees.pdf", "FR-2026-15530_table2.png",
            "CFETS_central_parity_2026-10-09.html"} <= names
    ch = raw["changelog"][0]["changes"]
    assert {c["param"] for c in ch} == {"mpf_min_usd", "mpf_max_usd", "mpf_informal_usd", "fx.CNY_per_USD"}
    assert all(c["new_status"] == "verified" for c in ch)


def test_overrides():
    s = load_snapshot(SNAP, {"mpf_informal_usd": "0", "CNY_per_USD": "7.25"})
    assert s.fee("mpf_informal_usd") == 0
    assert s.fx_cny_per_usd() == Decimal("7.25")
    assert "mpf_informal_usd" not in s.assumptions()
    # original snapshot unaffected
    assert load_snapshot(SNAP).fee("mpf_informal_usd") == Decimal("2.77")
    with pytest.raises(KeyError):
        load_snapshot(SNAP, {"bogus": "1"})


def test_overrides_listed():
    s = load_snapshot(SNAP, {"mpf_informal_usd": "0", "CNY_per_USD": "7.25"})
    assert s.overrides() == [{"name": "mpf_informal_usd", "original": "2.77", "value": "0"},
                             {"name": "CNY_per_USD", "original": "6.7330", "value": "7.25"}]
    assert load_snapshot(SNAP).overrides() == []


def test_fx_date():
    assert load_snapshot(SNAP).fx_date() == "2026-10-09"


def test_non_finite_rejected(tmp_path):
    import json
    raw = json.loads(SNAP.read_text())
    raw["fees"]["hmf_rate"]["value"] = "NaN"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_snapshot(p).fee("hmf_rate")
    with pytest.raises(ValueError):
        load_snapshot(SNAP, {"hmf_rate": "Infinity"})
