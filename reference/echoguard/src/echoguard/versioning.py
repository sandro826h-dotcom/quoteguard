from __future__ import annotations
from typing import Mapping, Dict
from .metrics import weighted_jaccard, cosine_similarity, set_jaccard


def compare_portfolio_versions(old: Mapping[str, float], new: Mapping[str, float]) -> Dict[str, float]:
    """Behavioral similarity for the same model/task across two model versions.

    Lower similarity means larger portfolio behavior drift. This function does not decide whether
    the drift is acceptable; policy thresholds belong to the bank's external release process.
    """
    return {
        "weighted_jaccard": weighted_jaccard(old, new),
        "cosine": cosine_similarity(old, new),
        "set_jaccard": set_jaccard(old, new),
    }


def metric_delta(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    return float(new) - float(old)
