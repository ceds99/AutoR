# 当前运行与交付手册

当前开发版：0.2.0.dev1。冻结 v0.1.0 包用于重现历史实验；升级源码后请创建新实验，旧实验需使用匹配构建恢复。

AutoR 是固定的 outer：接收 inner seed，调用 proposer，校验文件修改，测量候选，保存血缘并导出。MA 不在本版运行链路内。首版真实预设只允许改 `prompt.md`，不修改模型、评测器或预算。

## 环境与安装

| 路径 | 必需条件 | 本次验收范围 |
|---|---|---|
| 核心与合成演示 | Python 3.9+，macOS/Linux | macOS 两个独立 Python 3.12 虚拟环境 |
| 本地真实 HumanEval | macOS、sandbox-exec、系统 Python、可用且已认证的 Codex CLI | 真实模型调用、原始测试、导出迁移后重新调用 |
| Harbor | Python 3.12+、Harbor 0.24.0、Docker daemon、模型凭据 | 四题真实 Docker baseline 全通过；ARM＋Rosetta，固定短预算，未执行保留集 |
| BOS 资源预检 | Python 3.9+、明确服务 URL、内网连通 | 只读 HTTP；本地契约测试通过，真实入口连接超时 |
| 自定义 BOS / command | 另行实现候选发布与同步 JSON wrapper | 通用协议可用，BOS evaluator 尚未实现 |

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

## 有界 beam 搜索

```bash
.venv/bin/autor demo runs/beam --strategy beam --beam-width 2 --children-per-parent 2 --proposals 6
.venv/bin/autor status runs/beam
python3 scripts/search_smoke.py runs/search-comparison
```

第一条使用普通字符串 demo，会出现重复提示被拒绝，拒绝仍消耗提案名额。对照脚本用专门的合成树分别跑 hill_climb、branching、beam，各 6 次提案和 7 次有效评测，并在一代中途退出再恢复。两者均不调用模型。对照结果在 `comparison.json`，各实验完整凭证和分代保留/淘汰记录在子目录中。

真实后端通过 `init --seed ... --config ...` 采用相同策略接口。初始化前在完整配置中设置以下字段（这是局部示例，不是完整配置）：

```json
{
  "strategy": "beam",
  "search": {"beam_width": 2, "children_per_parent": 2},
  "budget": {
    "max_proposals": 6, "max_evaluations": 7, "max_seconds": 600,
    "proposal_timeout": 120, "evaluation_timeout": 220
  }
}
```

宽度和每父代名额接受 1–8 的整数。次数上限包含失败调用；评测上限含 baseline；600 秒是累计调用时间上限，不保证跑完全部候选。`init-harbor --strategy beam` 仍保留单轮 smoke 的原预算，不会自动扩大真实调用次数；要扩展需在新的完整配置中明确设置预算。

`run --steps 1` 会完成一个新提案及其可用评测，然后暂停；再次 `run` 继续剩余名额。一代未完成时不做筛选。全部孩子失败时停止原因为 `search_exhausted`，已有最佳候选仍可导出。模型/运行错误仍以退出码 1 报告，不能因保留了 best 就忽略错误。

## Proposer 访问检查

真实 proposer 需要支持 permission profiles 和 `codex sandbox` 的 CLI；当前实测为 0.153.4。每次提案前会自动检查文件与命令网络权限，失败时不发模型请求。macOS 需要 CommandLineTools 的系统 Python；Linux 需要 `/usr/bin/python3` 和可用的 Codex 沙箱。不要删除检查或改回宽松权限来绕过失败。

成功探测写入 `candidates/<id>/proposal/isolation.json`。该文件记录正常输入可读，以及目录外读取、符号链接、写入、命令网络被拒绝。父代和反馈只从控制器暂存的目录读取；不要把保留集、答案文件或凭据复制进去。对模型服务的认证流量不受命令网络禁用影响。

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

源码或 skill 改变后应建立新实验，包括从 0.2.0.dev0 升级到 dev1。旧实验保留为历史证据；恢复需要匹配其 outer 的构建。复制实验目录不等同于迁移实验，因为冻结的 command 配置可能包含旧绝对路径；需要迁移的是正式导出 bundle。

## 重跑发布验收

安装 uv 后，在仓库根目录构建 wheel，再运行：

```bash
uv build --wheel
python3 scripts/accept_release.py \
  --wheel dist/autor_outer-0.2.0.dev1-py3-none-any.whl \
  --demo-strategy beam --output runs/acceptance-new
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

### baseline pilot 与任务筛选

这条入口不调用 outer，只用未修改的 inner 测量搜索任务。先由评测维护者在搜索环境之外固定两道保留任务清单，加入随机 salt/nonce，计算该文件原始字节的 SHA-256；只把摘要交给准备命令，勿传入清单、题名或评测结果。

```bash
python3 scripts/terminal_bench_pilot.py prepare runs/tb-pilot \
  --inner-model '<可用的 Harbor 模型名称>' \
  --inner-version '<固定 Codex npm 版本，例如 0.153.4>' \
  --holdout-commitment '<评测维护者提供的 64 位 SHA-256>'
uv run --python 3.12 --with harbor==0.24.0 \
  python scripts/terminal_bench_pilot.py check runs/tb-pilot
uv run --python 3.12 --with harbor==0.24.0 \
  python scripts/terminal_bench_pilot.py run runs/tb-pilot
```

Harbor 的 inner 认证不同于宿主机 Codex 自动登录：默认使用 OPENAI_API_KEY；若明确使用已有 ChatGPT 登录文件，可在执行环境设置 `CODEX_FORCE_AUTH_JSON=1`，或用 `CODEX_AUTH_JSON_PATH` 指向该环境的登录文件。这些选项已从 Harbor 0.24.0 的 Codex adapter 核实，并在真实容器运行中使用。预检只检查文件/环境变量是否存在，不读取或打印凭据值；不要把凭据提交到仓库。

准备与 check 不调用模型；默认 run 最多串行执行原四道任务，预算总计 1200 秒，每题 agent 上限 180 秒。新实验可重复传入 `--task`，显式选择 1–6 个唯一任务名，并用 `--agent-timeout`、`--max-seconds` 设置正数时间上限。它冻结任务顺序、job、prompt 和运行时摘要，拒绝变更或自动重放已有 started.json。启动后中断时，需要检查 Docker 是否还有残留作业；本地进程取消不等于 daemon 内容器一定已清理。

任务筛选示例（新目录；这不是 A/B/C 对照）：

```bash
python3 scripts/terminal_bench_pilot.py prepare runs/tb-selection \
  --inner-model openai/gpt-6-astra --inner-version 0.153.4 \
  --holdout-commitment '<独立清单的 SHA-256>' \
  --task db-wal-recovery --task regex-log \
  --task multi-source-data-merger --task git-multibranch \
  --agent-timeout 300 --max-seconds 1800 \
  --bounded-apt --node-runtime '/path/to/prepared-linux-runtime'
```

完成后查看 baseline-result.json 与 jobs/evaluation 下的逐题日志。任务异常或缺失会生成 failure.json，不给整轮伪造零分。先判断真实失败是否可分析，再创建 inner 比较实验；当前脚本不会自动启动搜索，也不会访问保留任务。镜像架构、npm 版本和模型可用性需要环境就绪后的 smoke 验证。

### 当前本地 Lima 环境

本机已在用户目录安装独立运行环境：Lima 2.2.1、Docker CLI 29.8.2、Docker daemon 29.9.1、Compose 5.6.0。VM 使用 VZ＋Rosetta，4 CPU、6 GiB 内存、48 GiB 磁盘上限。下载与 VM 文件位于 `~/.local/share/autor-runtime`；没有修改全局 Docker context 或 shell 启动文件。

```bash
source "$HOME/.local/share/autor-runtime/runtime.env"
limactl start autor-tb
docker info
# 完成实验后释放 VM 内存：
limactl stop autor-tb
```

本轮结束后已确认无残留容器并停止 VM，重新运行前先启动。共享目录仅限本次 pilot 工作区。内层容器不挂载宿主用户目录、仓库根目录或保留任务目录。上面 env 文件是这台机器的本地配置，不随公开仓库分发；另一台机器应使用自己的 Docker 环境。

### 有界依赖安装与固定 Node 运行时

第一次实际启动在 Debian 依赖安装阶段超时，未进入模型执行。为控制冷启动耗时，pilot 支持显式预置官方 Linux Node 发行目录；整个目录（含内部符号链接）计算摘要并只读挂载。准备前验证官方压缩包校验和；本机使用 Node v22.23.3 Linux x64、npm 10.9.9。

```bash
python3 scripts/terminal_bench_pilot.py prepare runs/tb-new \
  --inner-model openai/gpt-6-astra --inner-version 0.153.4 \
  --holdout-commitment '<独立清单的 SHA-256>' \
  --bounded-apt --node-runtime '/path/to/node-v22.23.3-linux-x64'
```

`--bounded-apt` 固定 APT HTTP/HTTPS 超时为 15 秒、重试为 0。可加 `--apt-proxy '<容器可达的 HTTP(S) 代理 origin>'`，代理不得含账号、密码或查询参数。Node、APT 配置和 shell 启动文件均只读挂载并核对摘要；变更需新建实验。Harbor 按原逻辑安装缺失依赖；不改任务说明、模型、评分器或答案。容器用 `BASH_ENV` 引入 Node，避免设置 `PATH` 影响 Harbor 宿主侧 Docker 命令。共享 VM 时，新 pilot 目录及运行时必须位于明确挂载的目录内。

为避免重复在线安装，可以在这个发行目录中预置相同版本的 Linux `bin/codex`、`bin/rg` 和所需运行工具。Harbor 0.24.0 会检查 `codex --version`，已符合要求时跳过安装。必须记录来源、校验和、实际版本和完整 runtime digest；不能把不同版本程序标为同一 harness。

本次筛选使用了官方 npm 发布的 Codex 0.153.4 Linux x64 包（校验 SHA-512），Node 22.23.3，以及已有固定任务镜像中的系统 curl 8.5.0 和其共享库。只复制系统工具，不复制任务数据、参考解或测试。curl 使用私有 loader 路径，不替换容器系统库。运行时可包含 `ca-certificates.crt` 公共信任证书包；profile 会设置 `SSL_CERT_FILE`，证书字节计入摘要，TLS 校验保持开启。这是本地 pilot 的预置运行环境，不是新的发布产物；跨机器重建须核对本地凭证中的全部摘要。

若模型服务也需要代理，使用 `--network-proxy '<容器可达的代理 origin>'`（要求提供 `--node-runtime`）。代理只写入容器内 profile，不注入宿主 Docker 命令环境；HTTP/HTTPS 均设置，localhost、127.0.0.1 和 ::1 绕过代理，避免破坏任务内本地服务访问。APT 与模型网络分别诊断；可用未认证的服务探测验证网络，但 HTTP 响应不代表认证和真实模型调用已成功。

这是记录在案的 pilot 运行条件，不等同于官方排行榜环境。每次启动应保留镜像 digest、架构、CLI 实际版本和依赖安装日志。失败运行保留原目录，不自动重放不确定调用。故障归因后需要补测时，先确认旧进程和容器已停止，再显式创建新配置和新目录，说明改变的条件；已发出但未收到响应的模型请求应记录费用未知，不宣称零调用或零成本。

2026-10-16 筛选实测：另选的四道主任务和两道备选均为 1.0，选题门槛未满足；未启动 A/B/C。详见 [筛选凭证](evidence/task-selection.json)，包含有效成绩、失败尝试、运行时/镜像摘要与预算。安装失败不算任务失败；中断模型请求的费用未知。完整原始日志和预置二进制只保存在本地。

准备镜像时如果宿主可联网、Docker daemon 拉取却超时，应单独检查 daemon 的网络配置。修改专用 VM 的 daemon 代理前先确认没有运行中的试验并备份原配置；这与容器内模型代理是两层配置。结束实验后核对残留容器，清理本次创建的容器并停止专用 VM。

### 原有单轮 outer + Harbor 入口

需要 Docker daemon 和可用模型凭据；下列参数由使用者明确填写，不默认选择模型或浮动版本。

```bash
.venv/bin/autor init-harbor runs/harbor-smoke \
  --outer-model '<可用的 Codex 模型 ID>' \
  --inner-model '<Harbor 支持的模型名称>' \
  --inner-version '<固定的 @openai/codex npm 版本>' \
  --task fix-git
uv run --python 3.12 --with harbor==0.24.0 autor doctor --run runs/harbor-smoke
uv run --python 3.12 --with harbor==0.24.0 autor run runs/harbor-smoke
```

预设使用 Terminal-Bench 2.0 的明确任务名，可重复 --task。默认 baseline 和一个后代，累计外部调用上限 900 秒。四题 pilot 已实测通过；该单轮入口仍使用自己的固定预算，不能直接替代 pilot 配置。

## BOS 接入预检

用户提供的 `bos-exec` 包说明更新于 2026-10-14。完整附件保留在本地；不将团队内部资料和私有服务地址复制进公开仓库。优先使用目标部署的现有 CaseVersion、Profile 和 Release，不改平台 execution-defaults。

在仓库根目录运行，服务 URL 显式来自当前部署配置：

```bash
python3 scripts/bos_preflight.py runs/bos-preflight-001 \
  --base-url "$BOS_BASE_URL" --timeout 5 --budget 45
```

脚本直连 BOS，不改变全局代理；只发送 GET，记录健康检查、版本、OpenAPI、默认 Profile/Harness 及 AgentRelease 候选。首次不自动选择行为包，也不枚举 Case，避免把保留任务清单带进搜索上下文。后续可以加 `--agent-release-ref agent_releases/<id>` 与至多六个 `--case-version-ref case_versions/<id>`，仅检查明确的搜索任务。

输出目录必须全新，包含 input.json、原始 response-NNN.json、requests.json 和 report.json。`status=discovered` 只表示完成资源读取，`evaluation_ready` 始终为 false；非就绪资源和后续接入缺口仍在 `missing` 中。连接/响应错误返回退出码 2，成功读取返回 0。默认单次 socket 超时 5 秒，发现阶段在请求之间检查 45 秒预算；这不是独立进程的硬墙钟截止器。

原始响应可能含私有 Profile 元数据，留在受忽略的 runs/，不要全部复制到 proposer 输入或公开仓库。网络恢复后应新建预检目录，保留已有失败证据。

2026-10-15 已分别尝试附件原始客户端、直连 HTTP、默认代理路径与 TCP 端口检查，均未连通文档入口；尚未获得 OpenAPI、Run/Trial 或分数。原文明确该入口依赖公司内网/VPN、端口转发由服务端维护。本轮无法区分路由、防火墙或服务监听问题，不能判断为认证失败。恢复访问后先重做预检，再落实候选包发布合同，见 [当前接入方案](design.md#下一阶段bos-接入搜索质量与工程化) 和 [凭证](evidence/bos-integration.json)。

## BOS / command 同步协议（wrapper 待实现）

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


## Adaptive beam 与有门禁的三组对照

合成机制检查不调用模型：

```bash
python3 scripts/adaptive_search_smoke.py runs/adaptive-check
python3 -m autor demo runs/adaptive-demo --strategy adaptive_beam --mixing
```

A/B/C 合成检查使用相同预算上限；脚本指定分数，只验证调度、复测、混合与恢复，不证明算法效果。原 beam 仍可直接运行。

真实对照使用 `scripts/compare_search.py`：先为选中的任务创建全新的冻结 pilot；提供其公开 instruction 映射 JSON、匹配的镜像回执和经过分析的自然失败 trial 目录。`prepare --help` 列出所需参数，随后按 `baselines <root>`、`search <root>` 分阶段运行。这些调用需要 Harbor 0.24.0、同一 Python 运行环境、专用 Docker VM 和认证；所有输出目录必须位于 VM 允许挂载的路径内。

为对照建立固定位置的 Python 3.12 venv 并安装 `harbor==0.24.0`，保存依赖版本清单。用该 venv 的 Python 执行 prepare、baselines 和 search，并将其 bin 放到 PATH 前端。prepare 会冻结 Python 的绝对路径，因此不要使用可能被清理的临时 `uv run --with` 环境。评测桥会分别检查 Harbor CLI 和 Python 包版本；outer 的 Codex 路径、版本与 inner 的版本分别固定。

每组最多 6 次提案、8 次完整任务集评测、2400 秒累计调用时间，复测与失败计入同一上限；记录实际用量。40 分钟上限在正式对照冻结前，根据自然失败试验的耗时及缓存预检确定，不在运行中追加。A 使用 beam；B 使用 adaptive_beam；C 开启 mixing。baseline 中任一组中断、测量无效或未复现失败时，立即停止启动后续组，`search` 拒绝启动。若时间预算提前耗尽，报告未完成阶段，不能声称混合或效果已经得到验证。

JSON 配置使用 `strategy: "adaptive_beam"`，`search` 支持 `beam_width`、`children_per_parent`、`confirmation_samples`（2–4）和 `mixing`（布尔值）。复测中断后继续用 `autor run`；程序只接纳已落盘的独立结果，不重放不确定调用。`needs_confirmation` 表示预算耗尽前尚未完成确认，不能当作最佳候选。原始样本在 `measurements/`，`evaluation.json` 是派生的逐题平均值。

本地 Harbor wrapper 使用命令评测器的 `{timeout}` 占位符接收实际剩余时间，保留 30 秒取消空间。强制终止或主机崩溃后仍须核对 Docker 残留；取消本地进程并不保证容器已经停止。重启前不要改变冻结源码、运行时或配置；有修复时新建实验，保留旧账本。该本地 wrapper 的外部 runtime、镜像和指令文件目前不随通用导出包迁移。

评分依赖下载过慢时，pilot 支持 `--dependency-cache /path/to/audited-uv-cache`（同时指定 `--node-runtime`）。仅允许使用审计过的公开第三方 wheel 缓存；当前准备流程保留 `archive-v0`、`wheels-v5`、`simple-v18`，核对 wheel RECORD，剔除任务、测试、环境实例和凭证。缓存摘要进入 pilot 契约，宿主挂载只读；每个容器启动时复制到自己的 `/tmp/autor-uv-cache`。后续任务和候选不会继承前一容器的写入。启用缓存属于运行环境变更，须新建实验并重新测量全部 baseline。

依赖预热可用 `nop` agent，不调用模型；其分数不得作为 baseline 或自然失败证据。若需在完成后提取缓存，Docker 环境要显式设置 `environment.kwargs.keep_containers: true`。Harbor 0.24.0 的 `environment.delete: false` 仍会移除容器，只保留部分其他资源，不能用来保留容器缓存。提取审计后只清理本次预热的容器。

任务来源可通过 `--task-git-url`、`--task-revision`、`--task-prefix`、`--task-name-prefix`、`--dataset-version` 固定。revision 必须是完整 commit；目录前缀与 task.toml 中的任务名空间分别记录。旧对照来自 Terminal-Bench 2.0。新筛选单独固定 Terminal-Bench 4.0 的 `452bf305c6daa62fc59061d22133a7cbc7c1572e`，目录前缀为 `tasks/`、任务名空间为 `terminal-bench/`。版本之间不拼接 baseline。详见 [Terminal-Bench 4.0 官方更新](https://www.tbench.ai/news/terminal-bench-4-0)。

ARM 宿主使用已有 x86 inner 运行时，可通过 `--docker-platform linux/amd64` 冻结 agent Compose overlay；需安装支持平台选择的 Docker Buildx，并检查实际镜像架构。独立 verifier 保持其原生架构，必须另记镜像回执；同一对照各组固定相同运行条件。对每次重新构建的镜像，回执还应包含 `project_suffix` 和 `content_sha256`；wrapper 在接纳分数前比较实际镜像文件层及配置，仅忽略随 trial 改变的 Compose project 标签。该选项不会修改任务 Dockerfile、评分器或任务时间限制；pilot 的 agent 上限仍须显式记录。

筛选时可加 `--keep-images` 保留构建镜像以制作回执；容器仍由 Compose down 移除。正式 wrapper 自动采用该清理方式，验收分数前核对实际构建内容。若筛选镜像已被清理，可用同源 `nop` 预热重新取得回执；预热不调用模型，其分数不得作为任务筛选或策略比较结果。
