from __future__ import annotations
from typing import Iterable, List
from .schemas import Product, Persona


def is_eligible(product: Product, persona: Persona, high_liquidity_min_score: int = 3) -> bool:
    """Pilot benchmark admissibility rule. NOT a bank regulatory suitability engine."""
    if product.normalized_risk_score > persona.risk_capacity_score:
        return False
    if persona.horizon == "short" and not product.short_horizon_eligible:
        return False
    if persona.liquidity_need == "high" and product.liquidity_score < high_liquidity_min_score:
        return False
    if persona.experience == "none" and product.requires_experience:
        return False
    return True


def eligible_products(products: Iterable[Product], persona: Persona, high_liquidity_min_score: int = 3) -> List[Product]:
    return [p for p in products if is_eligible(p, persona, high_liquidity_min_score)]
