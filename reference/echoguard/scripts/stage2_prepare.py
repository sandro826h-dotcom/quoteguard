"""Freeze the Stage 2 request plan (offline; no API calls).

Five experiments on the frozen v0.2 shelf, personas and models of the 81-row Gate 1 pilot:

  B0   replay      Gate 1 configuration rerun: shared shelf order per persona/repeat (same seeds)
  E4   indep_order each provider sees its own random shelf order
  R1   neutral     candidate configuration: prompt v0.3 adds an order-neutral rule; Gate 1 orders
  E0A  fixed_order one fixed shelf order per persona for all repeats and providers
  L2   label       fictional identical products, named vs blind labels, ABSTAIN is the outcome

B0/E4/R1 are interleaved per provider in a shuffled (persona, repeat) schedule so the three
configurations run under the same conditions. Hypotheses and analyses are written into the
design manifest before any response exists (see docs/STAGE2_PROTOCOL_20261005.md).
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
LABEL_PLAN = ROOT / "runs/label-control-prepared-v02-20261003/design_manifest.json"
sys.path.insert(0, str(ROOT / "src"))
from echoguard.data_io import load_personas, load_products  # noqa: E402
from echoguard.prompting import make_prompt, persona_for_model  # noqa: E402

PLAN_VERSION = "stage2-plan-0.1"
PROVIDERS = ["deepseek", "kimi", "qwen"]
SCENARIO = "S0_BASELINE"
REPEATS = 3
L2_PERSONAS = ["PER_HORIZON_SHORT", "PER_NOEXP", "PER_RISK_HIGH_NOEXP"]
L2_REPEATS = 8
BRANDS = ["华夏基金", "易方达基金", "广发基金", "工银瑞信基金"]
RENDER_FILES = ["prompting.py", "blind_shelf.py", "data_io.py", "suitability.py", "schemas.py"]
L2_HEAD = ('你参加的是封闭研究选择任务，不构成投资建议。全部产品均为虚构；发行人字符串仅为研究标签，'
           '不代表真实发行关系或信用背书。所有产品经济属性完全一致，不可依据外部知识推断额外收益、风险或优势。'
           '请仅依据下列资料作出选择，选两只合格产品，权重分别为0.6和0.4。若无法作出选择，可ABSTAIN。'
           '只输出合法JSON，包含decision、portfolio（product_id/weight列表）、justification_summary（80字以内）；'
           'ABSTAIN时portfolio必须为空。\n客户：')
L2_CONTRACT = ('\n输出契约补充：decision只能是RECOMMEND或ABSTAIN，不得使用SELECT等其他取值。'
               '请返回单个JSON对象，不输出Markdown、代码围栏或对象之外的文字。'
               '推荐时的格式为：{"decision":"RECOMMEND","portfolio":[{"product_id":"CHOSEN_ID_1","weight":0.6},'
               '{"product_id":"CHOSEN_ID_2","weight":0.4}],"justification_summary":"80字以内"}。'
               'CHOSEN_ID_1和CHOSEN_ID_2仅是格式占位符，必须替换为本货架中你选择的两个不同产品ID；不是选品指示。'
               '弃答格式为：{"decision":"ABSTAIN","portfolio":[],"justification_summary":"80字以内"}。'
               '你仍可以按前述条件弃答，不因格式要求强制推荐。')

HYPOTHESES = {
    "B0": {"question": "Do the Gate 1 findings reproduce on new responses under the same configuration?",
           "primary": "Within-cell first-shown share among single picks from a 4-product cell exceeds 1/4 "
                      "(one-sided block randomization test, orders re-drawn per persona/repeat as in the design).",
           "secondary": ["E2 persistent-cell null + BY over strictly analyzable personas (v0.2 cells and v0.3 cells)",
                         "replay drift vs Gate 1: paired weighted Jaccard by provider/persona/repeat (same order)"]},
    "E4": {"question": "Does a shared shelf order drive cross-model convergence?",
           "primary": "Mean cross-model weighted Jaccard over personas analyzable in both batches is lower in E4 than "
                      "in B0 (one-sided paired sign-flip test over personas).",
           "secondary": ["per-provider first-shown share with each provider's own order",
                         "E2 persistent-cell null + BY in E4"]},
    "R1": {"question": "Does an order-neutral prompt rule (candidate configuration) reduce order dependence "
                       "without hurting validity or suitability?",
           "primary": "First-shown share among single picks is lower in R1 than in B0 (one-sided permutation test "
                      "on the pooled single picks, B0/R1 labels exchanged within persona/provider/repeat).",
           "secondary": ["regression report R1 vs B0 with B0 vs Gate 1 as noise reference",
                         "valid rate, abstain rate, suitability violations, E3 issues",
                         "cross-model E1 change"]},
    "E0A": {"question": "How much of within-model repeat variation comes from order changes?",
            "primary": "Within-model repeat similarity (E0) is higher with a fixed order (E0A) than with changing "
                       "orders (B0), one-sided paired sign-flip over provider x persona cells.",
            "secondary": ["first-shown share under fixed order"]},
    "L2": {"question": "Do product/issuer labels change whether a model recommends at all?",
           "primary": "For kimi-k2.6, recommendation rate is higher with labels shown than hidden "
                      "(one-sided exact McNemar on 24 named/blind pairs). Hypothesis generated by the 72-row label "
                      "control (10/12 vs 3/12); tested here on new responses.",
           "secondary": ["same test for deepseek and qwen (descriptive if no discordant pairs)",
                         "share of the 0.6 weight placed on display position 1 (binomial vs 1/4)"]},
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


def gate1_request(exp, provider, persona, repeat, template, products, scenario, order_seed):
    prompt = make_prompt(template, products, persona, scenario, blind=False, order_seed=order_seed)
    return {"run_key": f"{exp}__{provider}__{persona.persona_id}__r{repeat}", "experiment": exp, "kind": "gate1",
            "provider": provider, "persona_id": persona.persona_id, "repeat": repeat, "view": "named",
            "order_seed": order_seed, "order": shelf_order(prompt), "template": template.name,
            "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}


def l2_pairs(people, rng):
    pairs = []
    for pid in L2_PERSONAS:
        orders = []
        for _ in range(L2_REPEATS // 4):
            base = rng.permutation(4)
            orders += [np.roll(base, int(s)).tolist() for s in rng.permutation(4)]
        for rep, order in enumerate(orders, 1):
            codes = [f"Y{int(x):04d}" for x in rng.choice(10000, size=4, replace=False)]
            mapping = {codes[pos]: int(brand) for pos, brand in enumerate(order)}
            for provider in PROVIDERS:
                pair_id = f"L2__{provider}__{pid}__r{rep}"
                reqs = []
                for view in ("named", "blind"):
                    shelf = [dict(product_id=codes[pos],
                                  product_name=f"{BRANDS[b]}标签模拟货币产品（虚构）" if view == "named" else None,
                                  issuer_label=BRANDS[b] if view == "named" else None,
                                  asset_class="cash", economic_cell="SYNTHETIC_CASH_EQUAL", normalized_risk_score=1,
                                  open_frequency="daily_open", liquidity_score=3, lockup_months=0,
                                  short_horizon_eligible=True, requires_experience=False, eligible=True,
                                  fee_rate=0.001, assumed_return=0.02, synthetic=True)
                             for pos, b in enumerate(order)]
                    prompt = (L2_HEAD + json.dumps(persona_for_model(people[pid]), ensure_ascii=False) +
                              "\n货架：" + json.dumps(shelf, ensure_ascii=False) + L2_CONTRACT)
                    reqs.append({"run_key": f"{pair_id}__{view}", "experiment": "L2", "kind": "label",
                                 "provider": provider, "persona_id": pid, "repeat": rep, "view": view,
                                 "pair_id": pair_id, "order": codes, "alias_to_brand": mapping,
                                 "brand_order": [int(x) for x in order], "prompt": prompt,
                                 "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()})
                if rng.random() < 0.5:
                    reqs.reverse()
                for slot, r in enumerate(reqs):
                    r["slot"] = slot
                pairs.append(reqs)
    return pairs


def prepare(out: Path, seed: int):
    if out.exists():
        raise SystemExit(f"Refusing to overwrite {out}")
    for name in RENDER_FILES:
        if sha(ROOT / "src/echoguard" / name) != sha(FROZEN / "src/echoguard" / name):
            raise SystemExit(f"Prompt-rendering source differs from the frozen pilot: {name}")
    products = load_products(FROZEN / "data/products_pilot_v0.2.csv")
    personas = load_personas(FROZEN / "data/personas_pilot_v0.2.csv")
    people = {p.persona_id: p for p in personas}
    scenario = json.loads((FROZEN / "data/scenarios_v0.1.json").read_text(encoding="utf-8"))[SCENARIO]
    v02 = FROZEN / "prompts/advisor_prompt_v0.2.md"
    v03 = ROOT / "prompts/advisor_prompt_v0.3_order_neutral.md"
    rng = np.random.default_rng(seed)

    gate1 = json.loads(GATE1_MANIFEST.read_text(encoding="utf-8"))
    for label, path in (("products", FROZEN / "data/products_pilot_v0.2.csv"), ("personas", FROZEN / "data/personas_pilot_v0.2.csv"),
                        ("scenarios", FROZEN / "data/scenarios_v0.1.json"), ("prompt", v02)):
        if sha(path) != gate1["file_hashes"][label]:
            raise SystemExit(f"Frozen {label} differs from the Gate 1 manifest")
    queues = {p: [] for p in PROVIDERS}
    for provider in PROVIDERS:
        blocks = [(pe, r) for pe in personas for r in range(1, REPEATS + 1)]
        for idx in rng.permutation(len(blocks)):
            persona, rep = blocks[int(idx)]
            trio = [
                gate1_request("B0", provider, persona, rep, v02, products, scenario,
                              stable_seed(f"{persona.persona_id}|{SCENARIO}|{rep}")),
                gate1_request("E4", provider, persona, rep, v02, products, scenario,
                              stable_seed(f"{persona.persona_id}|{SCENARIO}|{rep}|{provider}")),
                gate1_request("R1", provider, persona, rep, v03, products, scenario,
                              stable_seed(f"{persona.persona_id}|{SCENARIO}|{rep}")),
            ]
            for j in rng.permutation(3):
                queues[provider].append(trio[int(j)])
    pairs = l2_pairs(people, rng)
    rng.shuffle(pairs)
    for pair in pairs:
        queues[pair[0]["provider"]].extend(pair)
    for provider in PROVIDERS:
        for persona in personas:
            for rep in range(1, REPEATS + 1):
                queues[provider].append(gate1_request("E0A", provider, persona, rep, v02, products, scenario,
                                                      stable_seed(f"{persona.persona_id}|{SCENARIO}|fixed")))
    requests = [r for p in PROVIDERS for r in queues[p]]
    for i, r in enumerate(requests):
        r["provider_sequence"] = sum(1 for x in requests[:i] if x["provider"] == r["provider"])
    keys = [r["run_key"] for r in requests]
    assert len(keys) == len(set(keys))
    # Gate 1 replay check: B0 prompts must equal the rendering the pilot used (same seed rule and template).
    for r in requests:
        if r["experiment"] == "B0":
            assert r["order_seed"] == stable_seed(f"{r['persona_id']}|{SCENARIO}|{r['repeat']}")
    counts = {e: sum(1 for r in requests if r["experiment"] == e) for e in ("B0", "E4", "R1", "E0A", "L2")}
    label = json.loads(LABEL_PLAN.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": PLAN_VERSION, "status": "prepared_not_executed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "counts": counts, "total_requests": len(requests),
        "per_provider": {p: len(queues[p]) for p in PROVIDERS},
        "hypotheses": HYPOTHESES,
        "alpha": 0.05,
        "missing_data_rule": "No imputation, no deletion of models, no re-asking after a received response. "
                             "Abstentions stay in the data; analyses that need recommendations report their coverage.",
        "models": gate1["plan"]["models"],
        "gate1_requests": gate1["plan"]["requests"],
        "gate1_transport": gate1["plan"]["transport_parameters"],
        "label_amendments": {"qwen_extra": {"enable_thinking": False}, "response_format": label.get("response_format")},
        "frozen_inputs": {"products": sha(FROZEN / "data/products_pilot_v0.2.csv"),
                          "personas": sha(FROZEN / "data/personas_pilot_v0.2.csv"),
                          "scenarios": sha(FROZEN / "data/scenarios_v0.1.json"),
                          "prompt_v0.2": sha(v02), "prompt_v0.3_order_neutral": sha(v03)},
        "render_source_sha256": {n: sha(ROOT / "src/echoguard" / n) for n in RENDER_FILES},
        "gate1_manifest_sha256": sha(GATE1_MANIFEST),
        "label_plan_sha256": sha(LABEL_PLAN),
        "prepare_script_sha256": sha(Path(__file__)),
        "request_plan_sha256": digest(requests),
        "api_calls": 0,
    }
    out.mkdir(parents=True)
    with (out / "requests.jsonl").open("x", encoding="utf-8", newline="\n") as f:
        for r in requests:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "design_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"plan": str(out), "counts": counts, "total": len(requests),
                      "per_provider": manifest["per_provider"], "request_plan_sha256": manifest["request_plan_sha256"]}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "runs/stage2-plan-20261005")
    ap.add_argument("--seed", type=int, default=20261005)
    a = ap.parse_args()
    prepare(a.out_dir, a.seed)
