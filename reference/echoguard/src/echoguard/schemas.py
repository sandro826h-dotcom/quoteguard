from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class Product:
    product_id: str
    official_code: str
    product_name: str
    issuer_name: str
    asset_class: str
    strategy_bucket: str
    economic_cell: str
    source_risk_label: str
    normalized_risk_score: int
    risk_normalization_basis: str
    open_frequency: str
    liquidity_score: int
    lockup_months: int
    short_horizon_eligible: bool
    requires_experience: bool
    source_url: str
    source_asof: str
    verification_status: str
    notes: str = ""


@dataclass(frozen=True)
class Persona:
    persona_id: str
    risk_capacity: str
    horizon: Literal["short", "long"]
    liquidity_need: Literal["high", "normal"]
    experience: Literal["none", "experienced"]
    budget_cny: int
    contrast_group: str
    changed_factor: str
    analyst_label: str = ""

    @property
    def risk_capacity_score(self) -> int:
        if not self.risk_capacity.startswith("C"):
            raise ValueError(f"Unexpected risk capacity: {self.risk_capacity}")
        return int(self.risk_capacity[1:])


Portfolio = Dict[str, float]


@dataclass
class Decision:
    decision: Literal["RECOMMEND", "ABSTAIN"]
    portfolio: Portfolio
    justification_summary: str = ""


@dataclass
class RunMeta:
    run_id: str
    model_family: str
    model_name: str
    model_version: str
    persona_id: str
    scenario_id: str
    shelf_condition: Literal["named", "blind"]
    repeat_id: int
    shelf_order_seed: int
    prompt_version: str
