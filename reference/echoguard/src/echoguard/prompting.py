from __future__ import annotations
import json
import random
from pathlib import Path
from typing import Dict, List, Sequence
from .schemas import Product, Persona
from .blind_shelf import build_shelf


def persona_for_model(persona: Persona) -> Dict:
    """Analyst-only labels are intentionally excluded."""
    return {
        "risk_capacity": persona.risk_capacity,
        "horizon": persona.horizon,
        "liquidity_need": persona.liquidity_need,
        "experience": persona.experience,
        "budget_cny": persona.budget_cny,
    }


def make_prompt(template_path: str | Path, products: Sequence[Product], persona: Persona,
                scenario: Dict, blind: bool, order_seed: int) -> str:
    template = Path(template_path).read_text(encoding="utf-8")
    shelf = build_shelf(products, persona, blind=blind)
    rng = random.Random(order_seed)
    rng.shuffle(shelf)
    return (template
            .replace("{{ persona_json }}", json.dumps(persona_for_model(persona), ensure_ascii=False))
            .replace("{{ scenario_json }}", json.dumps(scenario, ensure_ascii=False))
            .replace("{{ shelf_json }}", json.dumps(shelf, ensure_ascii=False)))
