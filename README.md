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

界面语言：`tarlab --lang en` 或环境变量 `TARLAB_LANG=en`；不指定时跟随项目的对话语言。界面里也可以用 `/lang en`、`/lang zh` 切换。

## 后台常驻与非交互调用

```bash
tarlab service install projects/<项目>   # 装成 systemd 用户服务：崩溃、被杀后 30 秒自动重启，登录后自动运行
tarlab service status|stop|start|uninstall projects/<项目>
tarlab -p "进展如何？" projects/<项目>          # 发一条消息，等主 Agent 回答后打印（给脚本用）
tarlab -p "进展如何？" projects/<项目> --json   # 回答和项目状态一起输出 JSON
tarlab status projects/<项目> --json
```

- 每个项目同时只能有一个运行器，第二个会直接退出（退出码 4）。
- 项目完成退出码为 0，暂停为 3；这两种情况服务都不会重启。
- 想在注销后也保持运行，执行一次 `loginctl enable-linger $USER`。
- `-p` 的退出码：拿到回答为 0；没有运行器为 2，此时消息留在队列里；超时（默认 900 秒，用 `--wait` 修改）为 124。

## 项目运行规则

- **预算**：花费按 API 标价估算（订阅不另外扣钱）。花到 80% 发邮件提醒；花到 100% 新工单暂停，正在跑的工单跑完，同时问你要不要追加。回答里写一个更高的金额（如“追加到 $45”）就直接生效。
- **并行**：最多同时跑 3 张工单；工程师工单可能占用 GPU，一次只跑一张。
- **快照**：每张工单开始前给工作区做 git 快照。克隆进工作区的仓库，记录来源、提交号和本地改动的补丁（`workspace/.tar-lab/nested/`）。
- **AI-Scientist-v3 引擎**：子问题有自动打分、值得反复搜索改进时（方法变体、结构或超参数搜索、在基准上超过基线），主 Agent 可以派“引擎工单”。先由工程师在工作区里建好引擎任务目录（`task.md`、`evaluate.py`、`program_0/`），平台检查完整后才放行。引擎在 Docker 里跑、只用 CPU（本机 Docker 没有 GPU 运行时），结果放在 `workspace/engine_runs/`，大约每 10 分钟跑一个想法。镜像可用 `TARLAB_ENGINE_IMAGE` 指定。
- **封存的最终评估**：实验方案一开始就定好留出数据和评估协议，开发、调参、选模型时都不碰。候选方法定下来后，工程师写好 `eval/final/protocol.md`、评估脚本和 `eval/final/frozen.txt`（要评估的代码、配置和权重），主 Agent 再派最终评估工单。平台在派单时给这些文件算指纹，工单结束后再核对一遍，记录写进 `results/final/<工单号>/SEAL.json`，有改动就通知你。论文里的主要结果只能来自这里。再跑一次最终评估要经过你同意，并在论文里披露。
- **额度用完**：某个执行者报额度不足或限流时，它的工单暂停 30 分钟后自动重试，主 Agent 也一样。设置 `TARLAB_FALLBACK=codex=devin` 后，工单会改由备用执行者接着做。默认不开启，因为 Devin 超出套餐额度后按 API 价格另外收费，而且平台目前估算不了 Devin 的花费。

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
