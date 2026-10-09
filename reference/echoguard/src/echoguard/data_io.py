from __future__ import annotations
import csv
from pathlib import Path
from typing import Dict, List
from .schemas import Product, Persona


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_products(path: str | Path) -> List[Product]:
    items = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            items.append(Product(
                product_id=row["product_id"], official_code=row["official_code"],
                product_name=row["product_name"], issuer_name=row["issuer_name"],
                asset_class=row["asset_class"], strategy_bucket=row["strategy_bucket"],
                economic_cell=row["economic_cell"], source_risk_label=row["source_risk_label"],
                normalized_risk_score=int(row["normalized_risk_score"]),
                risk_normalization_basis=row["risk_normalization_basis"],
                open_frequency=row["open_frequency"], liquidity_score=int(row["liquidity_score"]),
                lockup_months=int(row["lockup_months"]),
                short_horizon_eligible=_as_bool(row["short_horizon_eligible"]),
                requires_experience=_as_bool(row["requires_experience"]),
                source_url=row["source_url"], source_asof=row["source_asof"],
                verification_status=row["verification_status"], notes=row.get("notes", "")
            ))
    return items


def load_personas(path: str | Path) -> List[Persona]:
    items = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            items.append(Persona(
                persona_id=row["persona_id"], risk_capacity=row["risk_capacity"],
                horizon=row["horizon"], liquidity_need=row["liquidity_need"],
                experience=row["experience"], budget_cny=int(row["budget_cny"]),
                contrast_group=row["contrast_group"], changed_factor=row["changed_factor"],
                analyst_label=row.get("analyst_label", "")
            ))
    return items


def product_map(products: List[Product]) -> Dict[str, Product]:
    return {p.product_id: p for p in products}
