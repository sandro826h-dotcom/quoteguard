#!/usr/bin/env bash
# 正式实验看守：每 INTERVAL 秒检查三家 run_formal.sh 进程，停了且未完成、未因额度/余额/预算停止时续跑；
# 每 COMMIT_EVERY 秒提交推送一次 runs/ 新记录。运行 DURATION 秒后退出。
cd "$(dirname "$0")/.." || exit 1
INTERVAL=${INTERVAL:-510}; COMMIT_EVERY=${COMMIT_EVERY:-1800}; DURATION=${DURATION:-6600}
start=$(date +%s); last_commit=$start
commit() {
  git add -A runs >/dev/null 2>&1
  git diff --cached --quiet && return 0
  git commit -qm "run: 正式实验运行中快照

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UMPHkvZBpNCAQL7RLobLqu" && git push -q origin HEAD:main && git push -q origin HEAD:claude/practical-wozniak-dgep15
}
while :; do
  line="[$(date -u +%H:%MZ)]"
  for p in deepseek dashscope siliconflow; do
    last=$(tail -1 runs/formal_logs/$p.log)
    if ! pgrep -f "bash scripts/run_formal.sh $p" >/dev/null; then
      if echo "$last" | grep -q "全部批次完成"; then line="$line $p=done"; continue; fi
      if grep -qs '"status": "stopped_' runs/$p-*/manifest.json; then line="$line $p=STOPPED"; continue; fi
      nohup bash scripts/run_formal.sh $p >> runs/formal_logs/$p.log 2>&1 &
      line="$line $p:restart"
    fi
    cur=$(ls -td runs/$p-* 2>/dev/null | head -1)
    line="$line $(basename "$cur")=$(ls "$cur/observations" 2>/dev/null | wc -l)/$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['requests']))" "$cur/plan.json" 2>/dev/null)"
  done
  [ -d runs/think-B ] && line="$line think-B=$(ls runs/think-B/observations | wc -l)"
  echo "$line"
  now=$(date +%s)
  if [ $((now - last_commit)) -ge "$COMMIT_EVERY" ]; then commit && echo "  committed $(git log --oneline -1 | cut -c1-8)"; last_commit=$now; fi
  [ $((now - start)) -ge "$DURATION" ] && { commit; exit 0; }
  sleep "$INTERVAL"
done
