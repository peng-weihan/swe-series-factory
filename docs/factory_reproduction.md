# DeepSWE / ProgramBench 第一版构造器

本实现依据本目录只读规格，复现构造工艺与单题验收，不宣称还原官方制作全过程、历史模型输出或原平台内部协议。原始源码和模板未随三份规格提供，因此新增 prompts 是依规格重写的模板。

## 代码与运行层

- `swe_factory/`：固定 commit 的独立源码快照、二进制 Git diff、所有权检查、真实 JUnit 报告、宿主/Docker 执行、CLI Agent 协议。
- `deepswe_factory/`：三条构造路线、共享修复预算、六项矩阵、任务打包、独立 grader。
- `programbench_factory/`：CLI 构建、重复字节探测、测试展平与 QC、R2 重建、可选 JSON-action 工具循环。

继续使用 Python >=3.11 和现有 `.venv`。主体不新增第三方 Python 依赖；示例和 pytest 项目需要 pytest，ProgramBench 示例需要 `cc`。没有自动安装依赖、拉取镜像或读取旧凭据的步骤。

`Runner` 默认使用 Docker，每次执行复制到独立新容器的 `/factory-workspace`，断网并设置内存、CPU、PID 限制，支持 Docker CLI 的远端 daemon。依赖、Git、Python、pytest、`/bin/sleep` 和相应编译工具须预先存在于指定镜像。记录实际 image ID。容器通过 `docker cp` 传输文件，不依赖主机 bind mount。只清理本次创建的具名容器，不清理镜像或共享缓存。

`backend="host"` 是显式选择的非隔离模式，供可信人工示例/本地验证使用。工具型外部 CLI 的文件和 shell 权限由该 CLI/部署环境提供；controller 的 diff 检查不是它的沙箱。JSON-action 引擎强制 Docker。所有输出使用新目录，失败证据保留，串行处理候选；不提供多控制器协调或自动续跑。

## 已跑通的示例

```bash
.venv/bin/python -m deepswe_factory demo --route all --output runs/deepswe-demo-next
.venv/bin/python -m programbench_factory demo --output runs/programbench-demo-next
```

DeepSWE 示例使用人工映射合并功能：A 消融，C 测试先行，B 从参考优先级机制演化多层合并。每条路线都执行真实 pytest、patch 回放与打包后的独立 grader。预期 Nop=0 / Oracle=1，F2P/P2P 均非空。

ProgramBench 示例实际编译一个 C 字节复制 CLI；包括二进制/Unicode/stdin/错误输出，13 种 probe 各重复两次。12 个测试中 11 个应保留，一个只检查成功退出码的弱测试应归类为 `dummy_pass`。生成器是固定 fixture，不调用模型；示例 Dockerfile 刻意标记为未验证，不能作为 R2 产物发布。

两种 demo 的 `summary.json` 都记录 `model_called=false`、`docker_executed=false`。DeepSWE 最多声明本地 L2（`accepted=false`），ProgramBench 最多声明本地 R1（`published=false`）。

本次实测：173 项测试通过，0 failure/error/skip，包含原 NL2Repo 的 148 项测试；验证记录为 `runs/factories-verification-01/verification.json`。三份原始规格的 SHA256 与开工前一致，权限仍为只读。后续可用以下命令留存新的验收记录：

```bash
.venv/bin/python scripts/verify_factories.py --output runs/factories-verification-next
```

## 接入真实模型与仓库

复制 `examples/deepswe.toml` 或 `examples/programbench.toml` 为本地配置，填入真实的已准备镜像与工具型 Agent CLI。候选格式参考相应 `*-repositories.jsonl`；其中域名、SHA 和路径是占位符，不能直接当真实输入。

```bash
.venv/bin/python -m deepswe_factory validate-config --config examples/deepswe.local.toml
.venv/bin/python -m deepswe_factory run \
  --config examples/deepswe.local.toml --repo-jsonl examples/deepswe.local.jsonl \
  --output runs/deepswe-pilot-001 --limit 1

.venv/bin/python -m programbench_factory validate-config --config examples/programbench.local.toml
.venv/bin/python -m programbench_factory run \
  --config examples/programbench.local.toml --repo-jsonl examples/programbench.local.jsonl \
  --output runs/programbench-pilot-001 --limit 1
```

`validate-config` 只验证配置结构，不探测镜像、服务或模型。`run` 会取得源码、调用模型、执行构建/测试；ProgramBench 在 Docker 模式且 `rebuild=true` 时会构建独立交付镜像。选择资源预算、服务和输出位置后再启动真实运行。

候选必须提供完整 40 位 SHA 和 `url` 或 `local_path`。本地 Git 仓库也是独立 clone 后 checkout，不修改原仓库。JSONL 中相对路径基于 JSONL 所在目录解析。现有输出不能被原地覆盖；改变配置/题意时使用新目录。

### Agent 协议

延用 NL2Repo 的 `CommandAgent`：请求 JSON 和完整 prompt 写入 `roles/<attempt>/agent/`；通过 stdin、`{request}` / `{response}` / `{workspace}` / `{role}` 参数占位和 `NL2REPO_*` 环境变量交接。这些环境变量为兼容原接口而保留名称。CLI 在工作区循环使用工具，最终写 response JSON。非零退出、超时、非法 JSON 均不能以自称成功通过。

DeepSWE 角色可直接编辑工作区，或返回 `patch` 字符串（二选一）。controller 从真实快照生成最终补丁，检查生产/测试路径所有权。只读角色不得改仓库。通用 JSON 字段为 `status=ok/complete/rejected`；各角色字段见 `deepswe_factory/prompts/`。

ProgramBench 默认使用相同 CLI 接口；也可去掉 `[agent]`、使用示例中的 `[model]`，接入规格描述的 `/chat/completions` 消息形状和 `bash/write/done` 循环。API key 只从配置指定的环境变量读取，不写进配置快照。动作/请求/响应、工具完整输出保留，模型上下文只收到末尾 4000 字符。默认 80 步、每次 4096 tokens、工具 150 秒；工具超时会终止该构造容器，避免遗留执行污染后续步骤。

### DeepSWE 输入与路线

共同字段：`production_paths` 和 `test_paths` 声明互不重叠的所有权目录/文件；`route` 为 `ablation`、`query` 或 `specialize`。公开接口、文档或配置需要被答案修改时，显式加入 production_paths。符号链接快照暂不支持，避免隐式外部输入。

A：分析已有功能 → 消融生产实现并整文件隐藏选定测试 → controller 从 E/F 求逆并拆分 S/T → 实测红绿 → 导出包再跑 Nop/Oracle。第一版支持 existing 测试模式。

C：分析/提出一条 query → 测试作者 → 真实旧行为失败 → 只读 critic → 实现作者 → C1–C6 矩阵 → 导出验证。query/testcase/solution 修复共享预算；只有 query 修订增加版本，重做其下游；solution 修复复用已冻结 query/tests。原生基线故障直接记录失败，不以改题掩盖。测试命令是 argv；pytest 自动添加稳定 rootdir/JUnit 配置，其他 runner 必须显式使用 `{report}` 产出兼容 JUnit。

B：额外提供 `reference_dir`（有 manifest.json 的参考集合）、`reference_task_id`。要求参考题完整，manifest 的 task_id/ext_id 唯一且非空，参考 metadata/config 中 base_commit 与输入完全匹配。先应用原 S/T、补充独立测试文件、mask、验证 Base；Base 通过后，从原问题态加原 T 的 scaffold 规划和实现新功能，再 mask 求逆、重写题面、验证。新增测试不得修改原测试；结构化 requirements 在改写前后须一致，但语义等价仍待独立审阅。

六项矩阵分别保存真实报告：base 回归、base+T 目标、base+S build、base+S 回归、base+S+T 目标、base+S+T 回归。F2P 仅接受明确 `failed→passed`；P2P 要求非空且两态 passed。要求各态回归 ID 集合相同。missing/skipped/error 不计通过，重复 ID 取较差状态，collection error/无报告/超时拒绝。

导出的 `environment/problem/` 仅包含问题态快照；`solution/` 与 `tests/` 分开。grader 在全新 E 上检查答案只能修改生产路径，然后注入可信 T，执行 build/target/regression 并解析原生报告。任务包不依赖安装本项目即可验证：

```bash
.venv/bin/python -m deepswe_factory verify \
  --config examples/host-verification.toml \
  --task /absolute/path/to/task --output runs/deepswe-replay-next
```

这会回放包内 Nop/Oracle，不调用 Agent。`start.sh`/`pre_artifacts.sh` 提供候选提交捕获脚本，尚未在目标 Harbor 生命周期中验证；包内 selfcheck 不把 L2 提升为 L3。

### ProgramBench 构造与交付

固定仓库 → builder 提交 build.sh、probe 列表、测试、Dockerfile 和元数据 → controller 在冻结源码上重新执行 build.sh → 重复字节探测 → 展平/映射测试 → 新工作区 dummy/gold QC → 再跑 gold → 生成 instance。

builder 必须交付的字段与目录见 `programbench_factory/prompts/builder.md`。必须声明许可证依据、公开文件、source_globs、核心功能分组，以及逐测试到 probe ID 的关联；这些是可追溯证据，仍不能自动证明语义覆盖完整。

branch ID 与 dotted test ID 遵循规格；所有权/报告以显式 native ID 映射判断。只有 gold passed 且 dummy 真正 failed 的测试有效，至少 8 个，并须有核心功能 branch。skip/collection failure 不会当 pass。`tests.json` 保留 ignored 项及原因；`eval/run.sh` 按规格运行全部测试并保留报告，因此脚本退出码不是得分。task.yaml 采用 JSON 语法（合法 YAML 1.2）。

Docker R2：检查 recipe 中完整 SHA、基础镜像 digest 和 stash 路径 → 独立构建 → 核对 `/opt/programbench-source-commit` 中记录的实际 checkout SHA → 从 stash 提取新 gold → 重跑有效集合的 dummy/gold 和 gold-repeat → 检查 runtime 文件系统中的原源码/隐藏测试内容摘要、Git 历史路径与 gold 文件权限。runtime 必须声明非 root 数字 UID，两个 gold 文件须为 root 所有、111 权限；构造和 verifier 在独立容器中以 root 执行。镜像中须有用于验证的 Python/pytest。可以在后续有 Docker 时回放已有构造证据：

```bash
.venv/bin/python -m programbench_factory rebuild \
  --config examples/programbench.local.toml \
  --construction /absolute/path/to/saved/construction --output runs/programbench-rebuild-next
```

此命令需要真实且固定版本的 Dockerfile；人工 host demo 的占位 recipe 会被拒绝。`run` 的全部 R2 检查通过后，原子发布当前 run 的 `published/<instance_id>`，记录 `status=published, level=R2, r3_verified=false`；独立 `rebuild` 只保存验证证据，不改写旧产物。文件系统检查覆盖精确内容摘要、Git 路径和声明权限，不证明消除了所有源码变体/历史镜像层，也不替代 R3 的防包装/复制验收。

## 与规格的边界

| 范围 | 第一版行为 |
| --- | --- |
| DeepSWE A | existing 模式；尚未做 generate/reuse-original 的独立模式和批量候选排序 |
| DeepSWE B | 单参考 Base+一次演化；追加独立测试文件的更严格所有权策略，未实现独立阶段自动修复/缓存恢复 |
| DeepSWE C | 单 query 和共享修复预算；分析采用 Agent 证据，不含 call-graph/hybrid、大规模 taxonomy seed 调度 |
| 原生报告 | pytest 已真实验证；其他语言需提供兼容 JUnit 并单独验证，未实现 CTRF/Go JSON/Rust 专用 parser |
| Harbor | 自有 standalone grader 与任务布局，不假定与缺失的定制 Harbor/vanilla reference 等价 |
| ProgramBench | 保留原始 ID、QC、难度公式；子命令提取采用明确的缩进启发式，未声称原始未知 regex 逐字等价 |
| ProgramBench R2 | 已实现独立构建、有效测试回放、内容/权限门禁和发布；真实 Docker 运行仍待验证 |
| 发布与质量 | 未做真实求解难度、near-miss/mutation、全面泄漏扫描、语义公平性、稳定性三重复、数据集拆分去重 |
| 批量 | 串行、失败归档、重复输入不覆盖；未实现分布式 claim、自动 resume 和历史批次恢复 |

`run_config.json`、`input.json`、`stages.json`、角色轨迹、源码/任务摘要、原始测试报告和分级 summary 一起保留。只根据 summary 中的实际阶段统计，不能用产物文件存在或 CLI 退出 0 代替验证。
