import json
import re
from pathlib import Path

import pytest

from quoteguard import inputs as I
from quoteguard import prompts
from quoteguard import taskgen as tg
from quoteguard.inputs import (FORBIDDEN_WORDS, QuoteInput, TaskInput, build_task_input, freight_floor_text,
                               param_text, png_pages, registry_text, render_task, shipment_text)
from quoteguard.params import load_snapshot
from quoteguard.render import RenderResult
from quoteguard.schema import Task

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "data" / "tasks" / "dev" / "tasks"
SPEC = ROOT / "docs" / "plan" / "方法实现规格.md"


def all_tasks():
    for f in sorted(TASK_DIR.glob("*.json")):
        yield Task.from_dict(json.loads(f.read_text(encoding="utf-8")))


def load_task(n=0):
    """The n-th task file (sorted); the dev set may be regenerated, so no task id is assumed."""
    return list(all_tasks())[n]


@pytest.fixture(scope="module")
def inp():
    return tg.load_inputs()


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(tg.DEFAULT_SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """Tasks that together cover every modality, rendered once."""
    out = {}
    mods = set()
    for n, t in enumerate(all_tasks(), start=1):
        m = {c.presentation["modality"] for c in t.candidates}
        if n <= 3 or (m - mods):
            out[t.task_id] = (t, render_task(t, tmp_path_factory.mktemp(t.task_id)))
            mods |= m
        if len(out) >= 6 and mods >= {"text", "table", "image"}:
            break
    assert mods >= {"text", "table", "image"}
    return out


def test_render_task_covers_all_candidates(rendered):
    for t, res in rendered.values():
        assert list(res) == [c.cand_id for c in t.candidates]
        for c in t.candidates:
            assert res[c.cand_id].text_view and res[c.cand_id].files


def test_shipment_text_template():
    t = next(x for x in all_tasks() if x.shipment.dest.addr_type == "commercial"
             and not x.shipment.dest.fba_code.startswith("NONFBA"))
    s = t.shipment
    txt = shipment_text(t)
    g = s.goods[0]
    assert f"{g.desc}，{g.qty} 件，单价 " in txt and "货值合计：" in txt and "美元" in txt
    assert {"express": "运输方式：快递。", "air": "运输方式：空派。", "sea": "运输方式：海派。"}[s.mode] in txt
    assert f"共 {sum(c.n for c in s.cartons)} 箱" in txt
    assert f"目的仓 {s.dest.fba_code}，邮编 {s.dest.zip}，地址类型：商业，是否偏远地区：" in txt
    assert f"发运后 {s.deadline_days} 天内到仓" in txt and s.ship_date in txt
    assert g.hts not in txt  # HTS code is not a whitelisted field


def test_build_task_input_mapping_and_modalities(rendered):
    for t, res in rendered.values():
        ti = build_task_input(t, res)
        assert ti.task_id == t.task_id
        assert [q.label for q in ti.quotes] == [f"报价{k}" for k in range(1, len(t.candidates) + 1)]
        assert ti.label_to_cand() == {f"报价{k}": c.cand_id for k, c in enumerate(t.candidates, 1)}
        for q, c in zip(ti.quotes, t.candidates):
            assert q.cand_id == c.cand_id and q.modality == c.presentation["modality"]
            if q.modality == "image":
                assert q.text is None and q.images and all(p.endswith(".png") for p in q.images)
                assert not q.transcribed
            elif q.modality == "table":
                assert q.text == res[q.cand_id].text_view.strip() and "|" in q.text and not q.images
            else:
                assert q.text == res[q.cand_id].text_view.strip() and not q.images


def test_image_text_view_never_in_input(rendered):
    for t, res in rendered.values():
        ti = build_task_input(t, res)
        with pytest.raises(ValueError):
            if ti.image_quotes():
                ti.quotes_text()
            else:
                raise ValueError
        for q in ti.image_quotes():
            assert res[q.cand_id].text_view not in json.dumps(I.image_ref(q.images[0]))


def test_forbidden_words_absent(rendered, inp):
    reg = tg.model_registry_snapshot(inp["registry"], "dev")
    texts = [registry_text(reg)]
    for t, res in rendered.values():
        ti = build_task_input(t, res)
        texts.append(ti.shipment_text)
        for q in ti.quotes:
            if q.text is not None:
                texts.append(q.text)
    for txt in texts:
        low = txt.lower()
        for w in FORBIDDEN_WORDS:
            assert w not in low, w
    assert len(FORBIDDEN_WORDS) == 16


def test_png_pages_order_and_filter():
    files = ["/x/C4_10.png", "/x/C4_2.png", "/x/C4.png", "/x/C4.txt", "/x/C4_3.png"]
    assert png_pages(files) == ["/x/C4.png", "/x/C4_2.png", "/x/C4_3.png", "/x/C4_10.png"]


def test_multipage_image_quote_passes_all_pages(rendered):
    t, res = next(v for v in rendered.values() if any(c.presentation["modality"] == "image" for c in v[0].candidates))
    cid = next(c.cand_id for c in t.candidates if c.presentation["modality"] == "image")
    r = res[cid]
    fake = dict(res)
    fake[cid] = RenderResult(["/p/%s_2.png" % cid, "/p/%s.png" % cid, "/p/%s_3.png" % cid],
                             r.text_view, r.span_map, r.render_hash, r.meta)
    ti = build_task_input(t, fake)
    q = next(q for q in ti.quotes if q.cand_id == cid)
    assert q.images == [f"/p/{cid}.png", f"/p/{cid}_2.png", f"/p/{cid}_3.png"]


def test_registry_text(inp):
    reg = tg.model_registry_snapshot(inp["registry"], "dev")
    txt = registry_text(reg)
    for r in reg["fmc"]:
        assert r["name"] in txt and r["license_no"] in txt
    for r in reg["cn"]:
        assert r["reg_no"] in txt
    assert "已吊销" in txt and "有效" in txt
    aux = inp["registry"]["generator_aux"]["dev"]["unregistered_entities"]
    assert all(a not in txt for a in aux)


def test_param_text(snap, inp):
    p = param_text(snap, inp["freight_floor"], "6109.10.00", inp["value_sets"])
    assert p["snapshot_date"] == "2026-10-09" and p["fx"] == "6.733"
    assert p["mfn"] == "16.5%" and p["s301"] == "7.5%" and p["s301_2026"] == "12.5%"
    assert p["duty_rate"] == "36.5%"
    assert "空运" in p["freight_floor_text"] and "32.79" in p["freight_floor_text"]
    assert "快递：无参照值" in p["freight_floor_text"]
    assert set(p) == set(prompts.placeholders(prompts.M1)) - {"shipment_text", "registry_text", "quotes_text"}
    vs = p["value_sets_text"]
    assert "计泡系数 5000 或 6000" in vs and "燃油附加费 0、10% 或 20%" in vs
    assert "整票最低计费重 0、45 或 100 kg" in vs and "ISF 申报费 0 或 200 元" in vs
    assert "报关费 0、150 或 400 元" in vs and "包税条款为不包税、全包、包关税与 MPF、只包关税之一" in vs
    assert vs.endswith("之一") and "。" not in vs
    for k in ("eligible_fields", "conflict_fields", "surcharge_amounts", "sources", "id"):
        assert k not in vs


def test_freight_floor_text_lists_values(inp):
    txt = freight_floor_text(inp["freight_floor"])
    assert "海运：无参照值" in txt


def test_task_input_untranscribed_guard():
    ti = TaskInput("T", "s", [QuoteInput(1, "C1", "image", None, ["a.png"], False)])
    with pytest.raises(ValueError):
        ti.quotes_text()


def test_non_fba_destination_wording():
    t = load_task()
    d = t.shipment.dest
    d.fba_code, d.zip, d.addr_type, d.remote = "NONFBA-AK", "99501", "residential", True
    txt = shipment_text(t)
    assert "收货地址，邮编 99501，地址类型：住宅，是否偏远地区：是" in txt
    assert "目的仓" not in txt and "NONFBA" not in txt


def test_freeze_inputs(rendered, inp, snap, tmp_path):
    reg = tg.model_registry_snapshot(inp["registry"], "dev")
    t, res = next(iter(rendered.values()))
    files = I.freeze_inputs(t, res, tmp_path, registry_snapshot=reg, snapshot=snap,
                            freight_floor=inp["freight_floor"], value_sets=inp["value_sets"])
    assert set(files) == {f"tasks/{t.task_id}.json", "registry_text.txt"}
    from quoteguard.util import sha256_file
    for rel, h in files.items():
        assert sha256_file(tmp_path / rel) == h
    doc = json.loads((tmp_path / f"tasks/{t.task_id}.json").read_text(encoding="utf-8"))
    assert doc["label_to_cand"]["报价1"] == t.candidates[0].cand_id
    assert doc["param_text"]["fx"] == "6.733" and doc["shipment_text"] == shipment_text(t)
    for q in doc["quotes"]:
        assert ("images" in q) == (q["modality"] == "image")
        for im in q.get("images", []):
            assert im["sha256"] == sha256_file(im["path"])
    texts = [doc["shipment_text"], (tmp_path / "registry_text.txt").read_text(encoding="utf-8")]
    texts += [q["text"] for q in doc["quotes"] if "text" in q]
    for txt in texts:
        assert not any(w in txt.lower() for w in FORBIDDEN_WORDS)
    again = I.freeze_inputs(t, res, tmp_path, registry_snapshot=reg, snapshot=snap, freight_floor=inp["freight_floor"],
                            value_sets=inp["value_sets"])
    assert again == files


def test_mpf_text_matches_snapshot_sentence(snap):
    line = I.mpf_text(snap)
    assert line in prompts.M1
    assert "0.3464%" in line and "34.58" in line and "670.86" in line and "2.77" in line


def test_registry_text_oti_descriptions_and_type_check():
    reg = {"fmc": [{"name": "SYN-A", "license_no": "L1", "oti_type": "NVOCC", "status": "active"},
                   {"name": "SYN-B", "license_no": "L2", "oti_type": "OFF", "status": "active"}], "cn": []}
    txt = registry_text(reg)
    assert "NVOCC（无船承运人）" in txt and "OFF（海运货代，非无船承运人）" in txt
    reg["fmc"][1]["oti_type"] = False  # YAML 1.1 reads an unquoted OFF as False
    with pytest.raises(TypeError):
        registry_text(reg)


def test_forbidden_words_all_dev_tasks_and_m1_full_text(inp, snap, tmp_path_factory):
    reg = tg.model_registry_snapshot(inp["registry"], "dev")
    # text that varies per task or is substituted into the prompts; the template's own key names
    # (for example the "conflicts" key of the answer format) are not part of the model-facing data
    fixed = [registry_text(reg)] + list(param_text(snap, inp["freight_floor"], "6109.10.00",
                                                   inp["value_sets"]).values())
    for k in ("eligible_fields", "conflict_fields"):
        assert not any(k in t for t in fixed)
    n_tasks = 0
    for t in all_tasks():
        res = render_task(t, tmp_path_factory.mktemp(t.task_id))
        ti = build_task_input(t, res)
        texts = fixed + [ti.shipment_text] + [q.text for q in ti.quotes if q.text is not None]
        for txt in texts:
            low = txt.lower()
            assert not [w for w in FORBIDDEN_WORDS if w in low]
        n_tasks += 1
    assert n_tasks >= 1
