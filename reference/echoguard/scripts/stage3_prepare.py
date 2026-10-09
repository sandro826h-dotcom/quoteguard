"""Freeze the Stage 3 request plan (offline; no API calls).

Two pre-registered experiments, four arms, 324 requests (3 providers x 9 personas x 3 repeats per arm):

  E5F  global_fixed  v0.2 shelf A and pilot personas; ONE display order for every request, the way an
                     app lists products under a fixed sort rule
  E5R  per_request   same shelf and personas; every request gets its own random order
  R2S  shared        shelf B (12 other public funds from 4 other managers) and 9 new personas; the three
                     providers share the order within a persona/repeat (as Gate 1 / B0)
  R2I  independent   same as R2S, but each provider sees its own random order (as E4)

E5 tests the display remedy for a single-model deployment. R2 replicates the shared-order mechanism on
products, managers and customers not used in stages 1-2. Hypotheses are written into the design
manifest before any response exists (docs/STAGE3_PROTOCOL_20261005.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "runs/live-20261002T160402Z-ddf7f62f/frozen_project"
GATE1_MANIFEST = ROOT / "runs/live-20261002T160402Z-ddf7f62f/pilot/batch_manifest.json"
STAGE2_PLAN = ROOT / "runs/stage2-plan-20261005/design_manifest.json"
SHELF_A = FROZEN / "data/products_pilot_v0.2.csv"
PERSONAS_A = FROZEN / "data/personas_pilot_v0.2.csv"
SHELF_B = ROOT / "data/stage3/products_shelfB_v1.0.csv"
PERSONAS_B = ROOT / "data/stage3/personas_r2_v1.0.csv"
sys.path.insert(0, str(ROOT / "src"))
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.prompting import make_prompt  # noqa: E402
from echoguard.validation import validate_products  # noqa: E402

PLAN_VERSION = "stage3-plan-0.1"
PROVIDERS = ["deepseek", "kimi", "qwen"]
SCENARIO = "S0_BASELINE"
REPEATS = 3
RENDER_FILES = ["prompting.py", "blind_shelf.py", "data_io.py", "suitability.py", "schemas.py"]
EXPERIMENTS = {"E5F": {"shelf": "A", "persona_set": "pilot_v0.2", "order_design": "global_fixed"},
               "E5R": {"shelf": "A", "persona_set": "pilot_v0.2", "order_design": "independent"},
               "R2S": {"shelf": "B", "persona_set": "r2_v1.0", "order_design": "shared"},
               "R2I": {"shelf": "B", "persona_set": "r2_v1.0", "order_design": "independent"}}
BUDGET = {"requests": 324, "estimate": {"USD": 0.27, "CNY": 1.9},
          "estimate_basis": "stage 2 returned usage: 468 calls = USD 0.382 + CNY 2.665, scaled to 324 calls",
          "engineering_stop": {"USD": 0.6, "CNY": 4.0}}

HYPOTHESES = {
    "E5": {"question": "With a single model, does a fixed display order send most picks to the products it lists "
                       "first, and does a random order per request remove that concentration?",
           "primary": "Anchor share: among single picks from a same-group set of at least two eligible products, the "
                      "share that goes to the product listed first in its group under the E5F global order (the "
                      "anchor). Higher in E5F than in E5R. Unit = persona (picks pooled over providers and repeats); "
                      "one-sided exact sign-flip test over personas with single picks in both arms, alpha 0.05.",
           "secondary": ["anchor share by provider in each arm (descriptive)",
                         "manager (issuer) share of single picks in each arm: largest share and HHI",
                         "first-shown share in each arm with a randomization test (E5F: one global unit)",
                         "cross-model similarity E5F vs E5R (descriptive; E5F is also a shared order)"]},
    "R2": {"question": "Does the shared-order mechanism reproduce on a new shelf, new managers and new customers?",
           "primary": "Mean cross-model weighted Jaccard over personas analyzable in both arms is lower in R2I than in "
                      "R2S (one-sided exact sign-flip over personas), alpha 0.05.",
           "secondary": ["first-shown share in R2S and R2I (randomization test; shared / independent units)",
                         "all-three-same-product and pairwise same-product rates, persona-level sign-flip R2S vs R2I",
                         "E2 persistent-cell null + BY in both arms (shelf B cells)",
                         "release gate (policy v3) outcome for both arms; E3 not assessed (no contrast pairs)"]},
}


def stable_seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def shelf_order(prompt: str) -> list[str]:
    shelf = json.loads(prompt.split("### Shelf\n", 1)[1].split("\n\n### Required JSON schema", 1)[0])
    return [s["product_id"] for s in shelf]


def request(exp, provider, persona, repeat, template, products, scenario, order_seed):
    prompt = make_prompt(template, products, persona, scenario, blind=False, order_seed=order_seed)
    spec = EXPERIMENTS[exp]
    return {"run_key": f"{exp}__{provider}__{persona.persona_id}__r{repeat}", "experiment": exp, "kind": "gate1",
            "provider": provider, "persona_id": persona.persona_id, "repeat": repeat, "view": "named",
            "shelf": spec["shelf"], "persona_set": spec["persona_set"], "order_design": spec["order_design"],
            "order_seed": order_seed, "order": shelf_order(prompt), "template": template.name,
            "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}


def global_order_seed(products, template, persona, scenario):
    """Smallest k such that the first-listed product of each group comes from a different manager."""
    pmap = {p.product_id: p for p in products}
    for k in range(1000):
        seed = stable_seed(f"E5F|global|{k}")
        order = shelf_order(make_prompt(template, products, persona, scenario, blind=False, order_seed=seed))
        firsts = {}
        for pid in order:
            firsts.setdefault(pmap[pid].economic_cell, pid)
        if len({pmap[x].issuer_name for x in firsts.values()}) == len(firsts):
            return k, seed, order, firsts
    raise SystemExit("No admissible global order")


def prepare(out: Path, seed: int):
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    for name in RENDER_FILES:
        if sha(ROOT / "src/echoguard" / name) != sha(FROZEN / "src/echoguard" / name):
            raise SystemExit(f"Prompt-rendering source differs from the frozen pilot: {name}")
    gate1 = json.loads(GATE1_MANIFEST.read_text(encoding="utf-8"))
    v02 = FROZEN / "prompts/advisor_prompt_v0.2.md"
    for label, path in (("products", SHELF_A), ("personas", PERSONAS_A),
                        ("scenarios", FROZEN / "data/scenarios_v0.1.json"), ("prompt", v02)):
        if sha(path) != gate1["file_hashes"][label]:
            raise SystemExit(f"Frozen {label} differs from the Gate 1 manifest")
    shelf_a, shelf_b = load_products(SHELF_A), load_products(SHELF_B)
    if validate_products(shelf_b):
        raise SystemExit("Shelf B invalid: " + "; ".join(validate_products(shelf_b)))
    if {p.official_code for p in shelf_a} & {p.official_code for p in shelf_b} or \
            {p.issuer_name for p in shelf_a} & {p.issuer_name for p in shelf_b}:
        raise SystemExit("Shelf B must not reuse stage 1-2 funds or managers")
    people_a, people_b = load_personas(PERSONAS_A), load_personas(PERSONAS_B)
    scenario = json.loads((FROZEN / "data/scenarios_v0.1.json").read_text(encoding="utf-8"))[SCENARIO]
    k, gseed, gorder, anchors = global_order_seed(shelf_a, v02, people_a[0], scenario)
    rng = np.random.default_rng(seed)

    queues = {p: [] for p in PROVIDERS}
    for provider in PROVIDERS:
        blocks = [("E5", pe, r) for pe in people_a for r in range(1, REPEATS + 1)] + \
                 [("R2", pe, r) for pe in people_b for r in range(1, REPEATS + 1)]
        for idx in rng.permutation(len(blocks)):
            kind, persona, rep = blocks[int(idx)]
            pid = persona.persona_id
            if kind == "E5":
                pair = [request("E5F", provider, persona, rep, v02, shelf_a, scenario, gseed),
                        request("E5R", provider, persona, rep, v02, shelf_a, scenario,
                                stable_seed(f"E5R|{pid}|{SCENARIO}|{rep}|{provider}"))]
            else:
                pair = [request("R2S", provider, persona, rep, v02, shelf_b, scenario,
                                stable_seed(f"R2|{pid}|{SCENARIO}|{rep}")),
                        request("R2I", provider, persona, rep, v02, shelf_b, scenario,
                                stable_seed(f"R2|{pid}|{SCENARIO}|{rep}|{provider}"))]
            for j in rng.permutation(2):
                queues[provider].append(pair[int(j)])
    requests = [r for p in PROVIDERS for r in queues[p]]
    for i, r in enumerate(requests):
        r["provider_sequence"] = sum(1 for x in requests[:i] if x["provider"] == r["provider"])
    keys = [r["run_key"] for r in requests]
    assert len(keys) == len(set(keys))
    assert all(r["order"] == gorder for r in requests if r["experiment"] == "E5F")
    counts = {e: sum(1 for r in requests if r["experiment"] == e) for e in EXPERIMENTS}
    assert sum(counts.values()) == BUDGET["requests"]
    stage2 = json.loads(STAGE2_PLAN.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": PLAN_VERSION, "status": "prepared_not_executed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "experiments": EXPERIMENTS, "counts": counts, "total_requests": len(requests),
        "per_provider": {p: len(queues[p]) for p in PROVIDERS},
        "hypotheses": HYPOTHESES, "alpha": 0.05,
        "missing_data_rule": "No imputation, no deletion of models, no re-asking after a received response. "
                             "Abstentions stay in the data; analyses that need recommendations report their coverage.",
        "e5_global_order": {"selection_rule": "smallest k with seed text 'E5F|global|k' whose first-listed product "
                                              "in each group comes from a different manager",
                            "k": k, "order_seed": gseed, "order": gorder, "anchors": anchors},
        "budget": BUDGET,
        "models": gate1["plan"]["models"],
        "gate1_requests": gate1["plan"]["requests"],
        "gate1_transport": gate1["plan"]["transport_parameters"],
        "label_amendments": stage2["label_amendments"],
        "frozen_inputs": {"shelf_A": sha(SHELF_A), "personas_A": sha(PERSONAS_A),
                          "shelf_B": sha(SHELF_B), "personas_B": sha(PERSONAS_B),
                          "scenarios": sha(FROZEN / "data/scenarios_v0.1.json"), "prompt_v0.2": sha(v02)},
        "frozen_input_paths": {"shelf_A": str(SHELF_A.relative_to(ROOT)), "personas_A": str(PERSONAS_A.relative_to(ROOT)),
                               "shelf_B": str(SHELF_B.relative_to(ROOT)), "personas_B": str(PERSONAS_B.relative_to(ROOT))},
        "render_source_sha256": {n: sha(ROOT / "src/echoguard" / n) for n in RENDER_FILES},
        "gate1_manifest_sha256": sha(GATE1_MANIFEST),
        "stage2_plan_sha256": sha(STAGE2_PLAN),
        "prepare_script_sha256": sha(Path(__file__)),
        "request_plan_sha256": digest(requests),
        "api_calls": 0,
    }
    out.mkdir(parents=True)
    with (out / "requests.jsonl").open("x", encoding="utf-8", newline="\n") as f:
        for r in requests:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "design_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"plan": str(out.relative_to(ROOT)), "counts": counts, "total": len(requests),
                      "per_provider": manifest["per_provider"], "global_order_k": k, "anchors": anchors,
                      "request_plan_sha256": manifest["request_plan_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "runs/stage3-plan-20261005")
    ap.add_argument("--seed", type=int, default=20261006)
    a = ap.parse_args()
    prepare(a.out_dir.resolve(), a.seed)
