# AutoR

**固定的 outer，用评测反馈探索和改进另一个 agent harness（inner）。**

Outer 由 Codex + `autor-research` skill 和一个 Python 控制器组成。模型提出修改，控制器负责快照、校验、评测、预算、血缘、恢复与导出。MA 只负责提供初始 inner，目前不参与搜索循环。

## 当前进展 · 2026-10-14

v0.1.0 的工程闭环已完成；当前代码与冻结版本的 outer 一致。**尚未证明 harness 优化有效。**

| 已完成 | 实际证据与边界 |
|---|---|
| 真实闭环 | baseline → 提案 → 子代 → 评测 → 归档与导出 |
| 失败反馈案例 | 人为配置错误的 seed 在 HumanEval/0 上失败，子代修复后通过；不是自然任务泛化证据 |
| 导出独立运行 | 两个新 venv 从 wheel 安装；删除生产环境及源路径后，接收环境真实复测通过 |
| 搜索组织 | `hill_climb`、按层展开的 `branching`；持久化候选档案、父子对照、预算与恢复 |
| 验证 | Python 3.12 + Harbor 0.24.0：36 项通过；Python 3.9.6：33 项通过，3 项依赖测试跳过 |

真实实验在同一台 macOS 上完成。Harbor 已做适配和契约测试，尚未执行真实 Docker job。原始日志与冻结交付包保留在本地；公开仓库提供 [去除机器路径的验收摘要](docs/evidence/v0.1.0.json) 和 [失败案例的实际改动](docs/evidence/failure-feedback.patch)。

## 阅读入口

- **[当前实现与下一阶段方案](docs/design.md)**：职责、模块、数据流、搜索规则，以及建议的 v0.2 范围。
- **[运行手册](docs/runbook.md)**：安装、合成/真实运行、Harbor/BOS、导出、排障和复现。

默认分支只维护当前文档和最新源码，不并列存放旧设计稿。公开仓库从清理后的当前快照开始；本地 v0.1.0 标签和完整交付包保留原始历史。

## 快速运行

Python 3.9+，macOS/Linux；核心没有第三方运行依赖。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/autor demo runs/demo
.venv/bin/autor export runs/demo exports/demo
.venv/bin/autor verify-export exports/demo
.venv/bin/autor run-export exports/demo --work runs/export-check
```

这个 demo 是合成字符串测试，不调用模型。真实调用需要额外环境和认证，见运行手册。

## 下一步建议（尚未实现）

可以开始扩展搜索。建议先做 **宽度 2 的 beam search、最多 6 个新候选、固定任务与时间上限**，保留当前两种策略作为对照。优先补齐多候选选择、显式复测和独立验证集的记录，再考虑 MCTS、多父代合并和并行评测。

本阶段仍保持固定 outer 和小范围 inner 修改。验收目标是“在相同预算下，搜索过程完整、可解释、可恢复”，不要求展示分数提升。
