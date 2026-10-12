# 演示系统

本地离线网页，回放预试验已记录的模型输出，不发模型调用，不联网。三个案例均为 dev 集合成任务。

## 运行

```bash
PYTHONPATH=src python3 demo/build_data.py      # 生成 demo/data/cases.json 与 demo/data/assets/
python3 -m http.server 8000 -d demo            # 打开 http://localhost:8000
```

`build_data.py` 从 runs/ 读取观测（只读），用仓库代码离线重算 M0 与 M4 输出和各步骤数据，
并与 runs/derived/pilot_rescore_20261010_fullreview 逐项核对（决策、选择、追问清单、各报价成本），不一致时报错退出。

## 页面

- 顶栏：案例 A/B/C、模型配置、上传报价（按 SHA-256 匹配内置样例，其余文件只提示）。
- 六步：报价输入、字段抽取与原文出处、冲突与缺口、包税风险与资质证据、到仓成本对比、决策护照与追问清单。
- URL 可直达，例如 `#case=A&cfg=ds_flash&step=3`；第 2 步可加 `&q=C1` 选定报价。
- 第 6 步“打印护照”输出一页 A4。

## 截图

`demo/screenshots/` 为案例 A（ds_flash）六个步骤和案例 C（glm）的决策护照，1440×900。
