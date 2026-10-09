from __future__ import annotations
from typing import Dict, Iterable, List
from .schemas import Product, Persona
from .suitability import is_eligible


def shelf_record(product: Product, persona: Persona, blind: bool = False) -> Dict:
    """Return model-facing shelf data. Identity masking never removes economic information."""
    return {
        "product_id": product.product_id,
        "product_name": None if blind else product.product_name,
        "issuer_name": None if blind else product.issuer_name,
        "asset_class": product.asset_class,
        "strategy_bucket": product.strategy_bucket,
        "economic_cell": product.economic_cell,
        "normalized_risk_score": product.normalized_risk_score,
        "open_frequency": product.open_frequency,
        "liquidity_score": product.liquidity_score,
        "lockup_months": product.lockup_months,
        "short_horizon_eligible": product.short_horizon_eligible,
        "eligible": is_eligible(product, persona),
    }


def build_shelf(products: Iterable[Product], persona: Persona, blind: bool = False) -> List[Dict]:
    return [shelf_record(p, persona, blind=blind) for p in products]
