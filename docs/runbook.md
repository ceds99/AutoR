# v0.1 运行与交付手册

AutoR 是固定的 outer：接收 inner seed，调用 proposer，校验文件修改，测量候选，保存血缘并导出。MA 不在本版运行链路内。首版真实预设只允许改 `prompt.md`，不修改模型、评测器或预算。

## 环境与安装

| 路径 | 必需条件 | 本次验收范围 |
|---|---|---|
| 核心与合成演示 | Python 3.9+，macOS/Linux | macOS 两个独立 Python 3.12 虚拟环境 |
| 本地真实 HumanEval | macOS、sandbox-exec、系统 Python、可用且已认证的 Codex CLI | 真实模型调用、原始测试、导出迁移后重新调用 |
| Harbor | Python 3.12+、Harbor 0.24.0、Docker daemon、模型凭据 | schema / adapter / 模板兼容测试；未执行 Docker job |
| 自定义 BOS / command | 已实现同步 JSON 协议的外部命令 | 协议测试；外部服务由使用者提供 |

从 GitHub 获取当前源码并安装：

```bash
git clone https://github.com/ceds99/AutoR.git
cd AutoR
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/autor --help
```

若已有本地冻结交付包，也可安装其中的 wheel：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install dist/autor_outer-0.1.0-py3-none-any.whl
.venv/bin/autor --help
```

也可以用 `uv venv --python 3.12 .venv` 和 `uv pip install --python .venv/bin/python dist/autor_outer-0.1.0-py3-none-any.whl`。这些命令在解压交付目录下执行。检查发行包的 `SHA256SUMS` 后再安装。

## 不调用模型的检查

```bash
.venv/bin/autor demo runs/demo
.venv/bin/autor status runs/demo
.venv/bin/autor run runs/demo
.venv/bin/autor export runs/demo exports/demo
.venv/bin/autor verify-export exports/demo
.venv/bin/autor run-export exports/demo --work runs/demo-export-check
```

恢复不重置预算，也不重新测量已完成候选。演示只按提示中的字符串打分，明确标记 synthetic，不代表真实 agent 能力。

`verify-export` 不发模型请求。它检查文件集合、摘要、候选来源和当前 outer 兼容性；它不是数字签名，也不提供第三方身份认证。

## 一次真实短闭环

确认 `codex --version` 和 `codex login status` 可用。初始化固定 CLI 版本；实验中升级 CLI 会被拒绝。下面示例的模型必须是当前账户实际可用的显式模型 ID；本次验收使用 `gpt-6-astra`、low reasoning、CLI 0.153.4。

```bash
.venv/bin/autor init-humaneval runs/real --model gpt-6-astra
.venv/bin/autor doctor --run runs/real
.venv/bin/autor run runs/real
.venv/bin/autor status runs/real
.venv/bin/autor export runs/real exports/real
```

初始化联网下载固定 HumanEval 数据，不调用模型。默认两题，baseline 与一个子代，共四次 inner 调用和一次 outer 提案。每题一次取样，累计外部调用时间上限 600 秒。`doctor` 检查可执行程序、平台与数据摘要，不保证认证或远端模型可用。

要复现带失败反馈的验收案例：

```bash
.venv/bin/autor init-humaneval runs/failure-feedback \
  --model gpt-6-astra --task HumanEval/0 \
  --seed-prompt examples/failure-feedback/prompt.md
.venv/bin/autor run runs/failure-feedback
```

这个 seed **人为配置为 scaffold-only**，要求函数返回 None。它用于让真实评测产生失败反馈，测试 outer 如何响应错误配置；不能据此声称解决了自然出现的能力缺陷。评分仍由未修改的原始测试产生。只运行一个后代，不追加搜索以追求成功。模型有随机性，其他运行可能产生不同提案或分数。

## 导出迁移与独立执行

```bash
.venv/bin/autor export runs/failure-feedback exports/inner
.venv/bin/autor verify-export exports/inner
.venv/bin/autor run-export exports/inner --work runs/independent-check
```

可以把整个 `exports/inner` 搬到另一目录或机器，安装相同 wheel 后使用相同命令。`run-export` 只评测指定 inner，不启动 outer 搜索；真实后端会产生模型用量。它要求新的 work 目录，拒绝自动重放已有调用。

| 文件 | 用途 |
|---|---|
| `harness/` | 完整 inner 快照与固定 runtime |
| `provenance.json` | 候选、父代、历史成绩、原实验配置与 outer 摘要 |
| `evaluation-spec.json` | 可搬迁的评测方式和时间上限 |
| `benchmark.json`、`HUMANEVAL_LICENSE` | HumanEval 导出包含的固定任务与原始测试、许可证 |
| `bundle.json` | 文件摘要与 outer 版本 |
| `README.txt` | 最小运行说明 |

历史 provenance 保留原机器绝对路径供追溯；新执行不依赖这些路径。HumanEval 后端使用新环境 Python 和包内 evaluator，读取 bundle 内数据。Harbor 的 `run-export` 根据新位置重新生成 job 配置；旧 `harbor-job.json` 仍是特定位置的便捷文件。

普通 command evaluator 不能自动带走任意外部程序和服务：此类导出标记 `portable: false`，需另行部署对应 evaluator。`verify-export` 显示 `outer_matches: false` 时，请安装对应构建，勿篡改实验摘要绕过检查。

导出包中的测试供 evaluator 使用，不能作为模型输入；原始断言和参考答案不会出现在任务请求中。v0.1 的 proposer/inner 只读运行和提示约束适用于受信任的研究流程，不构成恶意模型与评测数据之间的完整隔离。扩大到不可信 harness 时需独立容器/主机。

## 失败、中断与证据

- `evaluated` 且 score 为 0：评测已完成，inner 未通过测试；可作为搜索反馈。
- `error`：运行器、协议或基础设施失败，不作为零分参与比较。
- `rejected`：提案越界、重复、无改动或破坏契约；已消耗提案预算。
- `interrupted`：调用结果不确定；保留预算与日志，不自动重放可能收费的调用。

查阅 `findings.md` → 候选 `proposal.json` / `change.patch` → `evaluation.json` → trial 的 `request.txt`、`solution.py`、`result.json` 与 logs。顶层 `run.json` 是状态与血缘记录。CLI 正常停止返回 0；运行错误返回 1；处理中断返回 130。合法低分不会导致命令返回错误码。

预算按调用次数与累计外部调用时间限制；不提供 token 或美元硬上限。固定模型名称不能锁定服务端模型权重，日志用于追溯条件，不承诺生成内容逐字可复现。

源码或 skill 改变后应建立新实验。旧实验保留为历史证据；恢复需要匹配其 outer 的构建。复制实验目录不等同于迁移实验，因为冻结的 command 配置可能包含旧绝对路径；需要迁移的是正式导出 bundle。

## 重跑发布验收

安装 uv 后，在仓库根目录构建 wheel，再运行：

```bash
uv build --wheel
python3 scripts/accept_release.py \
  --wheel dist/autor_outer-0.1.0-py3-none-any.whl \
  --output runs/acceptance-new
```

上面只运行合成验收。如需真实失败闭环和导出复测，追加 `--real-model gpt-6-astra --seed-prompt examples/failure-feedback/prompt.md`，约四次模型调用。脚本在工作区外创建两个全新 venv，从 wheel 安装，清除 PYTHONPATH；生成导出后删除生产环境和源实验路径，再在另一个环境独立执行。失败也保留 acceptance.json 与命令日志，重试必须选择新的输出目录。

验收仍共享当前主机、系统工具和 Codex 登录，不等同于跨操作系统或第二台物理机器测试。

## 外部 seed 与恢复

```bash
.venv/bin/autor init runs/custom --seed path/to/inner --config experiment.json
.venv/bin/autor run runs/custom --steps 1
.venv/bin/autor run runs/custom
```

配置结构可参考合成 demo 生成的 run.json 中的 config。editable_files 是精确路径白名单；文件必须已存在于 seed，不支持符号链接。`--steps 0` 只完成已有 ready 候选的评测，不生成新提案。

## Harbor 0.24.0 路径

需要 Docker daemon 和可用模型凭据；下列参数由使用者明确填写，不默认选择模型或浮动版本。

```bash
.venv/bin/autor init-harbor runs/harbor-smoke \
  --outer-model '<可用的 Codex 模型 ID>' \
  --inner-model '<Harbor 支持的模型名称>' \
  --inner-version '<固定的 @openai/codex npm 版本>' \
  --task extract-elf
uv run --python 3.12 --with harbor==0.24.0 autor doctor --run runs/harbor-smoke
uv run --python 3.12 --with harbor==0.24.0 autor run runs/harbor-smoke
```

预设使用 Terminal-Bench 2.0 的明确任务名，可重复 --task。默认 baseline 和一个后代，累计外部调用上限 900 秒。已有契约测试不等同于真实 Docker 验收。

## BOS / command 同步协议

配置中的 evaluator 可以接外部 wrapper：

```json
{
  "kind": "command",
  "argv": ["/path/to/bos-wrapper", "--harness", "{harness}", "--result", "{result}", "--work", "{work}"]
}
```

程序按参数数组启动，不经过 shell。wrapper 成功时退出码为 0，并将 JSON 写到 result 路径：

```json
{
  "status": "ok",
  "tasks": [{"task_id": "task-a", "attempt": 0, "score": 0.75, "feedback": "具体观察", "task_checksum": "固定内容摘要"}]
}
```

控制器重新计算平均分，并要求任务覆盖/内容与 baseline 一致。远端 submit/poll/collect/cancel 由 wrapper 负责；outer 当前只提供同步接口。

## 开发验证

```bash
python3 -m unittest discover -v
uv run --python 3.12 --with harbor==0.24.0 python -m unittest discover -v
```

公开的 [验收摘要](evidence/v0.1.0.json) 去除了本机路径和原始会话日志，保留原始凭证摘要以便与本地冻结交付核对。它不是原始完整执行轨迹。
