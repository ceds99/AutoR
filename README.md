# AutoR

**固定的 outer，用评测反馈探索和改进另一个 agent harness（inner）。**

Outer 由 Codex + `autor-research` skill 和一个 Python 控制器组成。模型提出修改，控制器负责快照、校验、评测、预算、血缘、恢复与导出。MA 只负责提供初始 inner，目前不参与搜索循环。

## 当前进展 · 2026-10-16

v0.1.0 已冻结；当前开发版为 **0.2.0.dev2**，新增 **adaptive_beam**：定向提案、逐题互补保留、晋升前复测、证据驱动的展开名额，以及有条件的双父代混合。原 beam 保留作对照。**尚未证明真实 harness 优化有效。**

| 当前能力 | 实际证据与边界 |
|---|---|
| 搜索组织 | hill_climb、branching、beam、adaptive_beam；保留独立测量、双父代血缘、名额依据与未完成代 |
| 验证 | 当前 Python 3.12＋Harbor 0.24.0：96 项通过（含 10 项 BOS 预检测试）；新增预检另在 Python 3.9 通过；此前两个新 venv 的 beam 导出迁移通过 |
| Proposer 文件访问 | 仅开放暂存输入目录和系统运行库；启动前验证目录外读取、符号链接、写入及命令网络被拒绝；已通过真实 Codex canary 调用 |
| 有界执行 | 次数与时间预算、部分代恢复、失败不重放、历史最佳独立于 frontier |
| 搜索验证 | A/B/C 合成对照在相同上限下运行；验证复测、互补、混合单计名额和中断恢复，不代表真实效果 |
| 历史真实闭环 | v0.1 的 HumanEval 失败反馈与两个新 venv 导出验收；人为错误 seed 的修复不是泛化证据 |
| Terminal-Bench | 历史十题全部通过；本轮 14 题：11 个有效通过、1 个自然失败、2 次排除；正在准备失败复现，A/B/C 搜索尚未启动，保留任务未测 |
| BOS 接入 | 已核对五个 skill 包，新增只读预检与本地 HTTP 契约测试；文档服务入口 TCP 连接超时，尚无真实 BOS Run，也未实现候选发布 |

增强机制凭证见 [A/B/C 合成验证](docs/evidence/adaptive-search.json)。最新筛选见 [hard 任务与排除记录](docs/evidence/hard-task-selection.json)。此前凭证见 [六题筛选与故障记录](docs/evidence/task-selection.json)；历史记录见 [Terminal-Bench baseline 与权限验证](docs/evidence/terminal-bench-pilot.json)，合成机制验证见 [搜索对照](docs/evidence/search-expansion.json)；冻结版本的真实实验见 [v0.1 验收](docs/evidence/v0.1.0.json) 和 [实际失败修复](docs/evidence/failure-feedback.patch)。原始日志与构建包保留在本地。

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

近期优先 **任务筛选和搜索增强**。BOS 内网接入因 VPN 条件暂停，先使用 Harbor CLI＋本地 Docker。BOS 当前只有[只读预检](scripts/bos_preflight.py)与契约测试，尚无真实远端 Run；边界见 [BOS 接入凭证](docs/evidence/bos-integration.json)。

已确认四项搜索增强：定向生成候选、保留逐题互补能力、晋升前复测、根据证据分配预算；满足条件时下一轮加入一个前二改进候选的混合子代。先选到有真实失败的任务，再进行 A（现有 beam）／B（四项增强）／C（B＋混合）的同预算比较。四项增强和有条件混合已实现；真实搜索仍受自然失败与 baseline 复现门禁约束。随后逐步完善稳定接口、持久化任务、恢复、取消与可观测性。详见[当前方案](docs/design.md#下一阶段bos-接入搜索质量与工程化)。

### Terminal-Bench 后续

新一轮有界筛选已完成：WAL 恢复、正则、数据合并、Git 多分支、SQLite 截断恢复、C++ 内存故障六题均为 1.0。模型与 seed 保持不变，每题 agent 上限 300 秒。有效试验累计约 27 分 42 秒，其中 agent 执行约 7 分 44 秒；这不含失败、网络诊断和镜像准备。环境故障单独记录，中断请求费用未知。

**已找到自然失败，正在准备三组 baseline 复现。** `torch-pipeline-parallelism` 正常完成但评分为 0：单进程和双进程反向梯度与参考结果不一致，4 项测试中 2 项失败。其余 11 道本轮任务有效通过；`path-tracing-reverse` 的评分环境不兼容、`train-fasttext` 的 agent 超时单独排除。模型与 seed 不变。先预热并冻结第三方依赖缓存，再重新测量各组 seed；只有失败复现才进入搜索。筛选成绩不直接充当三组共同 baseline。

两道保留任务不用于选题、选父或调 prompt。Proposer 的命令文件访问已受限；独立保留集评测服务和候选冻结门禁仍待实现。有条件的双父代混合和显式复测已实现；MCTS、通用多父代合并与并行评测后置。
