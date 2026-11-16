# TAR-Lab：可信自主科研实验室

TAR-Lab 基于 [SakanaAI/AI-Scientist-v3](https://github.com/SakanaAI/AI-Scientist-v3)（Apache-2.0）二次开发，目标是让自主科研流程能够发现故障、定位原因、修复并验证，同时保留可追溯记录。上游功能说明见 [README.md](README.md)。

## 仓库结构

| 路径 | 内容 | 维护方 |
| --- | --- | --- |
| `src/` `tests/` `configs/` `examples/` `apps/` `docs/` `scripts/` | 上游代码 | 上游，尽量少改 |
| `tar_lab/` | TAR-Lab 扩展层（新功能优先写在这里） | TAR-Lab |
| `tests_ext/` | 扩展层测试（`pytest` 默认同时运行 `tests/` 和 `tests_ext/`） | TAR-Lab |
| `deploy/local/` | 本地启动脚本、Docker 扩展镜像、环境配置模板，见 [deploy/local/README.zh.md](deploy/local/README.zh.md) | TAR-Lab |
| `docs/tar-lab/` | 缺陷修复记录、维护记录、设计文档 | TAR-Lab |
| `patches/upstream/` | 对上游代码的修复补丁，便于向上游提交 PR | TAR-Lab |

## 本地工作区布局

仓库之外的大文件和数据不进 Git：

```
Trusted-Autonomous-Research-Lab/
├── TAR-Lab/        本仓库
├── runs/           实验运行结果（agsci-local.sh 自动传入 paths.results_root）
├── deliverables/   交付快照（论文、报告、图表），不随运行更新
└── .tools/node/    本地 Node.js 运行时（可选）
```

## 开发约定

1. **新功能优先放 `tar_lab/`**，通过配置、包装或调用上游接口接入，避免直接修改 `src/`。
2. **必须修改上游代码时**，改动要小，单独提交，提交信息说明原因，并在 `patches/upstream/` 和 `docs/tar-lab/system_defects_and_remediation_log.md` 中登记，方便以后提交给上游。
3. **凭据不入库**：`.env`、`deploy/local/env/*.env` 已被忽略，只提交 `*.env.example` 模板。
4. 提交前运行：`uv run ruff check . && uv run mypy . && uv run pytest -q -n auto`。

## 同步上游

```bash
git fetch upstream
git log --oneline HEAD..upstream/main      # 查看上游新提交
git cherry-pick <commit>                   # 按需挑选合并
```

`main` 分支跟踪上游，开发在 `dev/local` 分支上进行。
