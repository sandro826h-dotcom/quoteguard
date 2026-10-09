# 项目记录

新记录写在最后。

## 2026-10-09 仓库初始化
- 建立仓库骨架，加入 CLAUDE.md、README.md、方案文档（docs/plan/AIC_最终方案.md 为设计依据，docs/plan/AIC_选题方案.md 第二节为比赛要求核对）和云端配置说明（docs/CLOUD_SETUP.md、docs/CLOUD_KICKOFF.md）。
- 从上一个项目复制迁移参考代码到 reference/echoguard/，只读，已去掉学校信息和成员姓名。
- 尚未进行任何模型调用。
- 未决：真实调用的预算上限待定。

## 2026-10-09 子代理与模型分工
- 新增 .claude/agents/：implementer（Sonnet，编码与测试）、bulk-data（Haiku，录入与匿名化扫描）、reviewer（Opus，只读独立审查）。
- CLAUDE.md 新增“模型分工”一节：主会话用 Opus 做判断，按表委托，reviewer 设四个强制检查点。

## 2026-10-09 模型阵容
- 定为 6 个配置：强模型档 DeepSeek-V4-Pro、qwen3-max、GLM-5.3、Kimi-K2.6；低成本档 DeepSeek-V4-Flash、qwen-plus。GLM 与 Kimi 经硅基流动调用。已写入 AIC_最终方案.md 第六节与 CLAUDE.md。
- 未决：真实调用预算上限。

## 2026-10-09 工作块①：方案修订
- 做了什么：按第一轮审查与用户同意的建议修订 docs/plan/AIC_最终方案.md，经 reviewer 两轮审查。要点：关税口径按 USITC HTS 2026 Rev 20 原文更新（新 301 9903.05.31 加征 12.5% 并与原 301 叠加；IEEPA 2026-02-24 停征；第 122 条 2026-07-23 到期）；核价补 MPF、HMF、报关与 ISF；包税三档与资质判定规则；72 个任务（开发 24、测试 48）及层内决定性与非决定性构成；决策规则与追问的形式定义；图片统一由各配置视觉模型转写；三个主终点与 Holm 族、可判定的事前决策规则；排期与削减顺序。
- 结果：已推送（b718fae、89e071b）。
- 未决：FY2027 MPF 数值（91 FR 48398）与 10-09 汇率待用户提供或放开白名单；标注人 B；预算上限；硅基流动代金券余额。

## 2026-10-09 工作块④：模型适配器、执行器、统计工具
- 做了什么：新增 src/quoteguard/{util,llm,executor,stats}.py、configs/models.yaml 及测试。三个服务商（DeepSeek、百炼、硅基流动）OpenAI 兼容适配，QG_PROXY_AUTH=1 时不带鉴权头；冻结计划执行器（批次锁、排他写入、逐次尝试留痕、断点续跑、预算硬上限、额度耗尽与余额不足停批、跨批次支出账）；统计函数（McNemar 精确、配对与分层置换、通用分层置换、整簇 bootstrap、Tango 非劣效、Holm）。经 reviewer 两轮审查。
- 结果：本块测试 83 项通过；未发起任何网络或模型调用。
- 未决（协议冻结前）：主终点 3 两层共用任务，需事前定主分析（整簇 bootstrap 为主或分层 Tango 合并）；check_protocol 改为实时计算协议文件哈希；valid=false 观测按失败计入写进协议；ConnectionError 类错误标 possibly_billed。预试验第 0 步核实：模型名、思考开关参数、seed、图片格式、余额不足报错体、单图 token 数；kimi 使用服务商默认 temperature 需在报告中写明。
