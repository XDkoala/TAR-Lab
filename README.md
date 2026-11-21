# TAR-Lab：可信自主科研实验室

TAR-Lab（Trusted Autonomous Research Lab）的目标是做成一个全自动科研平台：由多个 Agent 协作完成选题、文献调研、实验、分析和写作，并且能自我修复、自我迭代。关键节点保留人工审批。

[SakanaAI/AI-Scientist-v3](https://github.com/SakanaAI/AI-Scientist-v3)（Apache-2.0）的“想法搜索”循环作为平台的一个实验引擎，放在 `engines/ai-scientist-v3/`：只保留这一部分，在本机原生运行（不用 Docker），其余阶段已删除。未改动的上游原版放在 `engines/ai-scientist-v3-original/`，只用于基线对比。架构设计见 [docs/architecture.md](docs/architecture.md)。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `tar_lab/` | 平台本体：领域模型、多 Agent、编排、工具/MCP、外部集成、自修复、记忆 |
| `tests/` | 平台测试 |
| `engines/ai-scientist-v3/` | 精简后的 AI-Scientist-v3 想法搜索引擎（独立的 Python 环境和测试） |
| `engines/ai-scientist-v3-original/` | 上游原版，保持与上游逐字节一致，只用于基线对比 |
| `deploy/` | 邮件通知配置模板、Undermind OAuth 元数据 |
| `docs/` | 架构设计、缺陷修复记录、维护记录 |
| `patches/ai-scientist-v3/` | 引擎精简的说明（已不再与上游同步） |

## 本地工作区布局

```
Trusted-Autonomous-Research-Lab/
├── TAR-Lab/        本仓库
├── runs/           实验运行结果（不进 Git）
├── deliverables/   交付快照（论文、报告、图表）
└── .tools/         本地工具：node/、gh/、headless/（引擎启动 Agent 用）、texlive/（编译论文）、tectonic/（备用）
```

## 开发

```bash
uv sync                                               # 平台环境（仓库根目录 .venv）
uv run ruff check . && uv run mypy . && uv run pytest -q

cd engines/ai-scientist-v3 && uv sync --frozen        # 引擎环境（独立 .venv）
uv run ruff check . && uv run mypy src tests && uv run pytest -q -n auto
```

本地工具（都在 `../.tools/`，不需要 sudo）：
- `headless/`：引擎用它启动 Codex 等 Agent。安装：`git clone https://github.com/RobertTLange/headless-cli.git headless && git -C headless checkout 93cd9b06b85f848af1308c41e018991b33907c5e && cd headless && npm ci --ignore-scripts && npm run build && npm prune --omit=dev`（用 `.tools/node` 里的 Node）。
- `texlive/2026/`：编译论文 PDF 的首选，TeX Live 2026（scheme-full，不含文档和源码，安装后 4.9 GB；pdfTeX 1.40.29、latexmk 4.89）。arXiv 和各会议的投稿系统都用 TeX Live 的 `pdflatex`，本地用同一套才能得到一样的字体和排版（arXiv 目前默认 TeX Live 2025，也支持 2023）。`tarlab tex build` 在论文目录里运行 `latexmk -pdf -interaction=nonstopmode -halt-on-error -shell-restricted -file-line-error main.tex`（受限 shell-escape：只允许 TeX Live 白名单里的工具，如把 EPS 图转成 PDF 的 `repstopdf`），BibTeX/Biber 的重跑由 latexmk 负责。用 `TARLAB_TEXLIVE_BIN` 可以指定别的 `bin/<arch>` 目录；没有本地安装时用 `PATH` 上的 `latexmk`/`pdflatex`。安装（不需要 sudo，清华 CTAN 镜像，tlmgr 会校验每个包的校验和，约 30 分钟）：

  ```bash
  T=/path/to/Trusted-Autonomous-Research-Lab/.tools/texlive   # 改成实际的绝对路径
  cd /tmp && curl -fsSLO https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/texlive/tlnet/install-tl-unx.tar.gz
  tar xzf install-tl-unx.tar.gz && cd install-tl-2*/
  cat > texlive.profile <<EOF
  selected_scheme scheme-full
  TEXDIR $T/2026
  TEXMFLOCAL $T/2026/texmf-local
  TEXMFSYSVAR $T/2026/texmf-var
  TEXMFSYSCONFIG $T/2026/texmf-config
  TEXMFVAR $T/2026/texmf-var
  TEXMFCONFIG $T/2026/texmf-config
  TEXMFHOME $T/texmf-home
  binary_x86_64-linux 1
  instopt_adjustpath 0
  instopt_letter 0
  tlpdbopt_autobackup 0
  tlpdbopt_install_docfiles 0
  tlpdbopt_install_srcfiles 0
  EOF
  ./install-tl --no-interaction -profile texlive.profile \
    -repository https://mirrors.tuna.tsinghua.edu.cn/CTAN/systems/texlive/tlnet
  ```

  不建立 `/usr/local/bin` 符号链接，也不改 `PATH`；TAR-Lab 直接调用 `.tools/texlive/<年份>/bin/x86_64-linux`（有多个年份时用最新的）。
- 投稿前检查：`tarlab tex check paper [--page-limit 8] [--anonymous] [--json]` 把论文目录（去掉 `.aux`、`.log`、`.bbl`、`main.pdf` 等编译产物）复制到临时目录重新编译，确认源码自包含，再检查：日志里的 LaTeX 错误、未定义的引用和交叉引用、缺失文件、超过 1pt 的 overfull \hbox、字体替换；`pdffonts` 是否全部嵌入、有没有 Type 3 字体；`pdfinfo` 页数（含参考文献）；`\cite` 的键是否都在 `.bib` 里、条目是否缺标题或年份；`--anonymous` 时查作者、邮箱、`github.com/<用户>` 链接和致谢。每项 PASS/WARN/FAIL/SKIP，有 FAIL 时退出码为 1；没装 poppler（`pdffonts` 等）时对应项为 SKIP。
  在论文目录的上一级（工作区）有 `figures/spec.toml` 时，还会检查生成的图表：是否都已生成；脚本和输入数据自上次生成后有没有改动；`paper/generated/` 里的文件有没有被手改；在临时目录重新生成一遍能否逐字节复现；主要结果的图表是否只读了 `results/final/`（以上任一不满足为 FAIL）。另外检查图是否按论文实际栏宽绘制且没有被缩放、手写表格里有没有直接敲进去的小数、位图分辨率是否低于 300 ppi（这几项为 WARN）。
- `texlive-arxiv/2025/`：arXiv 用的 TeX Live 2025，取自 texlive.info 的每日存档，冻结在 arXiv 采用的 2025-08-03 状态（scheme-full，不含文档和源码，4.5 GB；pdfTeX 1.40.28、latexmk 4.87、BibTeX 0.99d、biber 2.21），只给 `tex check --arxiv` 和 `tex arxiv` 用。不要对它运行 `tlmgr update`。安装（不需要 sudo，约 1 小时）：

  ```bash
  T=/path/to/Trusted-Autonomous-Research-Lab/.tools/texlive-arxiv   # 改成实际的绝对路径
  R=https://texlive.info/tlnet-archive/2025/08/03/tlnet
  mkdir -p $T && cd /tmp && curl -fSLO $R/install-tl-unx.tar.gz
  tar xzf install-tl-unx.tar.gz && cd install-tl-20250803
  cat > texlive.profile <<EOF
  selected_scheme scheme-full
  TEXDIR $T/2025
  TEXMFLOCAL $T/2025/texmf-local
  TEXMFSYSVAR $T/2025/texmf-var
  TEXMFSYSCONFIG $T/2025/texmf-config
  TEXMFVAR $T/2025/texmf-var
  TEXMFCONFIG $T/2025/texmf-config
  TEXMFHOME $T/texmf-home
  binary_x86_64-linux 1
  instopt_adjustpath 0
  instopt_letter 0
  tlpdbopt_autobackup 0
  tlpdbopt_install_docfiles 0
  tlpdbopt_install_srcfiles 0
  EOF
  ./install-tl --no-interaction -profile texlive.profile -repository $R
  ```
- arXiv 检查：`tarlab tex check paper --arxiv` 用 `texlive-arxiv/2025`（arXiv 现在默认的 TeX Live 2025，冻结在 arXiv 采用的 2025-08-03 状态）先编译一遍，再按 latexmk 的记录文件（`main.fls`）只挑出编译时实际读到的文件，加上 `.bbl` 和 `.bib`，单独再编译一次，确认上传包自包含。另外按 arXiv 的 TeX Live 2025 说明检查：`\input` 等用了绝对路径或 `..`、hyperxmp 先于 hyperref 加载、minted 缺 `_minted` 目录、上传包超过 50 MiB（这些为 FAIL）；cleveref 配 `\newtheorem`、把 `\doublespace` 当命令用、隐藏文件、文件名含特殊字符（这些为 WARN）；论文目录里没用到的文件会列出来，不放进包。`tarlab tex arxiv paper [-o reports/arxiv.tar.gz]` 检查通过后写出可直接上传的 `.tar.gz`（文件顺序、时间戳固定）。没装 TeX Live 2025 时用本地 2026 编译并给出 WARN。用 `TARLAB_ARXIV_TEXLIVE_BIN` 可以指定别的目录。
- 页面渲染：`tarlab tex render paper [--dpi 110]` 把已编译的 PDF 每页渲染成 PNG，放到 `reports/paper_pages/`，供写作者和审稿人看图检查。
- `figures/venv`：画论文图表专用的 Python 3.11 环境（matplotlib、numpy、pandas、scipy，版本和哈希锁在 `tar_lab/figures/requirements.lock`），用 `tarlab figures setup` 创建，可用 `TARLAB_FIGURES_PYTHON` 指定别的解释器。`tarlab figures build` 按工作区的 `figures/spec.toml` 逐个运行图表脚本：
  - 脚本只能读自己声明的输入文件和 `figures/` 下的代码，只能写自己的输出目录，不能启动子进程（Python 审计钩子强制执行）；
  - 图按目标模板实测的 `\columnwidth`/`\textwidth` 绘制（测量结果按导言区哈希缓存），字体与正文接近（默认 Times 风格的 STIX，可选 Computer Modern 或无衬线），字体以 TrueType 嵌入，没有 Type 3；
  - 输出写到 `figures/out/<id>/`（PDF、300 dpi 的 PNG 预览、表格 `.tex` 片段），论文用到的文件复制到 `paper/generated/`；脚本、输入、输出的哈希记在 `figures/out/provenance.json`。输出逐字节确定，所以 `tex check` 能重新生成并比对。
  - 结构图和方法图用 `[[diagram]]`：源文件是 Graphviz 的 `.dot`（只用它算布局，再转成 TikZ）或直接写 `tikzpicture` 的 `.tex`，用 pdfLaTeX 按正文字体、8 pt 编译成 PDF，保持自然大小，超过目标宽度就报错。编译在只含源文件和声明输入的临时目录里进行（`openin_any=p`），PDF 元数据固定，输出逐字节确定。
  规格格式和脚本辅助函数见 `tar_lab/figures/build.py` 和 `tar_lab/figures/runtime/tarlab_fig.py` 的文档字符串。
- `tectonic/bin/tectonic`：备用引擎，只在找不到 TeX Live 时使用。Tectonic 0.17.0（`tectonic-0.17.0-x86_64-unknown-linux-gnu.tar.gz`，SHA-256 `1a715688baf591e650c8aeb160ae934e181685eecbb38b317de30b269ac5d606`），基于 XeTeX，字体和断行可能与会议用的 `pdflatex` 不同；第一次编译会自动下载用到的宏包。可用 `TARLAB_TECTONIC` 指定路径。

平台和引擎使用各自独立的环境。平台通过命令行和运行目录里的文件调用引擎，不导入引擎的 Python 代码。

凭据不入库：`.env`、`engines/ai-scientist-v3/.env`、`deploy/local/env/*.env` 都已被 Git 忽略，只提交 `*.env.example` 模板。引擎里的 Agent 用你本机已登录的 Codex 订阅（`HEADLESS_BILLING=subscription`），不用 API 密钥。

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
- **AI-Scientist-v3 引擎**：子问题有自动打分、值得反复搜索改进时（方法变体、结构或超参数搜索、在基准上超过基线），主 Agent 可以派“引擎工单”。先由工程师在工作区里建好引擎任务目录（`task.md`、`evaluate.py`、`capture.py`、`program_0/`，以及任务自己的 `.venv`），平台检查完整后才放行。引擎在本机原生运行，候选程序用任务的 `.venv` 评估，可以用 GPU；结果放在 `workspace/engine_runs/`，大约每 10 分钟跑一个想法。
- **封存的最终评估**：实验方案一开始就定好留出数据和评估协议，开发、调参、选模型时都不碰。候选方法定下来后，工程师写好 `eval/final/protocol.md`、评估脚本和 `eval/final/frozen.txt`（要评估的代码、配置和权重），主 Agent 再派最终评估工单。平台在派单时给这些文件算指纹，工单结束后再核对一遍，记录写进 `results/final/<工单号>/SEAL.json`，有改动就通知你。论文里的主要结果只能来自这里。再跑一次最终评估要经过你同意，并在论文里披露。
- **执行者分工**：高难度角色（选题设计 designer、审稿 reviewer）默认交给 Devin（`claude-opus-5-5-medium`），其余角色用 Codex。主 Agent 负责和用户沟通（立项提问、立项书、审批、回复消息）和全程决策，默认也用 Devin，`TARLAB_MAIN=codex` 可改；它以只读权限运行（`--permission-mode auto`），回答按 JSON Schema 校验，不合格时在同一会话里纠正一次。例行步骤交给调度助理（默认 Codex，`TARLAB_DISPATCHER=none` 关闭）：只有调研、工程、写作工单正常完成时才由它决定下一步；它不能向用户提问、请求审批、改立项书、取消工单、派最终评估或结束项目，遇到这些或拿不准时上报（`escalate`），作废它的回答，改由主 Agent 决策（账本记 `pi.escalated`）。其余情况（立项、用户消息和回复、工单失败、选题设计/审稿/分析/最终评估完成、预算和期限）都由主 Agent 处理。审稿工单分三类（`review`）：选题审稿（topic）和终审（final）用 Devin，中途审稿（interim，实验设计、中间结果、草稿）降一档用 Codex；用户批准选题之前、或交付物是 `topic_*`/`final_review` 的审稿，一律按高难档。用 `TARLAB_TIERS=high=codex`（可写多项，如 `high=devin,low=codex`）改分工；找不到 Devin CLI 时高难档自动改用 Codex 并给出警告。Devin 工单的 MCP 服务器写在工作区的 `.devin/mcp_config.local.json`（只含命令和参数，不含任何环境变量值；不进快照），各服务器从环境变量 `TARLAB_ORDER_ID` 读取自己属于哪张工单。Devin 的花费按 `devin models` 列出的价格估算。
- **额度用完**：某个执行者报额度不足或限流时，它的工单暂停 30 分钟后自动重试；主 Agent 也按同样的 `TARLAB_FALLBACK` 切换（Devin 用完时由 Codex 接着决策），每次调用用的模型记在账本里，花费按各自的价格算。默认 `TARLAB_FALLBACK=devin=codex`：Devin 额度用完时，工单改由 Codex 接着做（账本里记录切换）。`codex=devin` 默认不开，因为 Devin 超出套餐额度后另外收费。设成 `TARLAB_FALLBACK=none` 则一律等额度恢复。`tarlab devin usage` 显示 Devin 套餐额度还剩多少（读取交互界面的 `/usage` 面板，不调用模型）。
- **运行环境预检**：`tarlab doctor` 检查每个阶段需要的宿主工具（git、Codex/Devin CLI 及登录状态、TeX Live、poppler、绘图环境，以及可选的 Graphviz 和 arXiv 用的 TeX Live 2025）。项目运行时也会先检查一遍，缺项立即通知；派发某个阶段的工单前再查这个阶段需要的工具，缺了就拒绝该工单并说明缺什么、怎么装，其他阶段照常进行。

## 平台命令

```bash
uv run tarlab undermind login          # 浏览器授权 Undermind（一次性；之后自动刷新令牌）
uv run tarlab undermind status         # 查看凭据状态（不联网）
uv run tarlab undermind tools          # 列出工具及权限判定（默认只读）
uv run tarlab undermind call <tool> --args '{"key": "value"}'
uv run tarlab engine summary ../runs/circpack/codex/20261116_143042   # 汇总一次引擎运行
uv run tarlab figures setup            # 创建论文图表专用环境（一次性）
uv run tarlab figures build --workspace <工作区>   # 按 figures/spec.toml 生成图表
```

## 文献检索密钥

Undermind 不可用时，Agent 用 `literature` 工具查 OpenAlex、Semantic Scholar 和 arXiv（`tarlab literature search "world models"` 可以手动试）。不配密钥也能用，但 Semantic Scholar 的匿名额度是所有匿名用户共享的，经常返回 429。

- **Semantic Scholar 密钥**：在 <https://www.semanticscholar.org/product/api> 点 “Request an API Key” 申请（免费）。密钥给你独享的 1 次/秒（所有接口合计）。
- **放在哪里**：后台服务（systemd 用户服务）和每张工单启动的文献服务进程不一定继承你 shell 里的环境变量，所以建议写进 `~/.config/tar-lab/literature.env`（遵循 `XDG_CONFIG_HOME`；也可用 `TARLAB_LITERATURE_ENV` 指定别的路径），格式和 `email.env` 一样，每行 `KEY=VALUE`，`#` 开头为注释，值可以加引号：

  ```bash
  mkdir -p ~/.config/tar-lab
  cat > ~/.config/tar-lab/literature.env <<'ENV'
  TARLAB_S2_API_KEY=你的密钥
  # TARLAB_OPENALEX_API_KEY=...      # 可选，OpenAlex 密钥
  # TARLAB_OPENALEX_MAILTO=you@example.org
  ENV
  chmod 600 ~/.config/tar-lab/literature.env
  ```

  支持的键：`TARLAB_S2_API_KEY`（或 `SEMANTIC_SCHOLAR_API_KEY`）、`TARLAB_OPENALEX_API_KEY`（或 `OPENALEX_API_KEY`）、`TARLAB_OPENALEX_MAILTO`。同名环境变量优先于文件。文件对其他用户可读时会在 stderr 提醒你 `chmod 600`；密钥本身不会写进日志。
- **限速**：最多 3 张工单并行，各有自己的文献服务进程，所以本机所有进程共用一个限速器（`~/.local/state/tar-lab/rate/`，遵循 `XDG_STATE_HOME`）：Semantic Scholar 两次请求至少间隔 1.05 秒（有没有密钥都一样，可用 `TARLAB_S2_MIN_INTERVAL` 调整），arXiv 至少间隔 3 秒（`TARLAB_ARXIV_MIN_INTERVAL`）。遇到 429/503 时按服务器的 `Retry-After` 等待（最多 60 秒），没有这个头就指数退避。
- **检查配置**：`tarlab literature status` 显示密钥文件路径与权限（不是 0600 会警告）、Semantic Scholar / OpenAlex 密钥和 mailto 是否已设置及来源（环境变量名或文件，密钥最多只显示末 4 位）、各来源的最小请求间隔和距上次请求的时间。加 `--online` 会对每个来源发一次最小请求（走同一套请求头、密钥和限速器），报告 OK / HTTP 状态 / 错误和延迟；带密钥时 401/403 表示密钥无效，全部失败时退出码为 1。加 `--json` 输出 JSON。

## 引擎与上游

`engines/ai-scientist-v3/` 只保留了上游的想法搜索循环（investigation），任务起草、消融、上游自带的最终评估、事后分析、写论文、外部评审、网页查看器、容器支持和并行 worker 都已删除，所以不再从上游合并更新。删了什么、为什么删，见 [patches/ai-scientist-v3/README.md](patches/ai-scientist-v3/README.md)。需要和上游完整流程对比时，用 `engines/ai-scientist-v3-original/`（工单执行者 `ai-scientist-original`，同样原生运行）。
