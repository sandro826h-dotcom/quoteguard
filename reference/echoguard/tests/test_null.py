from pathlib import Path
from echoguard.data_io import load_products, load_personas
from echoguard.suitability import eligible_products
from echoguard.null_model import simulate_k_model_null, evaluate_excess_convergence

ROOT=Path(__file__).resolve().parents[1]

def test_k_model_null_shape_and_pvalue():
    products=load_products(ROOT/'data/products_pilot_v0.2.csv')
    persona=next(p for p in load_personas(ROOT/'data/personas_pilot_v0.2.csv') if p.persona_id=='PER_BASE')
    eligible={p.product_id for p in eligible_products(products,persona)}
    ports=[{"P05":0.5,"P06":0.5},{"P05":0.5,"P07":0.5},{"P05":0.5,"P08":0.5}]
    dist=simulate_k_model_null(ports,products,eligible,num_simulations=100,seed=1)
    assert dist.shape==(100,)
    res=evaluate_excess_convergence(0.8,dist)
    assert 0 < res['empirical_p_value'] <= 1
