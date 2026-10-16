# AutoR

**固定的 outer，用评测反馈探索和改进另一个 agent harness（inner）。**

Outer 由 Codex + `autor-research` skill 和一个 Python 控制器组成。模型提出修改，控制器负责快照、校验、评测、预算、血缘、恢复与导出。MA 只负责提供初始 inner，目前不参与搜索循环。

## 当前进展 · 2026-10-16

v0.1.0 已冻结；当前开发版为 **0.2.0.dev2**，新增 **adaptive_beam**：定向提案、逐题互补保留、晋升前复测、证据驱动的展开名额，以及有条件的双父代混合。原 beam 保留作对照。**已观察到真实搜索题上的单次提分，尚未证明稳定改进或策略优势。**

| 当前能力 | 实际证据与边界 |
|---|---|
| 搜索组织 | hill_climb、branching、beam、adaptive_beam；保留独立测量、双父代血缘、名额依据与未完成代 |
| 验证 | 当前 Python 3.12＋Harbor 0.24.0：105 项测试通过；此前两个新 venv 的 beam 导出迁移通过 |
| Proposer 文件访问 | 仅开放暂存输入目录和系统运行库；启动前验证目录外读取、符号链接、写入及命令网络被拒绝；已通过真实 Codex canary 调用 |
| 有界执行 | 次数与时间预算、部分代恢复、失败不重放、历史最佳独立于 frontier |
| 搜索验证 | 合成测试覆盖复测、互补、混合单计名额和恢复；首轮真实 A/B/C 已结束，未证明增强策略优势 |
| 历史真实闭环 | v0.1 的 HumanEval 失败反馈与两个新 venv 导出验收；人为错误 seed 的修复不是泛化证据 |
| Terminal-Bench | 可分析自然失败已复现；五份正式基线均为 0.0；A 最好完整观测 0.5，B/C 为 0.0，保留任务未测 |
| BOS 接入 | 已核对五个 skill 包，新增只读预检与本地 HTTP 契约测试；文档服务入口 TCP 连接超时，尚无真实 BOS Run，也未实现候选发布 |

当前对照与机制凭证见 [A/B/C 实验记录](docs/evidence/adaptive-search.json)，最新筛选见 [更难任务与失败复现](docs/evidence/harder-task-selection.json)。此前凭证见 [hard 任务与排除记录](docs/evidence/hard-task-selection.json)、[六题筛选与故障记录](docs/evidence/task-selection.json)；历史记录见 [Terminal-Bench baseline 与权限验证](docs/evidence/terminal-bench-pilot.json)，合成机制验证见 [搜索对照](docs/evidence/search-expansion.json)；冻结版本的真实实验见 [v0.1 验收](docs/evidence/v0.1.0.json) 和 [实际失败修复](docs/evidence/failure-feedback.patch)。原始日志与构建包保留在本地。

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

## 下一阶段

近期优先 **评测预算规划和候选确认**，再扩大搜索深度。BOS 内网接入因 VPN 条件暂停，先使用 Harbor CLI＋本地 Docker。BOS 当前只有[只读预检](scripts/bos_preflight.py)与契约测试，尚无真实远端 Run；边界见 [BOS 接入凭证](docs/evidence/bos-integration.json)。

四项搜索增强和有条件混合已实现，首轮 A（现有 beam）／B（四项增强）／C（B＋混合）同上限对照已完成。下一轮优先按实测耗时规划粗筛与确认额度，并检查剩余时间能否容纳一次有效尝试。后续逐步完善稳定接口、持久化任务、恢复、取消与可观测性。详见[当前方案](docs/design.md#下一阶段bos-接入搜索质量与工程化)。

### Terminal-Bench 后续

**首轮真实 A/B/C 对照已结束。** 保持 `openai/gpt-6-astra` 和初始 inner 不变，固定 Terminal-Bench 4.0 的 `cargo-flight-dispatch`（航线规划）与 `batched-eval-parity`（批量评测）。航线题在筛选和五份正式 seed 测量中均出现可分析失败；五份完整基线的两题均分全部为 0.0。候选只修改通用 `prompt.md`。

| 组别 | 基线 | 最好完整成绩 | 生成候选 / 完成整组评测的候选 | 结果性质 |
|---|---:|---:|---:|---|
| A：beam | 0.0 | 0.5 | 3 / 2 | c0002 单次提分，未复测 |
| B：四项增强 | 0.0 | 0.0 | 2 / 1 | 未观察到完整成绩提升 |
| C：增强＋条件混合 | 0.0 | 0.0 | 2 / 1 | 未形成两个确认改进父代，混合未触发 |

每组最多 6 次提案、8 次整组评测、2400 秒累计调用时间；基线复测、失败和确认均计入，三组均因时间预算停止。每组实际启动 8 个单题 trial，完成 7 个；未完成任务不补零、不用于晋升。表中候选数不含 seed，也不含未生成 harness 的超时提案。一次启动版本字符串问题消耗的两次预检名额/组及原基线成本已核验沿用，没有重置额度或重复计算基线调用。

A 的 c0002 通过批量评测题；其后代 c0003 通过全部 27 项航线检查，但另一题未完成，因此没有完整成绩。逐项检查数量仅用于诊断，官方指标仍是两题二元得分的平均值。真实运行覆盖了定向提案、基线复测及无改进分支的名额收缩；尚未验证互补保留、改进候选确认晋升和混合的实际收益。不能据此判断策略优劣或泛化。

105 项测试通过，运行结束后无遗留任务容器，保留集未评测。详见[当前对照凭证](docs/evidence/adaptive-search.json)与[筛选依据](docs/evidence/harder-task-selection.json)。下一步应先为候选复测预留额度、避免在剩余几十秒时启动提案，再扩展复杂搜索。

两道保留任务不用于选题、选父或调 prompt。Proposer 的命令文件访问已受限；独立保留集评测服务和候选冻结门禁仍待实现。有条件的双父代混合和显式复测已实现；MCTS、通用多父代合并与并行评测后置。
