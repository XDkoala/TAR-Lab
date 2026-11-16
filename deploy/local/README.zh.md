# 本地运行

以下命令都在仓库根目录 `TAR-Lab/` 执行，无需手动激活 Python 环境。

## 首次准备

```bash
uv sync                                                   # 创建 .venv
cp deploy/local/env/codex-subscription.env.example deploy/local/env/codex-subscription.env
bash deploy/local/setup-docker.sh                         # 安装 Docker 并构建上游 agent 镜像（需要 sudo）
# 论文阶段需要 pdftotext，构建带 poppler 的 Codex 镜像：
docker build --network=host -t agentic-scientist/agent-cli:codex-0.169.0-pdf \
  -f deploy/local/docker/codex.Dockerfile deploy/local/docker
```

使用 API 密钥的 agent 需要在仓库根目录的 `.env` 中填写密钥（该文件已被 Git 忽略）。

## 常用命令

```bash
bash deploy/local/agsci-local.sh --help
bash deploy/local/try-codex-subscription.sh         # 流程预览，不调用模型
bash deploy/local/try-codex-subscription.sh --run   # 真实运行一次研究循环，会消耗订阅额度
bash deploy/local/agsci-local.sh inspect            # Run Inspector，读取 ../runs
bash deploy/local/agsci-local.sh doctor examples/circpack agent.name=codex agent.model=gpt-6.1-sol \
  container.image=agentic-scientist/agent-cli:codex-0.169.0-pdf \
  "container.env_file='deploy/local/env/codex-subscription.env'"
```

## agsci-local.sh 做了什么

- 使用仓库内的 `.venv/bin/agsci`，并清除宿主机 ROS 环境带来的 `PYTHONPATH`、`PYTHONHOME`。
- 如果存在 `../.tools/node`（可用 `TARLAB_NODE_DIR` 覆盖），就把它加入 `PATH`。
- `run` 命令默认追加 `paths.results_root=../runs`，`inspect` 命令默认追加 `--results-root ../runs`（可用 `TARLAB_RUNS_DIR` 覆盖；命令行中显式指定时不会追加）。
- 当前会话尚未生效 docker 组权限时，用 `sg docker` 重新执行。

## 归档

`archive/continue-circpack.sh` 是 2026-11-16 circpack 实验的续跑脚本，里面的路径仍是迁移前的旧路径，仅作记录保存，不保证能直接运行。该实验和论文已经完成，不需要再续跑。
