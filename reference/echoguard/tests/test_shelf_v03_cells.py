import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rows(name):
    return list(csv.DictReader(io.StringIO((ROOT / "data" / name).read_text(encoding="utf-8-sig"))))


def test_v03_cells_only_change_bond_groups():
    old = {r["product_id"]: r for r in _rows("products_pilot_v0.3_sources.csv")}
    new = {r["product_id"]: r for r in _rows("products_pilot_v0.3_cells.csv")}
    assert old.keys() == new.keys()
    for pid, r in old.items():
        for k, v in r.items():
            if k == "economic_cell" and pid in {"P05", "P06", "P07", "P08"}:
                continue
            assert new[pid][k] == v, (pid, k)


def test_v03_cells_match_stage2_analysis_map():
    sys.path.insert(0, str(ROOT / "scripts"))
    import stage2_analyze

    cells = {r["product_id"]: r["economic_cell"] for r in _rows("products_pilot_v0.3_cells.csv")}
    assert cells == stage2_analyze.CELLS_V03


def test_frozen_v02_untouched():
    import hashlib

    digest = hashlib.sha256((ROOT / "data/products_pilot_v0.2.csv").read_bytes()).hexdigest()
    assert digest.upper() == "9D2468F8691CEF6B90EB7B70327D9761E99A0C2212776CAE74AF9579CD3A64BC"
