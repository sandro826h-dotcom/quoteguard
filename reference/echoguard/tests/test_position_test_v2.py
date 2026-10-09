import pytest
from echoguard.position_test_v2 import first_shown_tests
from echoguard.plan_validation import validate_observations


def picks(n=3):
    return [dict(provider="a", persona_id="p", repeat=i, cell="cash", picked="x", members=["x", "y", "z", "w"]) for i in range(n)]


def test_fixed_order_repetitions_do_not_inflate_sample_size():
    assert first_shown_tests(picks(), order_design="fixed")["a"]["p"] == .25
    assert first_shown_tests(picks(), order_design="independent")["a"]["p"] == 1 / 64


def test_global_order_remains_unassessed():
    r = first_shown_tests(picks(27), order_design="global_fixed")["a"]
    assert r["p"] is None and r["holm_p"] is None and r["status"] == "unassessed"


def test_fixed_order_rejects_mixed_order():
    rows = picks()
    rows[1]["members"] = ["y", "x", "z", "w"]
    with pytest.raises(ValueError, match="Incompatible"):
        first_shown_tests(rows, order_design="fixed")


def test_plan_rejects_duplicate_even_if_total_matches():
    import hashlib
    a = dict(run_key="a", prompt="text", prompt_sha256=hashlib.sha256(b"text").hexdigest())
    b = dict(a, run_key="b")
    with pytest.raises(ValueError, match="Duplicate observation"):
        validate_observations([a, b], [a, a])
    with pytest.raises(ValueError, match="Incomplete"):
        validate_observations([a, b], [a])
    assert validate_observations([a, b], [a, b])["complete"]
