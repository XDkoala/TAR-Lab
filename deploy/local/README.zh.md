# 本地运行（AI-Scientist-v3 引擎）

以下命令都在仓库根目录 `TAR-Lab/` 下执行。脚本会自动使用引擎自带的 `.venv`，不需要手动激活。

## 首次准备

```bash
(cd engines/ai-scientist-v3 && uv sync --frozen)          # 创建引擎环境
cp deploy/local/env/codex-subscription.env.example deploy/local/env/codex-subscription.env
bash deploy/local/setup-docker.sh                         # 安装 Docker，构建上游 agent 镜像（需要 sudo）
# 论文阶段需要 pdftotext，所以要构建带 poppler 的 Codex 镜像：
docker build --network=host -t agentic-scientist/agent-cli:codex-0.169.0-pdf \
  -f deploy/local/docker/codex.Dockerfile deploy/local/docker
```

使用 API 密钥的 agent，密钥填在 `engines/ai-scientist-v3/.env`（已被 Git 忽略）。

## 常用命令

```bash
bash deploy/local/agsci-local.sh --help
bash deploy/local/try-codex-subscription.sh         # 流程预览，不调用模型
bash deploy/local/try-codex-subscription.sh --run   # 真实跑一轮研究循环，会消耗订阅额度
bash deploy/local/agsci-local.sh inspect            # 打开 Run Inspector，读取 ../runs
bash deploy/local/agsci-local.sh doctor examples/circpack agent.name=codex agent.model=gpt-6.1-sol \
  container.image=agentic-scientist/agent-cli:codex-0.169.0-pdf \
  "container.env_file='$PWD/deploy/local/env/codex-subscription.env'"
```

## agsci-local.sh 做了什么

- 进入 `engines/ai-scientist-v3/` 目录，运行引擎 `.venv` 里的 `agsci`。所以命令里的相对路径（如 `examples/circpack`）是相对引擎目录的。
- 清除宿主机 ROS 环境设置的 `PYTHONPATH` 和 `PYTHONHOME`。
- 如果存在 `../.tools/node`，把它加入 `PATH`。可以用 `TARLAB_NODE_DIR` 换成别的目录。
- `run` 默认追加 `paths.results_root=../runs`，`inspect` 默认追加 `--results-root ../runs`。可以用 `TARLAB_RUNS_DIR` 换成别的目录；命令行里已经显式指定的，就不再追加。
- 如果当前会话还没获得 docker 组权限，用 `sg docker` 重新执行自己。

## 归档

`archive/continue-circpack.sh` 是 2026-11-16 circpack 实验的续跑脚本，里面还是迁移前的旧路径，只作记录保存，不保证能直接运行。
