from pathlib import Path
from echoguard.data_io import load_products, load_personas
from echoguard.validation import validate_products, validate_personas
from echoguard.suitability import eligible_products

ROOT=Path(__file__).resolve().parents[1]

def test_stage1_data_valid():
    ps=load_products(ROOT/'data/products_pilot_v0.2.csv')
    pers=load_personas(ROOT/'data/personas_pilot_v0.2.csv')
    assert validate_products(ps)==[]
    assert validate_personas(pers)==[]

def test_liquidity_contrast_restricts_to_cash():
    ps=load_products(ROOT/'data/products_pilot_v0.2.csv')
    persona=next(p for p in load_personas(ROOT/'data/personas_pilot_v0.2.csv') if p.persona_id=='PER_LIQ_HIGH')
    eps=eligible_products(ps,persona)
    assert eps and all(p.economic_cell=='CASH_MM' for p in eps)
