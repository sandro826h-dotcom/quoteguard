# docs/report

- `技术方案报告.md`：技术方案报告正文（Markdown），按附件 2 大纲组织，定稿后转 PDF（正文 Noto Serif CJK SC，论文图表灰度）。
- 正文中的〔待填：…〕与表格中的“待填”是结果占位，正式实验与真实报价集运行结束后按 `docs/protocol/冻结协议.md` 的评分输出填写，定稿前全部替换。

## 图位

| 图 | 路径（相对报告） | 提供方 |
|---|---|---|
| 图 3-1 系统架构 | `../figures/fig1_architecture.png` | docs/figures/ |
| 图 3-2 方法阶梯 | `../figures/fig2_method_ladder.png` | docs/figures/ |
| 图 4-1 评测集构建与数据流程 | `../figures/fig3_dataflow.png` | docs/figures/ |
| 图 4-2 真实报价集标注流程 | `../figures/fig4_annotation.png` | docs/figures/ |
| 图 5-1 各层级决策正确率 | `../../output/results/formal/fig5_e1_accuracy_by_layer.png` | scripts/make_results.py（灰度） |
| 图 5-2 风险项检出 | `../../output/results/formal/fig6_e2_risk.png` | scripts/make_results.py（灰度） |
| 图 5-3 演示系统决策护照 | 演示系统截图，路径待定 | demo/ |

## 填写顺序

正式结果的表与图由一条命令生成：`PYTHONPATH=src python scripts/make_results.py --preset formal`，输出在 `output/results/formal/`（`tables.md` 含表 5-1 至表 5-10，图 5-1、5-2 为 PNG 与 PDF）。评分输出中没有的项，流水线写“评分输出无此项”，报告对应位置保持〔待填〕另行填写：图片转写准确率、反事实任务字段与出处准确率、无依据宣称的采纳率、M0/M1 自由文本映射的人工抽检一致率、表 5-8 真实报价集、5.10 效益分析。

1. 5.1 执行概况：`runs/spend_ledger.jsonl` 与各批次 manifest。
2. 5.2–5.7（含 5.5 离线基线对照）：`orchestrate score` 输出（`--seed 20261011 --sims 99999 --B 9999`），按 5.11 节逐条判定事前决策规则，再写简介“效果”、5.10 效益分析与 6.1 总结。
3. 5.8 与 2.2 节缺失比例：全部标签与裁决导入并记录哈希、真实报价集运行之后。
4. 4.6 节协议偏离：补正式实验与真实报价集运行中的停批、续跑与其他偏离。
