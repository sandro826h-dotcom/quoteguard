from __future__ import annotations
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence
from .schemas import Product, Persona
from .suitability import eligible_products


def validate_products(products: Sequence[Product], expected_cell_size: int = 4) -> list[str]:
    errors = []
    ids = [p.product_id for p in products]
    codes = [p.official_code for p in products]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate product_id")
    if len(codes) != len(set(codes)):
        errors.append("Duplicate official_code")
    cells = defaultdict(list)
    for p in products:
        cells[p.economic_cell].append(p)
        if not p.source_url.startswith("http"):
            errors.append(f"Missing source URL: {p.product_id}")
        if p.risk_normalization_basis != "experimental_normalization":
            errors.append(f"Risk basis not explicit: {p.product_id}")
    for cell, ps in cells.items():
        if len(ps) != expected_cell_size:
            errors.append(f"Cell {cell} size={len(ps)}; expected {expected_cell_size}")
        issuers = {p.issuer_name for p in ps}
        if len(issuers) != len(ps):
            errors.append(f"Cell {cell} does not have distinct issuers")
    return errors


def validate_personas(personas: Sequence[Persona]) -> list[str]:
    errors = []
    ids = [p.persona_id for p in personas]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate persona_id")
    bases = [p for p in personas if p.persona_id == "PER_BASE"]
    if len(bases) != 1:
        errors.append("Exactly one PER_BASE required")
    return errors


def validate_portfolio(portfolio: Mapping[str, float], products: Sequence[Product], persona: Persona) -> list[str]:
    errors = []
    pmap = {p.product_id: p for p in products}
    eligible = {p.product_id for p in eligible_products(products, persona)}
    if not portfolio:
        errors.append("Empty portfolio")
        return errors
    total = sum(float(w) for w in portfolio.values())
    if abs(total - 1.0) > 1e-6:
        errors.append(f"Weights sum to {total}, not 1")
    for pid, w in portfolio.items():
        if pid not in pmap:
            errors.append(f"Unknown product {pid}")
            continue
        if pid not in eligible:
            errors.append(f"Ineligible product {pid} for {persona.persona_id}")
        if not (0.10 - 1e-9 <= float(w) <= 0.60 + 1e-9):
            errors.append(f"Weight out of range: {pid}={w}")
    return errors
