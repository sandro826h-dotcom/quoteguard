"""Parameter snapshot loader (tariff components, fees, exchange rate).

Overridden parameters get status 'override' and are listed by Snapshot.overrides().
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional


def _d(x) -> Decimal:
    try:
        d = Decimal(str(x))
    except Exception as e:  # InvalidOperation
        raise ValueError(f"bad decimal in snapshot: {x!r}") from e
    if not d.is_finite():
        raise ValueError(f"non-finite decimal in snapshot: {x!r}")
    return d


class Snapshot:
    def __init__(self, raw: dict, overrides: Optional[dict] = None):
        self.raw = raw
        self.snapshot_id: str = raw["snapshot_id"]
        self.retrieved_at: str = raw.get("retrieved_at", "")
        self._hts = raw["hts"]
        self._fees = {k: dict(v) for k, v in raw["fees"].items()}
        self._fx = {k: dict(v) for k, v in raw["fx"].items()}
        self._overrides: list = []
        for k, v in (overrides or {}).items():
            tbl = self._fees if k in self._fees else self._fx if k in self._fx else None
            if tbl is None:
                raise KeyError(f"unknown override: {k}")
            _d(v)
            self._overrides.append({"name": k, "original": str(tbl[k]["value"]), "value": str(v)})
            tbl[k]["value"] = str(v)
            tbl[k]["status"] = "override"

    def duty_components(self, hts: str) -> list:
        if hts not in self._hts:
            raise KeyError(hts)
        return [(c["component"], _d(c["rate"]), c["cite"], c["status"]) for c in self._hts[hts]["components"]]

    def total_duty_rate(self, hts: str) -> Decimal:
        return sum((c[1] for c in self.duty_components(hts)), Decimal(0))

    def fee(self, name: str) -> Decimal:
        return _d(self._fees[name]["value"])

    def fx_cny_per_usd(self) -> Decimal:
        return _d(self._fx["CNY_per_USD"]["value"])

    def fx_date(self) -> Optional[str]:
        return self._fx["CNY_per_USD"].get("date")

    def overrides(self) -> list:
        """Overridden parameters as [{name, original, value}] (strings)."""
        return [dict(o) for o in self._overrides]

    def assumed_among(self, fee_names, hts_codes, with_fx: bool = True) -> list:
        """Assumed parameters (status 'assumed') among those a calculation actually used."""
        out = [k for k in fee_names if self._fees[k].get("status") == "assumed"]
        if with_fx:
            out += [f"fx:{k}" for k, v in self._fx.items() if v.get("status") == "assumed"]
        for code in sorted(set(hts_codes)):
            out += [f"hts:{code}:{c['component']}" for c in self._hts[code]["components"]
                    if c.get("status") == "assumed"]
        return out

    def assumptions(self) -> list:
        """All parameters in the snapshot with status 'assumed'."""
        out = [k for k, v in self._fees.items() if v.get("status") == "assumed"]
        out += [f"fx:{k}" for k, v in self._fx.items() if v.get("status") == "assumed"]
        for code, h in self._hts.items():
            out += [f"hts:{code}:{c['component']}" for c in h["components"] if c.get("status") == "assumed"]
        return out


def load_snapshot(path, overrides: Optional[dict] = None) -> Snapshot:
    with open(Path(path), encoding="utf-8") as f:
        return Snapshot(json.load(f), overrides)
