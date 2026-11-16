# TAR-Lab 架构设计

状态：草案（2026-11-17）。已实现：`integrations/ai_scientist`（引擎适配器）、`integrations/undermind`（MCP 客户端原型）。其余模块只有目录骨架。

## 目标

全自动科研平台：多 Agent 协作完成“选题 → 文献调研 → 假设 → 实验 → 分析 → 写作 → 审稿”的完整闭环。系统能自适应、自修复、自迭代，在关键节点（选题、大额预算、对外发布）请求人工审批。

## 分层

```
            人工审批 / 看板
                  │
┌─────────────────┴──────────────────┐
│ orchestration  工作流状态机、预算、检查点   │
├────────────────────────────────────┤
│ agents         规划 / 文献 / 构思 / 实验 / 审稿 / 修复 │
├──────────────┬─────────────────────┤
│ tools        │ memory              │
│ MCP、工具白名单 │ 文献库、经验、失败案例    │
├──────────────┴─────────────────────┤
│ integrations                       │
│  undermind/      文献检索与深度调研         │
│  ai_scientist/   实验执行引擎适配器         │
├────────────────────────────────────┤
│ core  领域模型与事件存储（所有层共享）        │
└────────────────────────────────────┘
   evolution  观察各层事件和失败，提出修复并验证
```

| 模块 | 职责 |
| --- | --- |
| `core` | 领域对象（ResearchProject、Question、Hypothesis、Experiment、Evidence、Artifact）、状态和事件记录；所有决策和产物都可以追溯 |
| `agents` | 角色定义、提示词、输入输出模式；Agent 之间不直接互调，统一经由编排层 |
| `orchestration` | 工作流状态机、并发、预算、重试、人工审批节点、断点续跑 |
| `tools` | MCP 客户端、工具注册、按角色划分的权限白名单、调用审计 |
| `integrations/undermind` | Undermind MCP 接入 |
| `integrations/ai_scientist` | 调用 AI-Scientist-v3 引擎 |
| `evolution` | 故障分类 → 修复方案 → 沙箱验证 → 记录或回退 |
| `memory` | 跨项目积累：文献、方法经验、失败案例 |

## 引擎边界：AI-Scientist-v3

AI-Scientist-v3 负责“给定任务 → 实验证据和论文”这一段：先构思，再迭代改进程序，接着做消融和事后分析，最后写论文。它擅长有明确评分函数的计算实验。它不负责选题、调研和跨项目规划。

平台与引擎的契约：

- **输入**：任务目录，包含 `task.md`、`evaluate.py`、`program_0/` 等（格式见 `engines/ai-scientist-v3/examples/circpack`），以及 Hydra 配置覆盖项。
- **调用**：使用引擎环境中的 `agsci run`，作为独立进程运行（`deploy/local/agsci-local.sh` 是现成示例）。
- **输出**：运行目录（`<runs>/<task>/<agent>/<run_id>/`）中的 `best_program_manifest.json`、消融与事后分析结果、论文目录和日志。
- **约束**：平台不导入 `src.*`。引擎有独立的环境和测试，可以深度修改，甚至整个替换，而不影响平台。

适配器 `tar_lab.integrations.ai_scientist` 的接口：

- `TaskSpec` + `write_task_dir()`：生成任务目录。没有 `program_0/` 的任务必须包含 `task_drafting` 阶段。
- `AIScientistEngine.run(RunRequest)`：以子进程运行 `agsci run`。会显式传入 `run_id` 和 `paths.results_root`，所以运行目录可以预先确定；同时清除宿主机的 `PYTHONPATH`；控制台输出写入日志文件。
- `load_run(run_dir)` / `tarlab engine summary <run_dir>`：从 `pipeline_events.jsonl`、`investigation/best_program_manifest.json`、`.investigation_completion.json`、`paper/paper.pdf` 中汇总各阶段状态、最佳程序、结局和费用。续跑时，以每个阶段最后一次的结果为准。

## Undermind 集成

- **接入方式**：远程 MCP `https://mcp.undermind.ai/mcp`，Streamable HTTP，OAuth 授权码 + PKCE。
- **授权限制**：没有 API 密钥或服务账户，必须以某个用户的身份授权。无人值守运行时，平台需要安全保存并刷新令牌。客户端 ID 需要 Undermind 预先注册，或者使用自己托管的 CIMD。
- **工具权限**：部分工具会删除数据或无法撤销，自动运行时默认只开放读取和创建类工具。
- **平台层用途**：选题、调研、新颖性检查、寻找基线方法和数据集，结果写入 `memory`。
- **引擎层用途（可选）**：为容器内的 Codex/Claude CLI 配置 MCP，在构思和写论文阶段使用 Undermind。代价是要把登录凭据挂载进容器。

实现（`tar_lab.integrations.undermind`）：

- **客户端标识**：默认使用 CIMD，客户端 ID 是 `https://xdkoala.github.io/TAR-Lab/oauth/client-metadata.json`。该文档的源文件是 `deploy/oauth/client-metadata.json`，发布在 `gh-pages` 分支。改用 Undermind 预分配的客户端 ID 时，设置 `TARLAB_UNDERMIND_CLIENT_ID`。
- **回调地址**：`http://127.0.0.1:47615/callback`，必须和 CIMD 文档中登记的一致。
- **凭据存储**：`~/.config/tar-lab/undermind/credentials.json`，权限 0600，原子写入。Undermind 每次刷新都会更换刷新令牌，新令牌必须立即持久化。
- **断点恢复**：SDK 重启后既不知道令牌何时过期，也不知道令牌端点。`UndermindOAuthProvider` 会从存储中恢复过期时间，并重新发现服务器的 OAuth 元数据，这样后台任务重启后能直接刷新令牌，而不是要求重新在浏览器登录。
- **非交互模式**：Agent 使用的默认模式，从不打开浏览器。凭据缺失或已被撤销时，抛出 `LoginRequiredError`，由编排层转为人工任务。
- **工具权限**：`ToolPolicy` 默认拒绝。只读模式只放行标注了 `readOnlyHint` 的工具；读写模式额外放行明确标注 `destructiveHint: false` 的工具；删除类和没有标注的工具一律拒绝，除非显式加入放行列表。
- **审计**：每次调用（包括被拒绝的调用）都追加到 `~/.config/tar-lab/undermind/audit.jsonl`。

## 模型调用

两种方式混用：
- 平台的规划、调研、审稿类调用走 API（按量计费，可以并发，费用清楚可见）。
- 编码量大的实验执行交给订阅版 CLI（通过引擎调用）。

预算由编排层统一核算。

## 自修复与自迭代路线

1. **单次运行层面**：引擎或 Agent 失败后，分类故障 → 修改任务、程序或配置 → 在沙箱中重跑验证 → 写入 `docs/system_defects_and_remediation_log.md` 这类结构化记录。
2. **流程层面**：把多次出现的故障沉淀为预检和回归测试。
3. **系统代码层面**：修改平台或引擎自身代码，以分支加测试的形式提交；测试不通过不合并，主分支的合并需要人工审批。

底线：不得通过降低评分标准、关闭完整性检查、篡改证据或扩大权限来“修复”问题。

## 待定

- 多 Agent 的实现方式（自研编排、LangGraph、厂商 Agents SDK，还是驱动编码 Agent CLI）。
- 平台状态存储（文件、SQLite 还是数据库）和人工审批的交互界面。
