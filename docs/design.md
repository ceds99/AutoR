# 当前实现与下一阶段方案

更新：2026-10-14。已实现部分对应 v0.1.0；后文的 v0.2 是建议，尚未落地。

## 目标与职责

AutoR 的交付物是一个固定 outer，用评测证据对外部提供的 inner harness 做版本化探索。当前以工程闭环为验收重点，不承诺优化收益。PPT 仅是最初的场景示例，不属于当前产品边界。

| 角色 | 职责 | 当前边界 |
|---|---|---|
| MA | 创建初始 inner | 作为 seed 输入，其他关系未定义 |
| Outer proposer | 读取父代和评测证据，输出 diagnosis、hypothesis、edits | Codex + autor-research skill；只读，不负责打分与晋升 |
| Outer controller | 选父、校验与应用修改、预算、评测调度、归档、晋升 | 固定 Python 程序，实验内不自修改 |
| Inner | 执行具体任务并产出交付物 | 当前真实预设为 Codex，唯一可变文件为 prompt.md |
| Evaluator | 运行 inner、测量每题结果并返回证据 | 本地 HumanEval、Harbor adapter 或未来 BOS command wrapper |

```mermaid
flowchart LR
  MA[外部 seed / MA 输出] --> Store[候选档案]
  Store --> Select[固定策略选父]
  Select --> Propose[Codex + research skill]
  Evidence[每题反馈与历史] --> Propose
  Propose --> Patch[结构化修改建议]
  Patch --> Controller[校验 / 快照 / 预算]
  Controller --> Eval[运行 inner 并评测]
  Eval --> Evidence
  Evidence --> Store
  Store --> Export[导出与独立复测]
```

## 已实现：一次循环如何运行

1. 导入 seed，固定模型、CLI/任务版本、文件白名单、评测器和预算，保存源码与快照摘要。
2. 测量 baseline。完整的零分也是有效测量；基础设施失败单独记录。
3. 从候选档案选择父代，将父代、逐题结果和历史交给 proposer。
4. Proposer 返回诊断、假设和整文件替换。控制器拒绝越界路径、重复候选、无改动、过大文件、必需指令丢失等情况。
5. 创建完整子代快照，调用 evaluator，核对任务覆盖、内容摘要和所要求的提示生效回执。
6. 计算父子逐题变化及总分；只有严格更高才替换 best，同分或低分仍留档。
7. 在次数或累计调用时间预算内继续，停止时生成 findings.md。已评测候选可独立导出。

## 代码定位

| 模块 | 作用 |
|---|---|
| [cli.py](../autor/cli.py) | init、run、status、doctor、export 等命令 |
| [engine.py](../autor/engine.py) | 循环、选父、提案校验、候选状态和晋升 |
| [adapters.py](../autor/adapters.py) | Codex 提案、demo / command / Harbor 评测协议 |
| [storage.py](../autor/storage.py) / [config.py](../autor/config.py) | 文件摘要、原子写入、锁、固定契约 |
| [humaneval.py](../autor/humaneval.py) | 固定任务下载、真实 inner 调用、本地受限 Python 评分和 trial 复用 |
| [bundles.py](../autor/bundles.py) | 可搬迁导出、完整性校验、独立复测 |
| [report.py](../autor/report.py) | 从状态派生可读报告 |
| [research skill](../autor/skills/autor-research/SKILL.md) | proposer 的研究约束和输出要求 |

## 已实现：搜索与状态

`hill_climb` 始终选择当前最高平均分候选。`branching` 在已评测档案中按深度、孩子尝试次数和分数选父，优先每个父代展开两个孩子；低分候选仍可繁殖。两者都串行运行，尚无独立维护的 beam、UCB 或 MCTS 状态。

候选状态为 `proposing → ready → evaluating → evaluated`。无效提案是 `rejected`，运行失败为 `error`，结果不确定的中断为 `interrupted`。

调用前预留预算并落盘；失败也消耗次数。恢复只接纳确定落盘的完整结果，不自动重放不确定的收费调用。`--steps` 限制本次 CLI 会话新增提案数，不增加总预算。一次实验仅允许一个写入进程。

`run.json` 保存配置、摘要、事件、候选、parents、得分和 best；候选目录保存 harness、proposal、diff、evaluation 与原始日志。`parents` 使用列表为后续合并预留结构，当前每个子代只有一个父代。

## 已实现：测量和导出边界

- 总分统一为固定 task × attempt 集合的平均分，不信任外部另报的总分。
- 任务缺失、重复、非有限分数、任务摘要变化会阻止比较和晋升。
- 提示回执证明候选文本进入请求，不证明某条修改导致了得分变化。
- 本地 HumanEval 使用原始测试、每题一次取样；不报告完整 pass@k，也无独立 held-out 结论。
- 运行版本、固定任务和预算可追溯；远端模型权重与生成结果不保证逐字可复现。
- 导出包含 inner 快照和 provenance。HumanEval 导出还包含固定数据和许可证；run-export 用接收环境重新解析执行路径。
- 普通 command 后端依赖外部程序，不自动成为自包含导出。Harbor 仍需要 Docker 和认证。

v0.1 已完成真实失败反馈和同机新 venv 导出验收，详见 [验收摘要](evidence/v0.1.0.json)。人为 scaffold-only seed 的修复是链路测试，不是研究效果证明。

## 下一阶段：先建立 Terminal-Bench 实验场景

优先级已调整：先验证任务和评测信号，再扩展选父算法。总共预留 6 道任务，其中 4 道用于搜索，2 道只用于最终验证。

搜索任务固定为：

| 任务 | 覆盖的能力 | baseline 后重点检查的失败类型 |
|---|---|---|
| fix-git | Git 状态与变更恢复 | 历史定位、恢复目标、验证遗漏 |
| log-summary-date-ranges | 日志聚合与日期处理 | 日期边界、计数与输出格式 |
| nginx-request-logging | 服务配置与启动验证 | 配置位置、依赖、服务状态 |
| cancel-async-tasks | Python 异步控制 | 并发上限、取消传播和清理 |

这些是预期覆盖的类型，不是已观察到的失败。任务定义与提交来自 [Terminal-Bench 2.0](https://github.com/harbor-framework/terminal-bench-2/tree/69671fbaac6d67a7ef0dfec016cc38a64ef7a77c)；没有读取参考解或评分测试来设计提示。

已实现 [baseline pilot 脚本](../scripts/terminal_bench_pilot.py)：四道任务显式固定到提交，串行单次采样，无重试、不调用 proposer。inner 上限每题 180 秒，整体外部运行上限 1200 秒；这是受限预算的 pilot，不是官方排行榜设置。任务镜像目前沿用上游标签，真实启动时还需要记录镜像 digest、架构及实际 CLI 版本。

进入 inner 比较前的门槛：Docker/认证与模型可用；四题全部有有效测量；至少存在可分析的任务失败。超时、镜像构建或认证错误不能冒充零分。如果全通过，记录原结果并重新设计任务批次，不能人为破坏 seed 以制造“真实失败”。第一轮只比较 baseline 和最多两个子代；仍使用现有搜索策略。

两道保留任务已在本地单独预选，并对带随机盐的清单生成承诺摘要。公开配置只有摘要和数量。它们目前尚未评测；本地目录分开存储只是准备状态，**不是已完成的隔离**。正式搜索前，应把清单及任务交给 outer 无法访问的独立评测端：不挂载到 outer、不写入 prompt/skill/evidence/history、不让搜索日志引用它们。还需在任何 outer 启动前验证该访问边界。

搜索结束时先冻结候选摘要和选择理由，再在保留任务上同时测 baseline 与最终候选。结果只用于最终报告，不用于再选父、继续提案或在多个候选中择优。更改选择后要新建实验；2 道任务只能提供初步检查，不能支持显著性或广泛泛化结论。当前 pilot 只实现 baseline，未实现该独立评测服务或搜索冻结门禁。

现阶段状态：配置与 40 项测试通过；本机无 Docker，尚无 Terminal-Bench 实测成绩。模型服务与镜像可运行性也尚未通过真实容器验证。

## 实验场景稳定后：有界搜索建议

这部分后置到上面的 baseline 与隔离检查完成后。现有真实演化记录只包含单个子代，无法比较不同选父策略；公开的小样本任务也容易出现满分，缺少区分候选的信号。

| 建议项 | 最小实现 | 验收重点 |
|---|---|---|
| 策略接口 | 把选父与搜索状态更新从主循环中拆出，保留当前策略 | 不改变评测和预算语义；可用确定性记录测试 |
| Beam search | 宽度 2，分批生成子代，按相同任务集成绩选保留候选 | 真正发生分叉、保留与淘汰；所有候选继续入档 |
| 小规模预算 | 最多 6 个新候选；固定任务集，累计外部调用上限建议 10 分钟 | 时间到可正常停止，不保证在上限内跑完所有候选 |
| 复测记录 | 候选快照与评测尝试分开建模；明确的重复采样预算 | 复测不是重复候选，也不覆盖首次成绩或自动重放中断调用 |
| 独立验证 | 搜索集用于选父；搜索完成后再评测预先保留的验证任务 | 验证结果不回流选父；先验声明任务切分 |
| 对照报告 | 相同模型、任务、调用/时间上限下比较 hill_climb 与 beam | 展示通过率、耗时、调用数及实际预算，不只展示最高分 |

达到实验场景门槛后，再完成策略接口与 beam，并用合成树验证选父和恢复；随后补显式复测。真实任务应能产生自然失败，且完整保留 baseline 结果；不要为了展示提升选择只会修复人为错误的案例。

本轮先保持 prompt 的可修改范围，避免同时改变搜索算法和搜索空间。涉及工具或工作流代码时，再增加构建/导入 smoke 检查及更强执行隔离。

暂不引入 MCTS、多父代合并、异步并行或 outer 自修改。这些能力各自增加统计、状态恢复或执行安全问题；当前还没有证据表明它们是限制搜索的主要因素。

## Raven 参考的具体取舍

参考 [Raven 的编排设计](https://github.com/EverMind-AI/Raven/blob/ee969dbc41a4b00978b4430aae9590a5ebfba010/evolver/orchestrator/DESIGN.md)：模型负责诊断/设计，确定性程序负责推进与计算；保留候选账本和逐题变化。AutoR 已采用这些组织原则，没有依赖 Raven 代码。

Raven 的筛选/确认分层提醒我们区分探索成绩和后续验证。上面的 beam、预算与实施顺序是本项目的工程建议，并非 Raven 的现成实现；v0.1 也未实现其显著性门槛或完整归因机制。
