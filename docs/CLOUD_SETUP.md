# 云端会话配置步骤（用 100 美元额度）

只需做一次，大约 15 分钟。完成后，编码、实验和材料生成都在云端运行，你的电脑不用开机。

## 1. GitHub 仓库

仓库是 `sandro826h-dotcom/quoteguard`（私有），由 Cowork 推送初始版本，提交邮箱为 GitHub noreply 地址。

在 github.com > Settings > Emails 勾选两项：
- Keep my email addresses private；
- Block command line pushes that expose my email。

这样云端会话即使用错邮箱提交，推送也会被拦下，学校邮箱不会进入提交记录。

## 2. 连接 GitHub

1. 打开 claude.ai/code，或桌面应用的 Code 标签页。
2. 按提示连接 GitHub，并安装 Claude GitHub App。安装时选择 Only select repositories，只勾选 `quoteguard`。

## 3. 配置云环境

在 claude.ai/code 的环境选择器里新建一个环境，名称填 `aic`。

**Network access** 选 Custom，在 Allowed domains 中逐行填入下面的域名，并勾选 Also include default list of common package managers：
```
api.deepseek.com
dashscope.aliyuncs.com
*.maas.aliyuncs.com
api.moonshot.cn
hts.usitc.gov
www.federalregister.gov
```

**API credentials**（Pro 和 Max 套餐才有这一栏）：点 Add credential，每家模型添加一条，Credential type 保持 Bearer：

| Name | Allowed websites | Custom headers：Name / Prefix / Value |
|---|---|---|
| DeepSeek | api.deepseek.com | Authorization / Bearer / 你的 DeepSeek key |
| DashScope | dashscope.aliyuncs.com，*.maas.aliyuncs.com | Authorization / Bearer / 你的 DashScope key |
| Moonshot | api.moonshot.cn | Authorization / Bearer / 你的 Moonshot key |

每条填完点 Connect，单独保存。key 保存后无法再查看，填错就删掉重加。会话内的 Claude 和它运行的命令都读不到 key，也不要把 key 发到聊天里。

如果环境对话框里没有 API credentials 这一栏，说明账号不是 Pro 或 Max。这时先不要把 key 填进环境变量（使用该环境的人都能读到），回到 Cowork 对话里商量替代办法。

**Environment variables**：
```
QG_PROXY_AUTH=1
KIMI_BASE_URL=https://api.moonshot.cn/v1
BASH_DEFAULT_TIMEOUT_MS=600000
```

**Setup script**（粘贴整段）：
```bash
#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
(apt-get update -y && apt-get install -y --no-install-recommends fonts-noto-cjk poppler-utils ffmpeg libreoffice-writer libreoffice-impress) || true &
npm install -g pptxgenjs docx || true &
pip install --break-system-packages numpy scipy PyYAML requests matplotlib openpyxl pytest || pip install numpy scipy PyYAML requests matplotlib openpyxl pytest || true &
wait
exit 0
```
脚本只在第一次开会话时运行，约 5 分钟内完成就会被缓存，之后的会话直接启动。每条命令都带 `|| true`，个别安装失败不会让会话起不来，缺什么由会话里的 Claude 补装。

## 4. 开始第一次云端会话

1. 在 Code 中选择 Cloud，仓库选 `quoteguard`，环境选 `aic`。
2. 模型：
   - 第一次会话做方案核对和计划，用 Opus。
   - 之后的常规编码可以切到 Sonnet，在会话里输入 `/model sonnet`。
   - 实验设计、结果解读、报告终审切回 Opus（`/model opus`）。
3. 粘贴 docs/CLOUD_KICKOFF.md 中的提示词。

## 5. 额度

- 云端会话先消耗 100 美元额度，用完后再计入正常套餐用量。
- 额度在 11 月 4 日过期。
- 第一次会话结束后去用量页面看一下消耗了多少，再估算后续能做多少。
- 省额度的做法：每个工作块开一个新会话，不要在一个很长的会话里反复“全面检查一遍”。新会话靠 CLAUDE.md 和 PROJECT_LOG.md 接上进度。

## 6. 和 Cowork 对话、D 盘保持同步

- **GitHub 上的 main 是唯一的工作来源。** 云端会话每完成一个工作块就推送到 main，并在 PROJECT_LOG.md 末尾写记录。
- **Cowork 对话**：已挂载本仓库，可以随时读取最新提交、日志和结果，负责方案判断和结果审查；方案调整由 Cowork 写进 docs/plan/ 并推送，云端会话下次启动时 `git pull` 读到。
- **D 盘**：D:\大学竞赛比赛\AIC\quoteguard 是快照，不是 git 仓库。需要更新时在 Cowork 对话里说“同步到 D 盘”，Cowork 从 GitHub 拉取最新版本覆盖过去。不要直接改 D 盘里的文件，要改的内容告诉 Cowork 或云端会话。
