# TAR-Lab：可信自主科研实验室

TAR-Lab（Trusted Autonomous Research Lab）的目标是做成一个全自动科研平台：由多个 Agent 协作完成选题、文献调研、实验、分析和写作，并且能自我修复、自我迭代。关键节点保留人工审批。

[SakanaAI/AI-Scientist-v3](https://github.com/SakanaAI/AI-Scientist-v3)（Apache-2.0）作为平台的实验执行引擎，放在 `engines/ai-scientist-v3/`。架构设计见 [docs/architecture.md](docs/architecture.md)。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `tar_lab/` | 平台本体：领域模型、多 Agent、编排、工具/MCP、外部集成、自修复、记忆 |
| `tests/` | 平台测试 |
| `engines/ai-scientist-v3/` | AI-Scientist-v3 引擎（独立的 Python 环境、测试和 CI） |
| `deploy/local/` | 本地启动脚本、Docker 扩展镜像、环境配置模板，见 [deploy/local/README.zh.md](deploy/local/README.zh.md) |
| `docs/` | 架构设计、缺陷修复记录、维护记录 |
| `patches/ai-scientist-v3/` | 对引擎上游代码的修改登记，便于给上游提 PR |

## 本地工作区布局

```
Trusted-Autonomous-Research-Lab/
├── TAR-Lab/        本仓库
├── runs/           实验运行结果（不进 Git）
├── deliverables/   交付快照（论文、报告、图表）
└── .tools/         本地工具：node/、gh/
```

## 开发

```bash
uv sync                                               # 平台环境（仓库根目录 .venv）
uv run ruff check . && uv run mypy . && uv run pytest -q

cd engines/ai-scientist-v3 && uv sync --frozen        # 引擎环境（独立 .venv）
uv run ruff check . && uv run mypy . && uv run pytest -q -n auto
```

平台和引擎使用各自独立的环境。平台通过命令行和运行目录里的文件调用引擎，不导入引擎的 Python 代码。

凭据不入库：`.env`、`engines/ai-scientist-v3/.env`、`deploy/local/env/*.env` 都已被 Git 忽略，只提交 `*.env.example` 模板。

## 交互界面

```bash
tarlab                      # 选择或新建项目，进入对话界面
tarlab projects/<项目>      # 直接打开某个项目
```

界面风格参照 Claude Code：消息、工单结果和审批写进终端自己的滚动记录；底部常驻子 Agent 面板、输入行和状态栏（阶段、按 API 标价估算的花费、时间、深度检索次数、GPU）。界面只读账本、写入用户事件，不负责运行项目；关掉界面，后台的 `tarlab run` 照常运行。输入 `/help` 查看全部命令。

| 按键 | 作用 |
|---|---|
| Enter | 发给主 Agent（当前工单完成后读到） |
| Esc，或消息末尾加 `!` | 紧急发送，立刻叫醒主 Agent |
| Ctrl+A | 回答待审批的问题（方向键选择） |
| Ctrl+O | 展开/收起子 Agent 的最近步骤 |
| Ctrl+J，或 `\` + Enter | 换行 |
| `/` 和 `@` | 补全命令、工单号、报告和工作区文件 |

## 平台命令

```bash
uv run tarlab undermind login          # 浏览器授权 Undermind（一次性；之后自动刷新令牌）
uv run tarlab undermind status         # 查看凭据状态（不联网）
uv run tarlab undermind tools          # 列出工具及权限判定（默认只读）
uv run tarlab undermind call <tool> --args '{"key": "value"}'
uv run tarlab engine summary ../runs/circpack/codex/20261116_143042   # 汇总一次引擎运行
```

## 同步引擎上游

`upstream-main` 分支跟踪 SakanaAI 上游 `upstream/main`，代码位于仓库根目录；本仓库中对应代码位于 `engines/ai-scientist-v3/`。

```bash
git fetch upstream && git branch -f upstream-main upstream/main
git log --oneline upstream-main ^main                  # 查看上游新提交
git cherry-pick -X subtree=engines/ai-scientist-v3 <commit>    # 挑选单个提交
git merge -X subtree=engines/ai-scientist-v3 upstream-main     # 或整体合并
```

修改引擎代码时，单独提交，并登记到 `patches/ai-scientist-v3/README.md` 和 `docs/system_defects_and_remediation_log.md`。
