import copy
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from quoteguard import taskgen as tg
from quoteguard.decide import decide, view_from_task
from quoteguard.gate import claim_status
from quoteguard.params import load_snapshot
from quoteguard.schema import Claim, Gold, QuoteTerms, Task
from quoteguard.util import canonical_json

ROOT = Path(__file__).resolve().parent.parent
slow = pytest.mark.slow
FIELDS = list(QuoteTerms().to_dict())


@pytest.fixture(scope="module")
def inp():
    return tg.load_inputs()


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(tg.DEFAULT_SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def dev(inp, snap):
    return tg.generate(6, "dev", 7, snap, inp["structures"], inp["value_sets"], inp["registry"],
                       tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])


def by_layer(dev, layer):
    tasks, golds = dev
    gm = {g.task_id: g for g in golds}
    return [(t, gm[t.task_id]) for t in tasks if t.layer == layer]


def view_of(inp, snap, task):
    return view_from_task(task, snap, inp["freight_floor"], inp["value_sets"])


def statuses(task):
    return {c.cand_id: {f: c.presentation["terms"][f]["status"] for f in FIELDS} for c in task.candidates}


def dump(tasks, golds):
    return canonical_json([t.to_dict() for t in tasks]) + canonical_json([g.to_dict() for g in golds])


# ------------------------------------------------------------------ determinism and structure
def test_same_seed_same_bytes(dev, inp, snap):
    again = tg.generate(6, "dev", 7, snap, inp["structures"], inp["value_sets"], inp["registry"],
                        tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])
    assert dump(*dev) == dump(*again)
    other = tg.generate(1, "dev", 8, snap, inp["structures"], inp["value_sets"], inp["registry"],
                        tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])
    assert dump(*dev) != dump(*other)


def test_counts_and_basic_shape(dev):
    tasks, golds = dev
    assert len(tasks) == len(golds) == 24
    for layer in tg.LAYER_ORDER:
        assert len(by_layer(dev, layer)) == 6
    for t in tasks:
        t.validate()
        assert 4 <= len(t.candidates) <= 6
        assert "2026-10-12" <= t.shipment.ship_date <= "2026-10-31"
        assert t.shipment.goods[0].hts in ("6109.10.00", "3926.90.99", "4202.92.31", "9503.00.00")
        assert t.param_snapshot_id == "tariff-2026-10-09"
        assert all(c.is_synthetic for c in t.candidates)
    assert len({t.task_id for t in tasks}) == 24


def test_each_task_has_ddp_and_non_ddp(dev):
    tasks, golds = dev
    explicit = [tg.true_terms(t, g, c.cand_id).plain("ddp").is_ddp
                for t, g in by_layer(dev, "explicit") for c in t.candidates]
    assert any(explicit) and not all(explicit)
    for t, g in by_layer(dev, "explicit"):
        flags = [tg.true_terms(t, g, c.cand_id).plain("ddp").is_ddp for c in t.candidates]
        assert any(flags) and not all(flags)


def test_presentation_spec(dev):
    keys = {"cand_id", "modality", "template_id", "pool", "lang_mix", "fee_location", "conflicts",
            "ddp_wording_id", "claim_wording_id", "forwarder_alias", "terms"}
    tasks, _ = dev
    for t in tasks:
        for c in t.candidates:
            p = c.presentation
            assert keys <= set(p)
            assert p["modality"] in tg.MODALITIES and p["pool"] == "dev"
            assert p["template_id"] in tg.DEFAULT_TEMPLATE_POOL["dev"]
            assert f"-{p['modality']}-" in p["template_id"]
            assert p["forwarder_alias"].startswith("FWD-")
            assert p["terms"] == QuoteTerms.from_dict(p["terms"]).to_dict()
    assert len({c.presentation["modality"] for t in tasks for c in t.candidates}) == 3


# ------------------------------------------------------------------ layers
def test_explicit_has_no_gaps(dev):
    for t, g in by_layer(dev, "explicit"):
        for c in t.candidates:
            assert set(statuses(t)[c.cand_id].values()) == {"stated"}
            assert c.presentation["conflicts"] == {}
            assert set(c.presentation["fee_location"].values()) == {"body"}
        assert g.decision["type"] == "choose"
        assert g.decision["verification"]["grid_size"] == 1


def test_implicit_fee_hidden_and_changes_optimum(dev, inp, snap):
    for t, g in by_layer(dev, "implicit"):
        hidden = [i for c in g.per_cand.values() for i in c["hidden_fee_ids"]]
        assert hidden
        locs = {c.cand_id: c.presentation["fee_location"] for c in t.candidates}
        assert any(v in ("footnote", "annex") for l in locs.values() for v in l.values())
        for c in t.candidates:
            assert set(statuses(t)[c.cand_id].values()) == {"stated"}
            assert c.presentation["conflicts"] == {}
        # a reader who misses the footnote and annex fees picks a different quote
        v = view_of(inp, snap, t)
        for cid, f in (h.split(".") for h in hidden):
            cv = next(x for x in v.candidates if x.cand_id == cid)
            tg_terms = tg._drop_fee(cv.terms, f)
            cv.terms = tg_terms
        naive = decide(v)
        assert naive.choice != g.decision["choice"]


@pytest.mark.parametrize("layer,field_status", [("conflict", "conflicting"), ("insufficient", "missing")])
def test_gap_layers_decisive_counts_and_recheck(dev, inp, snap, layer, field_status):
    items = by_layer(dev, layer)
    types = [g.decision["type"] for _, g in items]
    assert types.count("ask") == 4 and types.count("choose") == 2
    for t, g in items:
        st = {c: s for c, fs in statuses(t).items() for f, s in fs.items() if s == field_status}
        assert st, "the layer operation must be applied"
        other = "missing" if field_status == "conflicting" else "conflicting"
        assert not any(s == other for fs in statuses(t).values() for s in fs.values())
        d = decide(view_of(inp, snap, t))            # recheck from the task alone
        assert d.type == g.decision["type"]
        assert d.choice == g.decision["choice"]
        assert d.ask_fields == g.followup_gap_ids == g.decision["ask_fields"]
        gaps = set(sum((c["conflict_ids"] + c["missing_ids"] for c in g.per_cand.values()), []))
        assert set(d.ask_fields) <= gaps
        if d.type == "ask":
            assert d.ask_fields
        else:
            assert g.decision["oracle_choice"] == g.decision["choice"]


def test_conflicting_values_recorded_twice(dev):
    for t, g in by_layer(dev, "conflict"):
        n = 0
        for c in t.candidates:
            for f, vals in c.presentation["conflicts"].items():
                n += 1
                assert len(vals) == 2 and vals[0] != vals[1]
                assert c.presentation["terms"][f]["status"] == "conflicting"
        assert n >= 1


def test_margin_3_percent_everywhere(dev):
    for t, g in zip(*dev):
        ver = g.decision["verification"]
        assert D(ver["min_margin"]) >= tg.MIN_MARGIN
        assert ver["min_feasible"] >= 2
        for row in ver["grid"]:
            assert (D(row["second_cny"]) - D(row["best_cny"])) / D(row["best_cny"]) >= tg.MIN_MARGIN
    # also directly on the final gold costs
    for t, g in zip(*dev):
        feas = sorted(D(c["total_cny"]) for c in g.per_cand.values() if c["feasible"])
        assert (feas[1] - feas[0]) / feas[0] >= tg.MIN_MARGIN


def test_gold_content(dev):
    for t, g in zip(*dev):
        g.validate()
        assert set(g.per_cand) == {c.cand_id for c in t.candidates}
        for c in g.per_cand.values():
            assert {"lines", "ddp_level", "claim_statuses", "hidden_fee_ids", "conflict_ids",
                    "missing_ids", "feasible"} <= set(c)
            assert c["ddp_level"] in ("none", "verify", "high")
            assert (c["ddp_level"] == "high") <= (not c["feasible"])


def test_claim_statuses_consistent_with_gate(dev, inp):
    n = 0
    for t, g in zip(*dev):
        for c in t.candidates:
            tr = tg.true_terms(t, g, c.cand_id)
            for i, cl in enumerate(tr.plain("claims")):
                ev = g.per_cand[c.cand_id]["claims"][i]
                assert ev["gold_status"] == claim_status(cl, t.shipment.mode, tg.model_registry_snapshot(inp["registry"], "dev"),
                                                         c.presentation["quote_entity"])
                n += 1
    assert n > 0


def test_synthetic_registry_names(inp):
    reg = inp["registry"]
    assert reg["is_synthetic"] is True
    for pool in ("dev", "test"):
        for r in reg[pool]["fmc"] + reg[pool]["cn"]:
            assert r["name"].startswith("SYN-") and r["is_synthetic"] is True
        assert all(n.startswith("SYN-") for n in reg["generator_aux"][pool]["unregistered_entities"])


def test_registry_pools_disjoint_and_model_snapshot_clean(inp):
    reg = inp["registry"]
    names = lambda pool: {r["name"] for r in reg[pool]["fmc"] + reg[pool]["cn"]} | \
        set(reg["generator_aux"][pool]["unregistered_entities"])
    assert not names("dev") & names("test")
    snap_dev = tg.model_registry_snapshot(reg, "dev")
    assert set(snap_dev) == {"snapshot_id", "is_synthetic", "fmc", "cn"}
    text = json.dumps(snap_dev, ensure_ascii=False)
    for n in reg["generator_aux"]["dev"]["unregistered_entities"]:
        assert n not in text
    for n in names("test"):
        assert n not in text
    bad = copy.deepcopy(reg)
    bad["test"]["fmc"][0]["name"] = bad["dev"]["fmc"][0]["name"]
    with pytest.raises(ValueError):
        tg._validate_registry(bad)


def test_unregistered_mot_scenario_is_insufficient(inp):
    reg = tg.model_registry_snapshot(inp["registry"], "dev")
    ent = inp["registry"]["generator_aux"]["dev"]["unregistered_entities"][0]
    assert claim_status(Claim("MOT_NVOCC", ent, None, "CN"), "sea", reg, ent) == "insufficient"
    assert inp["structures"]["claims"]["scenario_weights"]["unregistered_mot"] > 0


def test_included_excluded_taxes_follow_scope(dev, inp):
    from quoteguard.schema import DDP
    cases = [(DDP(True, "duty", "included"), ["关税"], ["MPF", "HMF"]),
             (DDP(True, "duty_mpf", "included"), ["关税", "MPF"], ["HMF"]),
             (DDP(True, "all", "included"), ["关税", "MPF", "HMF"], []),
             (DDP(False, "duty", "actual"), [], ["关税", "MPF", "HMF"]),
             (None, [], [])]
    for ddp, inc_t, exc_t in cases:
        inc, exc = tg._quote_lists(ddp)
        assert [t for t in tg.TAXES if t in " ".join(inc)] == inc_t
        assert [t for t in tg.TAXES if t in " ".join(exc)] == exc_t
    n_stated = n_gap = 0
    for t, g in zip(*dev):
        for c in t.candidates:
            terms = QuoteTerms.from_dict(c.presentation["terms"])
            tax_text = lambda f: [x for x in tg.TAXES if x in " ".join(getattr(terms, f).value or [])]
            if terms.ddp.status == "stated":
                n_stated += 1
                d = terms.ddp.value
                want = tg.SCOPE_TAXES[d.scope] if d.is_ddp else set()
                assert set(tax_text("includes")) == want
                assert set(tax_text("excludes")) == set(tg.TAXES) - want
            else:
                n_gap += 1
                assert tax_text("includes") == [] and tax_text("excludes") == []


def test_gap_value_cannot_be_narrowed_by_stated_fields(dev, inp, snap):
    """Restricting the ddp grid by what includes / excludes imply must not change the decision."""
    n = 0
    for t, g in zip(*dev):
        v = view_of(inp, snap, t)
        base = decide(v)
        for cv in v.candidates:
            if cv.terms.ddp.status == "stated":
                continue
            n += 1
            inc, exc = cv.terms.includes.value, cv.terms.excludes.value
            assert not any(x in " ".join(inc + exc) for x in tg.TAXES)
        assert base.type == g.decision["type"]
    assert n > 0


def test_eligible_fields_match_spec_and_are_validated(inp):
    vs = inp["value_sets"]
    assert {m: set(v) for m, v in vs["eligible_fields"].items()} == tg.MISSING_FIELDS_SPEC
    assert tg.MISSING_FIELDS_SPEC["air"] == {"dim_divisor", "min_kg_shipment", "ddp"}
    bad = copy.deepcopy(vs)
    bad["eligible_fields"]["air"].append("broker_fee_cny")
    with pytest.raises(ValueError):
        tg.validate_value_sets(bad)


def test_missing_terms_are_only_required_terms(dev):
    for t, g in by_layer(dev, "insufficient"):
        for c in t.candidates:
            for f, v in c.presentation["terms"].items():
                if v["status"] == "missing":
                    assert f in tg.MISSING_FIELDS_SPEC[t.shipment.mode], (t.shipment.mode, f)


def test_render_check_runs_on_gap_candidates(dev, monkeypatch):
    # every gap candidate renders with two sources per conflict and none per missing term
    from quoteguard.render import RenderSpec, render
    import tempfile
    checked = 0
    for layer in ("conflict", "insufficient"):
        for t, g in by_layer(dev, layer)[:3]:
            for c in t.candidates:
                gaps = {f: v["status"] for f, v in c.presentation["terms"].items()
                        if v["status"] in ("missing", "conflicting")}
                if not gaps:
                    continue
                with tempfile.TemporaryDirectory() as td:
                    res = render(RenderSpec.from_dict(c.presentation), td)
                for f, st in gaps.items():
                    n = len(res.span_map.get(f) or [])
                    assert (n >= 2) if st == "conflicting" else n == 0
                    checked += 1
    assert checked > 0
    # a renderer that drops a source makes the check fail and the task is redrawn
    import quoteguard.taskgen as T
    real = T.render

    def fake(spec, outdir):
        r = real(spec, outdir)
        r.span_map = {k: v[:1] for k, v in r.span_map.items()}
        return r

    inp = tg.load_inputs()
    snap = load_snapshot(tg.DEFAULT_SNAPSHOT_PATH)
    ctx = {"structures": inp["structures"], "value_sets": inp["value_sets"], "snapshot": snap,
           "freight_floor": inp["freight_floor"], "reg": tg.model_registry_snapshot(inp["registry"], "dev"),
           "unreg": inp["registry"]["generator_aux"]["dev"]["unregistered_entities"]}
    monkeypatch.setattr(T, "render", fake)
    monkeypatch.setattr(T, "MAX_ATTEMPTS", 300)
    stats = {}
    with pytest.raises(RuntimeError):
        T._make_raw("conflict", (False, "forbid"), 123, ctx, "dev", tg.DEFAULT_TEMPLATE_POOL, stats)
    assert stats["conflict"]["render_check_failures"] > 0
    assert stats["conflict"]["attempts"] == 300


def test_ddp_quota_in_gap_layers(dev, inp, snap):
    def check(golds_tasks):
        for layer in ("conflict", "insufficient"):
            items = [(t, g) for t, g in golds_tasks if t.layer == layer]
            dec = [(t, g) for t, g in items if g.decision["type"] == "ask"]
            non = [(t, g) for t, g in items if g.decision["type"] == "choose"]
            n_ddp_dec = sum(any(f.endswith(".ddp") for f in g.followup_gap_ids) for _, g in dec)
            assert n_ddp_dec <= 0.4 * len(dec)
            has_ddp_gap = lambda t: any(c.presentation["terms"]["ddp"]["status"] != "stated" for c in t.candidates)
            assert sum(has_ddp_gap(t) for t, _ in non) == tg.layer_plan(len(items)).count((False, "require"))
            assert n_ddp_dec == tg.layer_plan(len(items)).count((True, "require"))
    check(list(zip(*dev)))
    tasks, golds = tg.generate(6, "dev", 20261010, **gen_kw(inp, snap))
    check(list(zip(tasks, golds)))


def test_layer_plan_quotas():
    assert [tg.layer_plan(n).count((True, "require")) for n in (6, 12)] == [1, 3]
    assert [tg.layer_plan(n).count((False, "require")) for n in (6, 12)] == [1, 2]
    for n in (1, 2, 3, 6, 12):
        assert len(tg.layer_plan(n)) == n


def test_hidden_fees_have_positive_amount(dev):
    for t, g in by_layer(dev, "implicit"):
        for cid, pc in g.per_cand.items():
            for hid in pc["hidden_fee_ids"]:
                f = hid.split(".")[1]
                lines = dict(pc["lines"])
                if f == "surcharges":
                    assert sum(D(v) for k, v in pc["lines"] if k not in tg.NON_COST_CODES) > 0
                else:
                    code = {"fuel_pct": "fuel", "broker_fee_cny": "broker", "isf_fee_cny": "isf"}[f]
                    assert D(lines[code]) > 0


def test_gold_undetermined_flags(dev):
    n_und = 0
    for t, g in zip(*dev):
        for cid, pc in g.per_cand.items():
            lo, hi = map(D, pc["cost_range_cny"])
            assert lo <= D(pc["total_cny"]) <= hi
            assert pc["ddp_level"] in pc["ddp_levels"] and pc["feasible"] in pc["feasible_values"]
            if pc["determined"]:
                assert lo == hi and len(pc["ddp_levels"]) == 1 and len(pc["feasible_values"]) == 1
            else:
                n_und += 1
                assert pc["conflict_ids"] or pc["missing_ids"]
    assert n_und > 0
    for t, g in by_layer(dev, "explicit"):
        assert all(pc["determined"] for pc in g.per_cand.values())


# ------------------------------------------------------------------ pools and files
def test_template_pools_disjoint_and_complete():
    p = tg.DEFAULT_TEMPLATE_POOL
    assert not set(p["dev"]) & set(p["test"])
    for pool in p.values():
        for m in tg.MODALITIES:
            assert len([t for t in pool if f"-{m}-" in t]) == 3


def test_overlapping_pools_rejected(inp, snap):
    bad = {"dev": ["dev-text-1", "x-table-1", "x-image-1"], "test": ["dev-text-1", "t-table-1", "t-image-1"]}
    with pytest.raises(ValueError):
        tg.generate(1, "dev", 1, snap, inp["structures"], inp["value_sets"], inp["registry"], bad,
                    inp["freight_floor"])


def test_dev_wording_pools_disjoint(inp):
    wp = inp["structures"]["wording_pools"]
    for k in ("ddp", "claim"):
        assert not set(wp["dev"][k]) & set(wp["test"][k])


def test_write_split_separates_gold(dev, tmp_path):
    tasks, golds = dev
    out = tmp_path / "tasks" / "dev"
    m = tg.write_split(tasks, golds, out, {"seed": 7})
    gold_dir = tmp_path / "gold" / "dev"
    assert gold_dir.is_dir() and out.is_dir()
    task_files = [p for p in out.rglob("*.json") if p.name != "manifest.json"]
    assert len(task_files) == 24
    assert not any(gold_dir in p.parents for p in task_files)
    for p in task_files:                      # answers never leak into task files
        txt = p.read_text(encoding="utf-8")
        for key in ("per_cand", "followup_gap_ids", "true_values", "calc_hashes", "oracle"):
            assert key not in txt
    assert len(list(gold_dir.glob("*.json"))) == 24
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["seed"] == 7 and len(man["generator_code_sha256"]) == 64
    assert set(man["generator_files_sha256"]) == set(tg.CODE_FILES) >= {"render.py", "schema.py", "calc_xlsx.py"}
    assert man["gold_dir"] == "../../gold/dev"
    env = man["environment"]
    assert set(env) == {"git_commit", "git_clean", "python", "pyyaml", "libreoffice"}
    assert env["python"] and env["pyyaml"]
    assert set(man["files"]) == {"tasks", "gold"} and len(man["files"]["tasks"]) == 24
    # round trip
    t0 = Task.from_dict(json.loads(task_files[0].read_text(encoding="utf-8")))
    assert t0.to_dict() == next(t for t in tasks if t.task_id == t0.task_id).to_dict()
    g0 = Gold.from_dict(json.loads((gold_dir / f"{t0.task_id}.json").read_text(encoding="utf-8")))
    g0.validate()
    assert m["n_tasks"] == 24


def test_write_split_byte_identical_and_gold_outside(dev, tmp_path, inp, snap):
    tasks, golds = dev
    again = tg.generate(6, "dev", 7, snap, inp["structures"], inp["value_sets"], inp["registry"],
                        tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])
    m1 = tg.write_split(tasks, golds, tmp_path / "a" / "tasks" / "dev")
    m2 = tg.write_split(*again, tmp_path / "b" / "tasks" / "dev")
    assert m1["files"] == m2["files"]
    with pytest.raises(ValueError):
        tg.write_split(tasks, golds, tmp_path / "c", gold_dir=tmp_path / "c" / "tasks")


def test_generate_split_meta(tmp_path):
    tasks, golds, meta = tg.generate_split("dev", 3, 1)
    assert meta["snapshot_version"] and meta["snapshot_id"] == "tariff-2026-10-09"
    assert set(meta["yaml_sha256"]) == {"structures.yaml", "value_sets.yaml", "registry_synthetic.yaml",
                                        "freight_floor.yaml"}
    assert meta["pending_calibration_count"] == 0          # recorded in the manifest
    assert len(tasks) == 4
    st = meta["layer_stats"]
    assert set(st) == set(tg.LAYER_ORDER)
    assert all(v["tasks"] == 1 and v["attempts"] >= 1 and "swallowed_exceptions" in v for v in st.values())
    assert meta["max_attempts_per_task"] == 2000


# ------------------------------------------------------------------ test split guard
def with_pending(structures):
    st = copy.deepcopy(structures)
    st["modes"]["sea"]["channels"][0]["source_status"] = "pending_calibration"
    return st


def gen_kw(inp, snap, structures=None, ff=None):
    return dict(snapshot=snap, structures=structures or inp["structures"], value_sets=inp["value_sets"],
                registry=inp["registry"], template_pool=tg.DEFAULT_TEMPLATE_POOL,
                freight_floor=ff or inp["freight_floor"])


def test_input_statuses_allow_test_when_none_pending(inp, snap):
    assert tg.pending_items(inp["structures"], inp["freight_floor"]) == []
    tasks, _ = tg.generate(1, "test", 5, **gen_kw(inp, snap))
    assert len(tasks) == 4
    for t in tasks:
        assert t.split == "test"
        assert all(c.presentation["template_id"] in tg.DEFAULT_TEMPLATE_POOL["test"] for c in t.candidates)
        assert all(c.presentation["pool"] == "test" for c in t.candidates)


def test_test_split_refused_when_structures_pending(inp, snap):
    st = with_pending(inp["structures"])
    assert tg.pending_items(st, inp["freight_floor"])
    with pytest.raises(tg.PendingCalibrationError):
        tg.generate(1, "test", 1, **gen_kw(inp, snap, st))
    tasks, _ = tg.generate(1, "dev", 1, **gen_kw(inp, snap, st))      # dev may use pending inputs
    assert len(tasks) == 4


def test_test_split_refused_when_freight_floor_pending(inp, snap):
    ff = copy.deepcopy(inp["freight_floor"])
    ff["air"]["west"]["status"] = "pending_calibration"
    with pytest.raises(tg.PendingCalibrationError):
        tg.generate(1, "test", 1, **gen_kw(inp, snap, ff=ff))


def test_other_statuses_do_not_block_test(inp):
    ff = copy.deepcopy(inp["freight_floor"])
    for st in ("calibrated", "estimated", "synthetic_assumption", "no_qualifying_units"):
        ff["air"]["west"]["status"] = st
        assert tg.pending_items(inp["structures"], ff) == []


def test_unknown_source_status_rejected(inp):
    st = copy.deepcopy(inp["structures"])
    st["commodities"][0]["source_status"] = "guess"
    with pytest.raises(ValueError):
        tg.validate_structures(st, inp["freight_floor"])


def test_structure_items_carry_source_fields(inp):
    st = inp["structures"]
    items = st["commodities"] + [c for m in st["modes"].values() for c in m["channels"]] + \
        list(st["source_meta"].values())
    for it in items:
        assert it["source_status"] in tg.ALLOWED_SOURCE_STATUS and "source_seed_ids" in it
    air = {c["id"]: c for c in st["modes"]["air"]["channels"]}
    assert air["AIR-A"]["source_status"] == "calibrated" and air["AIR-A"]["source_seed_ids"] == ["Q01-u2"]
    assert air["AIR-B"]["source_seed_ids"] == ["Q01-u3"]
    assert {c["source_status"] for c in st["modes"]["sea"]["channels"]} == {"estimated"}
    assert "Q05-u2" in " ".join(c["source_note"] for c in st["modes"]["sea"]["channels"])
    assert air["AIR-A"]["raw_usd_per_kg"]["12-20"] == 5.80 and air["AIR-B"]["raw_usd_per_kg"]["301-500"] == 4.87
    assert "美西" in air["AIR-A"]["raw_note"]
    ff = inp["freight_floor"]["air"]
    assert ff["west"]["source_seed_ids"] == ff["national"]["source_seed_ids"] == ["Q01-u1", "Q01-u2", "Q01-u3"]


def test_air_non_ddp_tiers_not_below_reference(inp, dev):
    ref = D(str(inp["freight_floor"]["air"]["national"]["value_cny_per_kg"]))
    for ch in inp["structures"]["modes"]["air"]["channels"]:
        assert min(D(str(t["price"][0])) for t in ch["tiers"]) >= ref
    seen = 0
    for t, g in zip(*dev):
        if t.shipment.mode != "air":
            continue
        for c in t.candidates:
            tr = tg.true_terms(t, g, c.cand_id)
            if not tr.plain("ddp").is_ddp:
                seen += 1
                assert all(x.price >= ref for x in tr.plain("rate_tiers"))
    assert seen > 0
    st = copy.deepcopy(inp["structures"])
    st["modes"]["air"]["channels"][0]["tiers"][0]["price"] = [30, 31]
    with pytest.raises(ValueError):
        tg.validate_structures(st, inp["freight_floor"])


def test_four_claim_statuses_all_appear(dev, inp, snap):
    from collections import Counter
    c = Counter(s for _, g in zip(*dev) for v in g.per_cand.values() for s in v["claim_statuses"])
    assert set(c) == {"supported", "insufficient", "contradicted", "irrelevant"}, c
    tasks, golds = tg.generate(6, "dev", 20261010, **gen_kw(inp, snap))
    c = Counter(s for g in golds for v in g.per_cand.values() for s in v["claim_statuses"])
    assert set(c) == {"supported", "insufficient", "contradicted", "irrelevant"}, c
    assert min(c.values()) >= 0.1 * sum(c.values()), c


def test_dev_and_test_template_ids_disjoint(inp, snap):
    d, _ = tg.generate(2, "dev", 11, **gen_kw(inp, snap))
    t, _ = tg.generate(2, "test", 11, **gen_kw(inp, snap))
    ids = lambda ts: {c.presentation["template_id"] for x in ts for c in x.candidates}
    assert ids(d) and ids(t) and not ids(d) & ids(t)


def test_n_decisive():
    assert [tg.n_decisive(n) for n in (1, 2, 3, 6, 12)] == [1, 1, 2, 4, 8]


def test_cli_dev_and_test(tmp_path, capsys, monkeypatch):
    out = tmp_path / "tasks" / "dev"
    assert tg.main(["--split", "dev", "--seed", "3", "--n-per-layer", "1", "--out", str(out)]) == 0
    assert (out / "manifest.json").exists()
    real = tg.load_inputs

    def pend(*a, **k):
        r = real(*a, **k)
        r["structures"] = with_pending(r["structures"])
        return r

    monkeypatch.setattr(tg, "load_inputs", pend)
    assert tg.main(["--split", "test", "--seed", "3", "--out", str(tmp_path / "tasks" / "test")]) == 2
    assert not (tmp_path / "tasks" / "test").exists()


# ------------------------------------------------------------------ second calculator
@slow
def test_verify_with_xlsx_small(inp, snap, tmp_path):
    tasks, golds = tg.generate(2, "dev", 21, snap, inp["structures"], inp["value_sets"], inp["registry"],
                               tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])
    tg.verify_with_xlsx(tasks, golds, tmp_path, snap)
    for t, g in zip(tasks, golds):
        assert set(g.calc_hashes) == {c.cand_id for c in t.candidates}
        for h in g.calc_hashes.values():
            assert abs(D(h["py_total_cny"]) - D(h["xlsx_total_cny"])) <= D("0.01")
            assert len(h["sha256"]) == 64
            assert all(len(x.split(".")[1]) == 2 for x in (h["py_total_cny"], h["xlsx_total_cny"]))


def test_verify_detects_mismatch(inp, snap, tmp_path, monkeypatch):
    tasks, golds = tg.generate(1, "dev", 21, snap, inp["structures"], inp["value_sets"], inp["registry"],
                               tg.DEFAULT_TEMPLATE_POOL, inp["freight_floor"])

    def fake(shipment, terms, snapshot, workdir):
        from quoteguard.calc_py import landed_cost
        r = landed_cost(shipment, terms, snapshot)
        return {"total_cny": r.total_cny + D("1"), "lines": r.lines}

    monkeypatch.setattr(tg, "landed_cost_xlsx", fake)
    with pytest.raises(tg.VerificationError):
        tg.verify_with_xlsx(tasks[:1], golds[:1], tmp_path, snap)
