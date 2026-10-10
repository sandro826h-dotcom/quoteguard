# 预试验重算汇总（2026-10-10，离线）

来源：summary.json（由 scripts/rescore_pilot.py 生成）。dev 集配置 data/taskgen/dev_v3，管线为当前 methods_pipeline.py。组 A 26 条观测（ds_flash、qwen_plus，T-dev-004、T-dev-010），组 B 14 条观测（glm、kimi，T-dev-020），无重复 run_key，无缺失观测，合计 40 条。样本极小，只用于检查流程。

## 调用质量
- 40 次调用 JSON 解析：M2–M4 的抽取调用 24/24 成功；M0/M1 的 8 次中 7 次成功，失败 1 次为 kimi M0（T-dev-020 的 choice 写成货代标签 FWD-15，不是 C1–C4）。
- 截断：无 finish_reason=length。kimi M1 输出 7618 token，为 8000 上限的 95.2%；qwen_plus M1 最大 6803（85%）；ds_flash M1 最大 5342；glm M1 2697。
- 抽取调用输出 token 中位数：ds_flash 1148、qwen_plus 1048、glm 984、kimi 1077，最大 1212。
- reasoning_tokens（由 raw_body 的 usage 重算）：glm 的 M0、M1 与抽取调用均为 2（实际未思考），kimi 为 0，ds_flash 与 qwen_plus 响应无该字段。

## 出处核对与归一化
- 出处降级：4 组配置共 0 个字段被降级（stated 字段 ds_flash 110、qwen_plus 107、glm 52、kimi 48）。
- 归一化未映射项：Customs filing fee 6、FSC 4、燃油附加费 2、清关费 1（附加费名）；未知资质类型 3 个（申报货值、运费 3 倍赔付、按申报货值赔付，上限 20 美元/kg）；kimi 有 2 个 validity 无法读取。

## 评分（score 输出，B=200；组 A 各配置 2 个任务，组 B 各配置 1 个任务）
- 组 A 决策正确率 M2/M3：两配置均 0.5；M4：ds_flash 1.0，qwen_plus 0.5。M2 核价绝对误差均值：ds_flash 0，qwen_plus 58.75；M4 ds_flash 57.14。
- 组 B 决策正确率：M0–M3 与 M4 的 glm 均为 0，M4 kimi 为 1。M2–M4 核价绝对误差均值 266.7 元（M4 kimi 400）。

## 修复前后（组 A，16 个候选，scripts/diagnose_pilot.py 口径）
| 指标 | 665b5fe 前 | 665b5fe 后（日志） | 本次 |
|---|---|---|---|
| 字段差异数 | 58 | 18 | 17 |
| M4 误报三元组 | 18 | 3 | 2 |
| M2 核价有误差的候选 | 14 | 4 | 4 |

M2 核价有误差的 4 个候选：ds_flash T-dev-004 C4（1588.00）、qwen_plus T-dev-004 C1（111.25）、qwen_plus T-dev-010 C3 与 C4（各 150.00）。M4 决策组 A 4 个中 3 个正确。

## 复核触发（M4；M3 不复核）
- 触发复核的报价比例：ds_flash 2/8、qwen_plus 0/8、glm 1/4、kimi 3/4。
- 金标签有风险（冲突、缺失、隐藏费、DDP、资质状态任一）但未触发复核的报价：ds_flash 4/6，qwen_plus 6/6，glm 1/2，kimi 0/2，合计 11 个。
