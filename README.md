# AutoR

**固定的 outer，用评测反馈探索和改进另一个 agent harness（inner）。**

Outer 由 Codex + `autor-research` skill 和一个 Python 控制器组成。模型提出修改，控制器负责快照、校验、评测、预算、血缘、恢复与导出。MA 只负责提供初始 inner，目前不参与搜索循环。

## 当前进展 · 2026-10-14

v0.1.0 已冻结；当前开发版为 **0.2.0.dev1**，新增有界 **beam search**。默认宽度 2，每个父代尝试 2 个孩子，合成演示最多生成 6 个新候选。**尚未证明真实 harness 优化有效。**

| 当前能力 | 实际证据与边界 |
|---|---|
| 搜索组织 | hill_climb、branching、beam；完成一代后筛选，记录保留/淘汰/失败 |
| 验证 | Python 3.12＋Harbor 0.24.0：57 项通过；Python 3.9：53 项通过、4 项依赖测试跳过；两个新 venv 的 beam 导出迁移通过 |
| Proposer 文件访问 | 仅开放暂存输入目录和系统运行库；启动前验证目录外读取、符号链接、写入及命令网络被拒绝；已通过真实 Codex canary 调用 |
| 有界执行 | 次数与时间预算、部分代恢复、失败不重放、历史最佳独立于 frontier |
| 搜索验证 | 三种策略各 6 次提案、7 次合成评测；验证低分分支可继续、分叉与淘汰，不代表真实效果 |
| 历史真实闭环 | v0.1 的 HumanEval 失败反馈与两个新 venv 导出验收；人为错误 seed 的修复不是泛化证据 |
| Terminal-Bench | 四题真实 baseline 全部通过（4/4，均分 1.0，约 13 分 17 秒）；两道保留任务未测，真实候选搜索未启动 |

最新凭证见 [Terminal-Bench baseline 与权限验证](docs/evidence/terminal-bench-pilot.json)，合成机制验证见 [搜索对照](docs/evidence/search-expansion.json)；冻结版本的真实实验见 [v0.1 验收](docs/evidence/v0.1.0.json) 和 [实际失败修复](docs/evidence/failure-feedback.patch)。原始日志与构建包保留在本地。

## 阅读入口

- **[当前实现与下一阶段方案](docs/design.md)**：职责、模块、数据流、搜索规则，以及尚未完成的验证环节。
- **[运行手册](docs/runbook.md)**：安装、合成/真实运行、Harbor/BOS、导出、排障和复现。

默认分支只维护当前文档和最新源码，不并列存放旧设计稿。公开仓库从清理后的当前快照开始；本地 v0.1.0 标签和完整交付包保留原始历史。

## 快速运行

Python 3.9+，macOS/Linux；核心没有第三方运行依赖。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/autor demo runs/demo --strategy beam
.venv/bin/autor export runs/demo exports/demo
.venv/bin/autor verify-export exports/demo
.venv/bin/autor run-export exports/demo --work runs/export-check
```

这个 demo 是合成字符串测试，不调用模型。真实调用需要额外环境和认证，见运行手册。

## 接下来的真实实验

本批四题 baseline 均通过，尚未观察到可分析的自然任务失败，当前样本没有分数提升空间。下一步先调整搜索任务，取得有区分度的反馈，再比较 baseline 与最多两个子代；暂未进行 Terminal-Bench 的真实候选搜索。

两道保留任务不用于选题、选父或调 prompt。Proposer 的命令文件访问已受限；独立保留集评测服务和候选冻结门禁仍待实现。MCTS、多父代合并、并行评测与显式重复采样继续后置。
