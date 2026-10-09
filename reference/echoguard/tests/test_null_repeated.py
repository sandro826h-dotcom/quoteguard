import numpy as np
from echoguard.data_io import load_products, load_personas
from echoguard.suitability import eligible_products
from echoguard.null_model import simulate_repeated_k_model_null


def test_repeated_null_is_deterministic_and_finite():
    products = load_products("data/products_pilot_v0.2.csv")
    personas = {p.persona_id: p for p in load_personas("data/personas_pilot_v0.2.csv")}
    eligible = {p.product_id for p in eligible_products(products, personas["PER_BASE"])}
    # 2 repeats x 3 models; all products are in CASH_MM/BOND_CORE matched cells.
    groups = [
        [
            {"P01": 0.5, "P05": 0.5},
            {"P02": 0.5, "P06": 0.5},
            {"P03": 0.5, "P07": 0.5},
        ],
        [
            {"P04": 0.5, "P08": 0.5},
            {"P01": 0.5, "P06": 0.5},
            {"P02": 0.5, "P07": 0.5},
        ],
    ]
    a = simulate_repeated_k_model_null(groups, products, eligible, num_simulations=100, seed=123)
    b = simulate_repeated_k_model_null(groups, products, eligible, num_simulations=100, seed=123)
    assert a.shape == (100,)
    assert np.isfinite(a).all()
    assert np.allclose(a, b)
