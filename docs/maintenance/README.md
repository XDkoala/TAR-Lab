# 维护记录

2026-11-16/17 本地安装、迁移和冒烟检查的历史记录。文件中的路径是当时的位置（如 `AI-Scientist-v3/`、`本地运行配置/`、`研究成果/`），不代表当前布局，当前布局见 [README.md](../../README.md)。

- `install-history.txt`：安装过程记录
- `environment-check.txt`、`pipeline-preview.txt`、`subscription-smoke-check.txt`：环境检查与冒烟检查输出
- `fix-task-prompt-2026-11-17.txt`：FIX-001/FIX-002 修复任务的交接提示词
- `workspace-readme-2026-11-17.txt`、`usage-notes-2026-11-17.txt`：第一次整理后的总目录说明和使用说明原文
- `migration-manifest-2026-11-17.json`、`cleanup-log-2026-11-17.json`：2026-11-17 第一次整理时的迁移清单和清理记录

2026-11-17 第二次整理将目录改为英文名：`AI-Scientist-v3/` 改名为 `TAR-Lab/`，`results/` 移到 `../runs/`，`研究成果/2026-11-17_26圆装填/` 移到 `../deliverables/2026-11-17_circpack26/`（子目录改为 `report/`、`data-figures/`、`paper/`、`paper/source/`、`report/process-logs/`），`本地运行配置/` 拆分到 `deploy/local/`、`docs/tar-lab/maintenance/`、`patches/upstream/` 和 `../.tools/node/`。

2026-11-17 第三次整理：TAR-Lab 改为平台仓库，上游代码移入 `engines/ai-scientist-v3/`（带独立 `.venv` 和 `.env`），`docs/tar-lab/` 并入 `docs/`，`tests_ext/` 改为 `tests/`，`patches/upstream/` 改为 `patches/ai-scientist-v3/`。
