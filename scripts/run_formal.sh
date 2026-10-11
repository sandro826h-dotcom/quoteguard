#!/usr/bin/env bash
# 按冻结协议附录的命令序列运行一家服务商的正式实验：A 阶段转写 → 补跑 → B 阶段 → 补跑（deepseek 再接思考组）。
# 上一批次状态不是 completed（额度、余额、预算、连续失败等停止）时脚本停在该处，排除原因后重新执行本脚本即从断点续跑。
# 用法：nohup bash scripts/run_formal.sh deepseek|dashscope|siliconflow > runs/formal_logs/<P>.log 2>&1 &
set -u
P="$1"
H=29a08030798cf637400a338e821f4bb6cc74bde5450817d5fbc51966f3837ac8
T=data/tasks/test/tasks
W=runs/formal_inputs
export PYTHONPATH=src QG_PROXY_AUTH="${QG_PROXY_AUTH-1}"   # 本地运行时设 QG_PROXY_AUTH=0 并提供三家 *_API_KEY
E=(--experiment formal --kind formal --rates configs/rates.json --protocol-sha256 "$H" --seed 20261010)
case "$P" in
  deepseek) C=ds_pro,ds_flash; CUR=USD; CAP=12; PREFIXES="deepseek- think-" ;;
  dashscope) C=qwen_max,qwen_plus; CUR=CNY; CAP=140; PREFIXES="dashscope-" ;;
  siliconflow) C=glm,kimi; CUR=CNY; CAP=100; PREFIXES="siliconflow-" ;;
  *) echo "unknown provider $P"; exit 2 ;;
esac

log() { echo "[$(date -u +%FT%TZ)] $*"; }

# 本服务商上限减去账本中本服务商正式批次已记支出（第七节）
budget() {
  python3 - "$CUR" "$CAP" $PREFIXES <<'PY'
import json, sys, math
cur, cap, prefixes = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
spent = 0.0
try:
    for line in open("runs/spend_ledger.jsonl", encoding="utf-8"):
        if line.strip():
            e = json.loads(line)
            if e["currency"] == cur and any(e["batch_id"].startswith(p) for p in prefixes):
                spent += e["amount"]
except FileNotFoundError:
    pass
print(f"{cur}={math.floor((cap - spent) * 10000) / 10000:.4f}")
PY
}

status() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['status'])" "runs/$1/manifest.json" 2>/dev/null || echo none; }

# 运行一个批次；批次目录已存在且 completed 时跳过；返回非 0 表示停止
run_batch() {  # plan batch_id
  local plan="$1" bid="$2"
  if [ "$(status "$bid")" = completed ]; then log "$bid 已完成，跳过"; return 0; fi
  # 续跑沿用该批次计划中的上限（executor 要求与计划一致）
  local b; b=$(python3 -c "import json,sys;print(','.join(f'{k}={v}' for k,v in json.load(open(sys.argv[1]))['budget'].items()))" "$plan")
  log "run $bid plan=$plan budget=$b"
  python3 -m quoteguard.orchestrate run --plan "$plan" --mode live --batch-id "$bid" --confirm-budget "$b" --protocol-sha256 "$H"
  local s; s=$(status "$bid"); log "$bid status=$s"
  [ "$s" = completed ]
}

# 主批次 + 补跑；成功时回显批次目录列表（逗号分隔）
stage() {  # plan batch_id  -> sets DIRS
  local plan="$1" bid="$2"
  run_batch "$plan" "$bid" || return 1
  DIRS="runs/$bid"
  local rp="$W/plans/$(basename "$plan" .json)-retry.json"
  if [ ! -f "$rp" ] && [ "$(status "$bid-retry")" = none ]; then
    python3 scripts/make_retry_plan.py "runs/$bid" --out "$rp" --budget "$(budget)"
  fi
  if [ -f "$rp" ]; then
    # 补跑只运行一次；未完成的仍按续跑规则处理
    run_batch "$rp" "$bid-retry" || return 1
    DIRS="$DIRS,runs/$bid-retry"
  fi
}

plan() {  # methods configs tag [transcriptions]
  local tr=(); [ -n "${4:-}" ] && tr=(--transcriptions "$4")
  python3 -m quoteguard.orchestrate plan --tasks "$T" --configs "$2" --methods "$1" --tag "$3" \
    --budget "$(budget)" --out "$W" "${tr[@]}" "${E[@]}"
}

# 转写（A 阶段）
[ -f "$W/plans/formal-$P-A.json" ] || plan M0,M1,M2,M3 "$C" "$P" || exit 1
stage "$W/plans/formal-$P-A.json" "$P-A" || { log "A 阶段停止"; exit 3; }
A_DIRS="$DIRS"
# B 阶段
[ -f "$W/plans/formal-$P-B.json" ] || plan M0,M1,M2,M3 "$C" "$P" "$A_DIRS" || exit 1
stage "$W/plans/formal-$P-B.json" "$P-B" || { log "B 阶段停止"; exit 3; }
if [ "$P" = deepseek ]; then
  [ -f "$W/plans/formal-think-B.json" ] || plan M1 ds_flash_think think "$A_DIRS" || exit 1
  stage "$W/plans/formal-think-B.json" think-B || { log "思考组停止"; exit 3; }
fi
log "$P 全部批次完成"
