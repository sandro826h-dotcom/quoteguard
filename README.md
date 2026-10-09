# QuoteGuard

运价明镜：面向中国跨境卖家的美国头程物流报价智能审计。输入微信文字、Excel 或价格表图片形式的货代报价，系统抽取条款并标注原文出处，折算可比的到仓成本，识别隐性费用、条款冲突、包税合理性和资质证据状态；信息不足时不给推荐，改为生成追问清单；输出可核对的决策护照。

## 目录
- `src/quoteguard/`：系统代码
- `tests/`：测试
- `data/seeds/`：真实种子记录（注明来源与日期）
- `runs/`：原始运行记录（不手工改动）
- `docs/plan/`：方案与比赛要求核对
- `docs/protocol/`：冻结的实验协议
- `reference/echoguard/`：迁移参考代码（只读）
- `output/`：报告、图表、演示等生成物

## 运行
环境：Python 3.10 及以上。

```bash
pip install -e ".[dev]"
pytest -q
```

调用模型前，先配置环境变量 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`MOONSHOT_API_KEY`。在云端由代理注入 key 时，改为设置 `QG_PROXY_AUTH=1`。
