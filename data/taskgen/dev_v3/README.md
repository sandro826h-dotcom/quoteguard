# dev 集生成配置（v3 存档）

data/tasks/dev 与 data/gold/dev 由本目录四个 YAML 生成，哈希与 data/tasks/dev/manifest.json 的 yaml_sha256 一致。
2026-10-10 复审真实报价单元后，data/taskgen/ 的参照下限与空派档价更新为 v4（见 freight_floor.yaml 头注），测试集按 v4 生成。
dev 集与预试验记录保持原样；对 dev 集评分、联调时传 `--data-dir data/taskgen/dev_v3`。
