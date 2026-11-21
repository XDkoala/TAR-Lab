# TAR-Lab 架构设计

状态：总体架构已确认（2026-11-17），需求依据见 `docs/requirements.md`（下文以 §n 引用其章节）。已实现：`integrations/ai_scientist`（引擎适配器）、`integrations/undermind`（MCP 客户端原型），以及账本、工单、内核和 Codex/Devin 执行者的最小原型（见“原型实测”）；其余模块只有目录骨架。技术细节将通过最小原型验证后再定。

## 总原则

**聪明的事交给大模型，必须可靠的事交给普通代码。**

记账、计时、扣预算、催审批、断点恢复、GPU 排队、权限判定由确定性代码完成，进程被杀也不受影响（§8）。大模型只负责判断和创造：理解用户、规划、选题、设计实验、写代码、分析、写作、审稿。

## 总体结构

```
                    用户（终端 / 邮件）
                          │  只和主 Agent 对话
                          ▼
   ┌──────────────── 主 Agent（课题组长）────────────────┐
   │   理解用户 · 规划 · 派工单 · 验收 · 叫停 · 修系统   │
   └──────┬───────────────────────────────▲──────────────┘
          │ 派工单                         │ 交结果 / 求助
          ▼                                │
   ┌──────────── 专家 Agent（按工单干活）────────────────┐
   │ 调研员  构思与设计  工程师  分析员  写作  审稿人   │
   └──────┬──────────────────────────────────────────────┘
          │ 由谁来干：执行者（可替换）
          ▼
   Codex CLI │ Devin CLI │ 模型 API │ AI-Scientist 引擎（原版/改造版）│ 以后：服务器、机器人、实验员

   ════════ 底座（普通代码，不靠大模型） ════════
   运行内核：调度、预算、超时、审批提醒、断点恢复、GPU 排队
   权限闸门：小改动 / 大改动 / 需审批，sudo 只给主 Agent，改动可回退
   项目账本：一切事件按顺序记录，可回放
   知识库：文献、代码来源与许可证、跨项目经验
```

## 部件

### 项目账本（`core`）

- 每个项目一本只追加的事件账本：用户设定、工单派发与结果、花费、审批、改动、错误等都记为事件。
- 存储：SQLite（WAL，写入原子）。代码、数据、论文等大文件放在项目目录里，账本只记路径和校验值。
- 用途：重启后重放账本恢复状态（§8）；结论可追溯（§11）；自修复的诊断依据（§10）；用户对数据的主动改动同样记入账本，并在报告中披露（§11）。

### 运行内核（`orchestration`）

- 常驻后台的普通程序，不是 Agent。
- 职责：
  - 按工单启动执行者，时间或预算耗尽时强制停止；
  - GPU 等资源排队；
  - 按 §8 的规则发送审批邮件、递增提醒、超时后采用默认选项或挂起项目；
  - 重启后从账本恢复进度；
  - 在需要判断的时刻唤醒主 Agent：工单完成或失败、进展停滞、用户消息、审批超时、限额过半。
- 日常进度巡查由内核加便宜模型完成，主 Agent 不常驻，以此平衡智能与花费（§12）。

### 主 Agent（`agents`）

- 唯一与用户对话的 Agent（§3），权限最高。
- 立项追问：先问核心问题，每题带默认值，迁就不愿多答的用户，对不合理要求提示风险或拒绝（§4）。
- 规划：把目标拆成工单，决定并行分支。
- 验收与干预：继续、返工、换方向、止损（§7）。
- 系统级事务：sudo、共性问题修复与上报、自迭代（§9、§10）。

### 工单（`core` 定义，`orchestration` 执行）

主 Agent 派活的唯一方式。每张工单必须包含：

| 项 | 说明 |
|---|---|
| 目标 | 要完成什么 |
| 输入 | 参考论文、代码仓库、上游工单的产出 |
| 应交付的产出 | 文件和格式，以及一段简短总结 |
| 限额 | 时间、美元或订阅额度、GPU 等资源 |
| 权限级别 | 小改动 / 大改动 / 需审批（§9） |
| 验收标准 | 怎样算完成 |
| 来源要求 | 关键设计必须注明参考的论文或仓库（§5） |

执行者交回结构化结果：状态（完成 / 失败 / 需要帮助）、产出、花费、证据。限额用到一半时内核唤醒主 Agent 检查进展，用完时强制停止，由主 Agent 决定追加、换方法或放弃。这是防止钻牛角尖的制度保证（§7）。

### 专家 Agent（`agents`）

角色卡定义提示词、可用工具、权限和默认难度档，不是常驻进程：每次领一张工单，做完即结束，上下文全部来自账本和知识库，因此可以随时重派。

| 角色 | 负责 | 难度档 |
|---|---|---|
| 调研员 | Undermind、论文、GitHub、OpenReview、排行榜；撞题检查 | 中 |
| 构思与设计 | 候选题目与实验设计，必须引用人类已有的做法 | 高 |
| 工程师 | 搭环境、写代码、跑训练、修 bug | 中低 |
| 分析员 | 统计、作图、显著性判断 | 中 |
| 写作 | 报告与论文 | 中 |
| 审稿人 | 按目标会议标准打分；与调研员一起判断课题价值（选题审稿 + 终审，§6） | 高 |

难度档与模型的对应关系写在配置里：第零版所有档位指向 Codex `gpt-6.1-sol` / `medium`（§13、§14）；第一版高难档（designer、reviewer）指向 Devin `claude-opus-5-5-medium`（§12；已实现，`TARLAB_TIERS` 可改），其余档位和主 Agent 仍用 Codex。额度耗尽或模型不可用时，内核按配置切换备用模型（默认 Devin 用完转 Codex）或暂停并通知用户。

### 执行者（`integrations`）

统一接口：接收工单 → 执行 → 交回结构化结果；支持查询状态、续接或重做。

- Codex CLI、Devin CLI：子进程调用（Devin 支持 `-p`、`--resume`、`--export`）。
- 模型 API：自有工具调用循环，接入 MCP。
- AI-Scientist 引擎：原版（`engines/ai-scientist-v3-original`，与上游 `df83138` 的目录树逐字节一致，只用于基线对比，有自己的 `.venv`）和改造版（`engines/ai-scientist-v3`）各包装一个：执行者 `ai-scientist-original` 和 `ai-scientist`（`tar_lab/integrations/ai_scientist/executor.py`）。工单的 `params` 传入 `task_dir`、`model`、`phases` 和 `overrides`。目前每次尝试都是一次全新的引擎运行；引擎自带的续跑（`paths.run_dir`）还没有接入。`tarlab engine compare` 用来比较多个运行的最终得分、花费和耗时。
- 以后：服务器、机器人、人工实验员（工单以邮件发给人，人回填数据）。

### 权限闸门（`tools`）

- 按 §9 分三级；sudo 只授予主 Agent。
- 回退：项目目录是 git 仓库，每张工单开始前自动快照；改动项目外文件前先备份；安装系统包时记录包名和版本。
- Undermind 工具权限沿用现有 `ToolPolicy`；`launch_deep_search` 作为受预算控制、需单独放行的动作。

### 知识库（`memory`）

- 项目知识：论文笔记、决策及其理由与来源。
- 来源登记：用到的开源代码的仓库、版本、许可证，用于自动生成引用和致谢（§5）。
- 跨项目经验：踩过的坑和有效做法，是自迭代中“积累经验”的落脚点（§10）。

### 自修复通道（`evolution`）

- 用户问题：工程师在工单内解决，解决不了上报主 Agent，再由主 Agent 与用户商量。
- 个性问题：主 Agent 只在当前项目内适配，小改动直接做，大改动先告知用户。
- 共性问题：主 Agent 在平台代码的独立副本中修复并测试，然后开 issue / PR 等待开发者审核；不在运行中的平台上直接生效，当前项目先以个性适配绕过。
- 底线：不得通过降低评分标准、关闭完整性检查、篡改证据、删除失败记录或扩大权限来“修复”问题。

## 研究流程

默认骨架：立项 → 调研 → 选题（选题审稿）→ 实验设计 → 实验 → 分析 → 写作 → 终审，并设置固定检查点（选题审稿、用户设定的审批节点）。主 Agent 可以退回之前的阶段（例如发现撞题）、开多条候选分支并行小实验后择优（§5），或跳过不需要的阶段。

## 已知局限

1. CLI 执行者内部的每条命令无法逐条拦截：只能事前用其权限模式和钩子限制，事中由内核超时和限额兜底，事后以 git 快照比对做审计。API 执行者可以逐条拦截。
2. 订阅制工具的花费无法精确换算成美元：预算分两种计量，API 按美元，订阅按时长和额度。
3. CLI 会话续接不一定可靠：兜底为以工单为单位重做，因此工单要切得足够小。
4. 内核、账本、工单、权限闸门需要自研；专家内部的循环可以借用现成的 CLI、SDK 或库。

## 技术选型方向

- 底座（内核、账本、工单、权限闸门）自研。
- LangGraph 等图编排框架不作为外层：主 Agent 动态派工单比固定流程图更灵活，而且自修复不应依赖框架内部的检查点格式。
- 专家内部的执行方式可以自由选择，它们都只是执行者的一种实现。
- 以上结论在最小原型对比中验证，必要时调整。

## 原型实测（2026-11-17）

已实现的最小底座：`core/ledger.py`（SQLite 账本）、`core/workorder.py`（工单、限额、结构化结果、执行者接口）、`orchestration/kernel.py`（快照、硬超时、半程提醒、重启恢复）、`integrations/cli_agents/`（Codex、Devin 执行者）、`agents/roles.py`（角色卡和工单提示词），以及命令行 `tarlab project init`、`tarlab order add|run|status|events`。

### 执行者对比

| 项 | Codex CLI（`gpt-6.1-sol` / medium） | Devin CLI（`claude-opus-5-5-medium`） |
|---|---|---|
| 非交互调用 | `codex exec --json`，提示词从 stdin 传入 | `devin -p --prompt-file` |
| 结构化结果 | `--output-schema` 强制返回 JSON | 没有 schema 约束，回复里可能先有一段文字，从输出中提取最后一个合格的 JSON 对象 |
| 会话 ID | 事件流的第一条（`thread.started`）就带有 | 运行结束前不输出；轮询 `devin list --format json`，约 4 秒后出现 |
| 续接 | `codex exec resume <id>`，可用 | `devin -p -r <id>`，可用 |
| 用量 | `turn.completed` 报告的是会话累计值 | 导出文件 `final_metrics` 里是会话累计值 |
| 被杀尝试的用量 | 可以从 `~/.codex/sessions/**/rollout-*-<id>.jsonl` 里的 `token_count` 补回 | 导出文件只在一轮结束后写入，**补不回来** |
| 最小任务（hello.py） | 18 秒，输入 5.8 万 token（其中 4.3 万命中缓存） | 11 秒，输入 3.9 万 token（其中 3.8 万命中缓存） |
| 杀内核后恢复（素数筛工单） | 成功，2 次尝试，累计 17.6 万 token | 成功，2 次尝试，累计 12.8 万 token |

两次恢复测试都是在执行者运行中途用 `kill -9` 杀掉内核：执行者随之退出（`setpriv --pdeathsig`），没有残留进程。重启后先把未完成的工单标为中断，再按会话 ID 续接，最后通过验收。

### 主 Agent 唤醒成本

用 Codex 读一份账本摘要（约 1.5 KB）并做一次验收决策：约 1.5 万 token，耗时 8 秒。同一张工单本身用了 17.6 万 token，所以“事件驱动、只在关键时刻唤醒”的成本可以接受。

### 实测发现

- **Codex 沙箱**：依赖 bwrap 和非特权用户命名空间，而 Ubuntu 24.04 默认禁止（`kernel.apparmor_restrict_unprivileged_userns=1`），连 `workspace-write` 也无法写文件。执行者默认改为 `--dangerously-bypass-approvals-and-sandbox`，由快照、硬超时和事后审计兜底。要恢复沙箱，需要用户用 sudo 调整 AppArmor 配置。
- **不加载用户配置**：用 `--ignore-user-config` 跳过用户的插件和 MCP，每次调用的固定上下文从约 11 万 token 降到约 5.8 万。
- **用量按会话累计**：两个 CLI 续接后报告的都是累计值。账本按会话记录最新累计值，订单总用量是各会话之和，不能把每次尝试的数字相加。
- **Devin 宿主环境变量**：在 Devin Desktop 或 Windsurf 里启动的进程会继承 `ACP_BACKEND` 等变量，CLI 因此切换到宿主凭据模式，报告未登录。执行者启动前要清除这些变量（见 `HOST_ENV_VARS`）。
- **Devin 修改了 `~/.bashrc`**：首次以已登录状态运行时，自动追加了一段把 `~/.local/bin` 加入 PATH 的配置块（带 `MANAGED DEVIN BLOCK` 标记）。
- **耗时统计**：恢复时，被杀尝试的耗时按其最后一条账本事件或最后一次写文件的时间计算，防止少计限额。
- **路径**：执行者在工作区内运行，传给它的所有路径都必须是绝对路径。

### 第零版项目 A：基线对比（2026-11-17）

输入相同：原版引擎目录中的 `examples/circpack`，`gpt-6.1-sol` / medium，只跑 `investigation` 阶段，最多 2 个构思轮次，使用 Docker 容器和 Codex 订阅，两边同时运行。

| | 原版引擎（直接运行 `agsci`） | TAR-Lab（内核 → `ai-scientist` 执行者） |
|---|---|---|
| 状态 | completed，2 个构思 | done（completed），2 个构思 |
| 最终得分（半径之和，n=26） | 2.63109 | 2.63304 |
| 结果正确 | 是 | 是 |
| 估算花费（按 API 牌价估算；订阅制下实际不另付费） | 0.65 美元 | 0.57 美元 |
| 耗时 | 14.9 分钟 | 11.5 分钟 |

结论：流程已跑通，TAR-Lab 能通过工单驱动引擎，并记录得分、花费和耗时。两边各只跑了一次，而且当前改造版引擎与原版相比只多了一处 TeX 修复（不影响 `investigation` 阶段），所以得分和耗时的差异属于随机波动，不能说明哪边更强。以后做量化评测，需要每边重复多次，比较均值和方差。

### 已知不足

- 同一个工作区里有多张 Devin 工单并行时，靠 `devin list` 识别新会话可能认错，需要按工单隔离工作目录。
- 被杀的 Devin 尝试用量未知，只能按耗时计入限额。
- 订阅制执行者只能按 token 和耗时计量，无法折算成美元。

## 引擎边界：AI-Scientist-v3

TAR-Lab 只用 AI-Scientist-v3 的“想法搜索”循环（investigation）：给定一个有自动打分的任务，编码 Agent 反复提出想法、改程序、调用任务的打分脚本，保留得分最高的程序。选题、调研、消融、最终评估、写论文和评审都由 TAR-Lab 自己的角色完成，所以 `engines/ai-scientist-v3/` 已精简为只含这一循环，并在本机原生运行（不用 Docker）。删除内容见 `patches/ai-scientist-v3/README.md`。

平台与引擎的契约：

- **输入**：任务目录，包含 `task.md`、`evaluate.py`、`capture.py`、`program_0/`（格式见 `engines/ai-scientist-v3/examples/circpack`），可选的任务环境 `.venv`（候选程序用它评估）。
- **调用**：`<引擎>/.venv/bin/python -m src.investigation.cli`，作为独立进程运行，传入 `paths.task_dir`、`paths.results_dir`、`run_id`、Agent 和想法轮数。Agent 通过本地的 headless CLI（`HEADLESS_BIN`）启动，使用订阅登录。
- **输出**：运行目录（`<results_root>/<task>/<run_id>/`）中的 `events.jsonl`（`finished`/`campaign_failed`、`cost_summary`）、`best_program_manifest.json`、`attempts_history.jsonl`、`conclusions.md` 和 `ideas/`。
- **约束**：平台不导入 `src.*`。引擎有独立的环境和测试。

适配器 `tar_lab.integrations.ai_scientist`：

- `task_problems(task_dir)`：检查任务目录是否完整，平台据此放行或拒绝引擎工单。
- `AIScientistEngine` + `RunRequest`：拼出命令和环境（清除宿主机 `PYTHONPATH`，把 `codex` 链接进 `PATH`，设置 `HEADLESS_BIN` 和订阅计费）。`pipeline=True` 用于未改动的上游原版，运行它自己的 `agsci run`，只跑 investigation 阶段，关闭容器。
- `AIScientistExecutor`：内核执行者，结果里写明是否超过基线、最佳分数、最佳程序位置和引擎的最新结论。
- `load_run(run_dir)` / `tarlab engine summary <run_dir>`：汇总状态、最佳程序、结局、费用和耗时；兼容原版的嵌套布局（`investigation/`）。

## Undermind 集成

- **接入方式**：远程 MCP `https://mcp.undermind.ai/mcp`，Streamable HTTP，OAuth 授权码 + PKCE。
- **授权限制**：没有 API 密钥或服务账户，必须以某个用户的身份授权。无人值守运行时，平台需要安全保存并刷新令牌。客户端 ID 需要 Undermind 预先注册，或者使用自己托管的 CIMD。
- **工具权限**：部分工具会删除数据或无法撤销，自动运行时默认只开放读取和创建类工具。
- **平台层用途**：选题、调研、新颖性检查、寻找基线方法和数据集，结果写入 `memory`。

实现（`tar_lab.integrations.undermind`）：

- **客户端标识**：默认使用 CIMD，客户端 ID 是 `https://xdkoala.github.io/TAR-Lab/oauth/client-metadata.json`。该文档的源文件是 `deploy/oauth/client-metadata.json`，发布在 `gh-pages` 分支。改用 Undermind 预分配的客户端 ID 时，设置 `TARLAB_UNDERMIND_CLIENT_ID`。
- **回调地址**：`http://127.0.0.1:47615/callback`，必须和 CIMD 文档中登记的一致。
- **凭据存储**：`~/.config/tar-lab/undermind/credentials.json`，权限 0600，原子写入。Undermind 每次刷新都会更换刷新令牌，新令牌必须立即持久化。
- **断点恢复**：SDK 重启后既不知道令牌何时过期，也不知道令牌端点。`UndermindOAuthProvider` 会从存储中恢复过期时间，并重新发现服务器的 OAuth 元数据，这样后台任务重启后能直接刷新令牌，而不是要求重新在浏览器登录。
- **非交互模式**：Agent 使用的默认模式，从不打开浏览器。凭据缺失或已被撤销时，抛出 `LoginRequiredError`，由编排层转为人工任务。
- **工具权限**：`ToolPolicy` 默认拒绝。只读模式只放行标注了 `readOnlyHint` 的工具；读写模式额外放行明确标注 `destructiveHint: false` 的工具；删除类和没有标注的工具一律拒绝，除非显式加入放行列表。
- **审计**：每次调用（包括被拒绝的调用）都追加到 `~/.config/tar-lab/undermind/audit.jsonl`。

### 实测结果（2026-11-17）

已完成真实登录（CIMD 客户端 ID 被 Undermind 接受）。服务器提供 32 个工具，工具标注齐全，默认只读策略的判定如下：

- **放行（20 个，均标注只读）**：`get_orientation`、`list_workspaces`、`search_papers`、`find_papers_by_author`、`lookup_papers_by_metadata`、`get_paper_info`、`read_pdfs`、`inspect_*`、`get_deep_search_status`、`display_*` 等。
- **拒绝，标注为破坏性**：`launch_deep_search`、`write_file`、`delete_file`、`delete_folder`、`delete_deep_search`。
- **拒绝，名称以 `remove_` 开头**：`remove_papers_from_folder`。虽然标注为非破坏性，仍按名称规则拒绝。
- **拒绝，非只读，加 `--allow-write` 后可放行**：`create_workspace`、`create_folder`、`move_*`、`add_papers_to_folder`、`star_papers`、`unstar_papers`。

对平台设计的影响：

- **深度检索需单独放行**：Undermind 的核心能力“深度检索”（`launch_deep_search`，异步运行数分钟，可同时运行的数量有限）被标注为破坏性，在读写模式下也会被拒绝。编排层需要把它作为受预算控制的动作，显式加入放行列表；结果通过 `get_deep_search_status` / `inspect_deep_searches` 轮询获取。
- **需要专用工作区**：`search_papers`、`launch_deep_search` 等工具都需要 `workspace_id`。平台应使用专用的 TAR-Lab 工作区，或者每个研究项目各用一个工作区，不要写进用户已有的工作区。
- **调用顺序**：按 Undermind 的说明，每个会话应先调用一次 `get_orientation`。

## 待定

- 主 Agent 的具体模型组合（强模型做决策、便宜模型做巡查的搭配）。单次唤醒的成本已经实测，模型搭配还要在真实项目上比较。
- Devin 订阅条款是否允许大规模自动调用，以及额度够不够用（需要用户确认）。
- 项目目录和账本的具体结构、工单与事件的数据格式。
- 原版引擎副本的具体上游提交。
- 止损和候选筛选规则的默认阈值。
