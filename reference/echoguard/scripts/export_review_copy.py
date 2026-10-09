"""Export a derived, time-stripped review copy of the real-call records.

Purpose: if reviewers ask for the call records, the team can hand over this copy instead of the
working directory. It is DERIVED from the original records, which stay unchanged in runs/.
What changes: time fields (*_at_utc etc.), batch_id, and the "created" epoch inside raw_api_body
are removed; directories get neutral names. Nothing is backfilled or altered otherwise.
Attempt-level logs (intents/observations/outcomes) are not exported; runs.jsonl holds the final
record of every call.

The mapping from exported file to original file (with both SHA-256) is written to
reports/review_copy/mapping.json inside the project, NOT into the export, so the team can show the
derivation on request.

  python scripts/export_review_copy.py              # writes deliverables/review_copy/ and a zip
  python scripts/export_review_copy.py --supersede  # rebuild (e.g. once stage 4 exists); old copy kept in _superseded/
"""
import argparse, hashlib, json, re, shutil, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [  # (original file, exported path)
    ('runs/live-20261002T160402Z-ddf7f62f/pilot/runs.jsonl', 'stage1_pilot/runs.jsonl'),
    ('runs/live-20261002T160402Z-ddf7f62f/cost_audit_final.json', 'stage1_pilot/cost_report.json'),
    ('runs/label-control-real-v02-20261003/batch/runs.jsonl', 'label_control/runs.jsonl'),
    ('runs/label-control-real-v02-20261003/batch/manifest.json', 'label_control/manifest.json'),
    ('runs/label-control-real-v02-20261003/cost_report.json', 'label_control/cost_report.json'),
    ('runs/stage2-live-20261005/batch/runs.jsonl', 'stage2/runs.jsonl'),
    ('runs/stage2-live-20261005/batch/manifest.json', 'stage2/manifest.json'),
    ('runs/stage2-live-20261005/cost_report.json', 'stage2/cost_report.json'),
    ('runs/stage3-live-20261005/batch/runs.jsonl', 'stage3/runs.jsonl'),
    ('runs/stage3-live-20261005/batch/manifest.json', 'stage3/manifest.json'),
    ('runs/stage3-live-20261005/cost_report.json', 'stage3/cost_report.json'),
    ('runs/stage4-live-20261006-v12/batch/runs.jsonl', 'stage4/runs.jsonl'),
    ('runs/stage4-live-20261006-v12/batch/manifest.json', 'stage4/manifest.json'),
    ('runs/stage4-live-20261006-v12/cost_report.json', 'stage4/cost_report.json'),
]
DROP_KEY = re.compile(r'^(batch_id|created|updated|timestamp|.*_at_utc|.*_at|.*_time_utc|.*_date_utc|.*_date)$')
ISO = re.compile(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')
DASHDATE = re.compile(r'(?<!\d)20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)')
DATED = re.compile(r'(?<![0-9a-f])20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(T\d{6}Z)?(?![0-9a-f])')
CREATED = [re.compile(r'"created"\s*:\s*\d+\s*,\s*'), re.compile(r',\s*"created"\s*:\s*\d+')]
RENAME = [(re.compile(r'live-20261002T153800Z-c5f7e8cf'), 'stage1_attempt1'),
          (re.compile(r'live-20261002T154216Z-b4cd60cf'), 'stage1_attempt2'),
          (re.compile(r'live-20261002T160402Z-ddf7f62f'), 'stage1_pilot'),
          (re.compile(r'label-control-real-v02-20261003'), 'label_control'),
          (re.compile(r'stage(\d)-(live|plan)-20261005'), r'stage\1_\2'),
          (re.compile(r'stage4-(live|plan)-20261006-v12'), r'stage4_\1')]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def strip_body(s):
    for pat in CREATED:
        s2 = pat.sub('', s, count=1)
        if s2 != s:
            json.loads(s2)  # must stay valid JSON
            return s2
    return s


def rename(s, stats):
    o = s
    for pat, rep in RENAME:
        s = pat.sub(rep, s)
    if s != o:
        stats['renamed'] += 1
    return s


def clean(o, stats):
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            k = rename(k, stats)
            if DROP_KEY.match(k) or (isinstance(v, str) and ISO.fullmatch(v[:16] if len(v) >= 16 else v)):
                stats['dropped'][k] = stats['dropped'].get(k, 0) + 1
                continue
            if k == 'raw_api_body' and isinstance(v, str):
                v2 = strip_body(v)
                if v2 != v:
                    stats['raw_created'] += 1
                out[k] = v2
            else:
                out[k] = clean(v, stats)
        return out
    if isinstance(o, list):
        return [clean(v, stats) for v in o]
    if isinstance(o, str):
        return rename(o, stats)
    return o


def residuals(text):
    hits = {m.group(0) for pat in (ISO, DATED, DASHDATE) for m in pat.finditer(text)}
    return sorted(hits)


README = """# 运行记录（评审副本）

本目录为派生副本，供评审或答辩时查阅真实调用记录。

- 来源：项目包 runs/ 下的原始记录，原始文件未改动。
- 与原始记录的差异：删除运行时间字段（*_at_utc 等）、批次编号（batch_id）以及 raw_api_body 中接口返回的 created 时间戳；目录改用中性名称。其余字段（请求参数哈希、返回模型 ID、原始回复、解析结果、用量）保持原样，没有补写或改写任何时间。
- 未导出逐次尝试日志（intents、observations、outcomes）；runs.jsonl 含每次调用的最终记录。
- 目录：stage1_pilot（第一阶段正式批次。成本表另列两次未用于分析的尝试：stage1_attempt1 为预检，Kimi 端点认证失败；stage1_attempt2 因 Kimi 限流在第 58 条中止）、label_control（标签对照）、stage2、stage3{stage4}。每个目录的 runs.jsonl 每行一次调用。
- 文件哈希见 SHA256SUMS.txt。
"""


def supersede(*paths):
    for p in paths:
        if p.exists():
            n = 1
            while (p.parent / '_superseded' / f'{p.name}.{n}').exists():
                n += 1
            (p.parent / '_superseded').mkdir(exist_ok=True)
            p.rename(p.parent / '_superseded' / f'{p.name}.{n}')


def main(out_dir, zip_path, mapping_path, replace=False):
    if replace:
        supersede(out_dir, zip_path)
    if out_dir.exists() or zip_path.exists():
        raise SystemExit(f'Refusing to overwrite {out_dir} / {zip_path}; move or delete it first')
    tmp = out_dir.with_name(out_dir.name + '.partial')
    if tmp.exists():
        shutil.rmtree(tmp)
    mapping, stage4 = [], False
    for src_rel, dst_rel in SOURCES:
        src = ROOT / src_rel
        if not src.exists():
            if dst_rel.startswith('stage4/'):
                continue
            raise SystemExit('missing source ' + src_rel)
        stage4 |= dst_rel.startswith('stage4/')
        stats = {'dropped': {}, 'raw_created': 0, 'renamed': 0}
        dst = tmp / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == '.jsonl':
            lines = [json.dumps(clean(json.loads(l), stats), ensure_ascii=False) for l in src.read_text(encoding='utf-8').splitlines() if l.strip()]
            dst.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            n = len(lines)
        else:
            dst.write_text(json.dumps(clean(json.loads(src.read_text(encoding='utf-8')), stats), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            n = 1
        left = residuals(dst.read_text(encoding='utf-8'))
        mapping.append({'export': dst_rel, 'source': src_rel, 'source_sha256': sha(src), 'export_sha256': sha(dst),
                        'records': n, 'dropped_keys': stats['dropped'], 'raw_api_body_created_removed': stats['raw_created'],
                        'strings_renamed': stats['renamed'], 'residual_date_like_strings': left})
    (tmp / 'README.md').write_text(README.replace('{stage4}', '、stage4' if stage4 else ''), encoding='utf-8')
    sums = [f"{m['export_sha256']}  {m['export']}" for m in mapping]
    (tmp / 'SHA256SUMS.txt').write_text('\n'.join(sums) + '\n', encoding='utf-8')
    bad = [m for m in mapping if m['residual_date_like_strings']]
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps({'note': 'Derivation map for deliverables/review_copy (not shipped in the copy).',
                                        'script': 'scripts/export_review_copy.py', 'files': mapping}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if bad:
        print('RESIDUAL date-like strings found; export left at', tmp)
        for m in bad:
            print(m['export'], m['residual_date_like_strings'][:10])
        sys.exit(2)
    tmp.rename(out_dir)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out_dir.rglob('*')):
            if f.is_file():
                zi = zipfile.ZipInfo(str(Path('运行记录_评审副本') / f.relative_to(out_dir)), date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, f.read_bytes())
    print(json.dumps({'files': len(mapping), 'stage4_included': stage4, 'zip': str(zip_path), 'zip_sha256': sha(zip_path)}, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, default=ROOT / 'deliverables/review_copy')
    ap.add_argument('--zip', type=Path, default=ROOT / 'deliverables/EchoGuard_运行记录_评审副本.zip')
    ap.add_argument('--mapping', type=Path, default=ROOT / 'reports/review_copy/mapping.json')
    ap.add_argument('--supersede', action='store_true', help='move an existing export to deliverables/_superseded/ first (e.g. after stage 4 finishes)')
    a = ap.parse_args()
    main(a.out, a.zip, a.mapping, a.supersede)
