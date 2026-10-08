# ProgramBench 新题工厂复现规约

> 目的：仅凭本文和本目录的参考材料，重新实现一条“开源仓库 → 可交付 ProgramBench instance”的构造流水线。
>
> 本文是逆向整理的实现规格与操作手册，不是源码讲解。分析日期：2026-09-16；证据范围仅为本目录。没有执行构造、联网、安装依赖或构建镜像。示例命令是后续获得授权后的操作说明，不代表已经验证可运行。

## 0. 复现什么，以及不复现什么

### 0.1 目标任务

选择一个能构建为独立可执行文件的开源工具，把其某个固定版本变成一道“行为重建”题：

1. 构造方取得源码，构建参考程序，探测其行为，生成黑盒测试。
2. 解题方只得到许可暴露的使用材料与可运行的参考程序，通过交互观察行为，从零实现替代程序。
3. 评测方用隐藏测试运行替代程序，检查是否满足已观察到的行为。

这不是修复已有项目，也不是格式转换。**参考程序本身是可交互的行为规范；隐藏测试是有限的验收样本。**

解题方允许“运行 gold”，不等于允许读取、复制或包装 gold 作为答案。这一区分决定后续环境隔离方式。

### 0.2 复现基线

本目录存在多代构造路径。本文选取信息最完整的 **Kimi 单实例构造路径** 作为基线，固定以下边界：

- 输入：一个固定 commit 的 CLI 项目。
- 生成：模型通过 `bash / write / done` 三种动作完成构建、探测和测试编写。
- 验收：参考程序检查，以及 dummy/gold 双重测试过滤。
- 输出：ProgramBench 风格的 instance 目录与 Dockerfile recipe。

Codex/Claude shell builder 是替代执行引擎，不是必须串行经过的前置步骤。集群调度、dashboard、能力标注和 Harbor 适配均不属于单实例最小闭环。

### 0.3 三个复现层级

| 层级 | 完成定义 | 本副本能提供什么 |
| --- | --- | --- |
| R1：工厂产物复现 | 从一个候选生成符合目录契约的题目，执行构造期 dummy/gold QC | 有核心实现，可据此重建；环境和模型服务需另行准备 |
| R2：交付复现 | 在独立环境中重建参考程序，并让有效测试全部通过 | 有 Dockerfile 样例，但缺少统一的交付验收入口；本文补充流程 |
| R3：解题评测复现 | 解题代理从 cleanroom 起步，提交替代实现，隐藏评测并计分 | 本副本缺少 runtime、提交接口和计分实现，不能声称已还原 |

第一轮目标是 R1 + R2。R3 只能定义接口边界，不能根据现有文件臆造官方启动命令和评分规则。

本文统一使用三个标记：

- **[原实现]**：从现有代码直接还原的行为。
- **[复现约定]**：为消除歧义或补齐可操作流程而明确选择的规则；不是声称原代码已有该功能。
- **[扩展]**：可提升质量，但不属于基线复现的机制。

“复现”指流程、产物契约和验收语义可重复，不承诺生成逐字相同的测试文件。模型采样、服务版本和工具链均可能影响产物；历史运行轨迹与完整模型快照未包含在本副本中。

## 1. 系统分工与数据流

```text
候选发现 → 仓库资格筛选 → 固定 commit
                              |
                         构建 gold
                              |
                    探测行为、记录 oracle
                              |
                     生成分支化黑盒测试
                              |
                   展平测试 + dummy/gold QC
                              |
                     生成 instance 文件
                              |
                    独立重建与交付验收
                              |
                       发布不可变版本
```

必须把生成者和裁判分开：模型负责探索和提出产物，控制程序负责版本检查、测试执行、计数与最终发布。

| 角色 | 可以看到的内容 | 负责交付 |
| --- | --- | --- |
| 构造代理 | 源码、文档、构建工具、gold、探测结果 | 构建方法、行为测试、初步题目文件 |
| 构造控制器 | 全部构造产物 | 测试映射、QC 结果、最终元数据 |
| 解题代理 | 使用文档、获准执行的 gold、公开输入资产 | 自己的源码和构建入口；具体接口待 runtime 确定 |
| 隐藏评测器 | 测试、fixture、替代程序、题目清单 | 测试结果；正式计分口径待 runtime 确定 |

## 2. 运行环境与目录契约

### 2.1 最小依赖

复现单实例需要：

- Python 3.10+，现有环境中可用的 `pytest`；建议有 `pytest-timeout`。
- Git；`gh` 可选，给定完整 commit 时无须依赖其版本解析。
- 与候选相符的编译工具链，例如 C 的编译器和 make、Go、Rust/Cargo。
- 一个兼容现有请求/响应形状的模型服务。
- 若执行 R2，则需要经过容量评估的镜像构建和隔离运行设施。

不要用本目录的 `uv sync` 或 `uv run` 作为“启动工厂”的默认步骤：这些命令可能安装与单实例生成无关的大量依赖。优先使用预先审核、已存在的环境。

### 2.2 存储布局

**[复现约定]** 参考材料只读；所有新运行写入独立目录。例如：

```text
/volume/pt-coder/users/mlei/factory_ref/pb/       # 只读参考材料
/volume/pt-coder/users/mlei/pb_repro/<run_id>/   # 获批后新建
  candidates.jsonl
  run_config.json
  state/                                     # 状态与拒绝原因
  evidence/<instance_id>/<attempt_id>/        # 日志、观测、QC、构建证据
  factory/instances/<instance_id>/            # 原引擎的候选输出
  scratch/
    work/<instance_id>/
    cache/{cargo,gomod,gobuild,go}/
  cache/                                     # 其他下载/工具缓存
  published/<instance_id>/                    # 通过 R2 后发布
```

原引擎映射：`PB_WT=<run_root>/factory`，`PB_TMP=<run_root>/scratch`。

本节点 ephemeral storage 只有 6 GiB。首次执行前必须检查 ephemeral 与持久卷可用空间，评估依赖、源码、编译、镜像和日志总量。安装依赖、拉取/构建镜像及大体积编译需明确授权。缓存、临时目录和构建输出必须落在持久卷，不能假定只设置 `TMPDIR` 就覆盖所有工具的写入。

不要套用原脚本中的全局 Docker 清理、跨任务进程终止或共享缓存删除操作。

### 2.3 应记录的运行配置

**[复现约定]** 将以下内容保存为 `run_config.json`，不要保存访问密钥：

```json
{
  "protocol": "pb-kimi-reconstruction-v1",
  "run_id": "<unique-run-id>",
  "engine_source_sha256": "<sha256-of-engine-file>",
  "prompt_sha256": "<sha256-of-rendered-system-prompt>",
  "model": "<served-model-id>",
  "temperature": 0.2,
  "max_tokens": 4096,
  "max_agent_steps": 80,
  "tool_timeout_seconds": 150,
  "suite_timeout_seconds": 300,
  "test_timeout_seconds": 20,
  "python_version": "<version>",
  "pytest_version": "<version>",
  "pytest_timeout_available": true,
  "toolchain_versions": {},
  "builder_image_digest": "<if-used>",
  "runtime_image_digest": "<if-used>"
}
```

完整环境应额外记录 CPU 架构、系统、动态链接依赖和构建相关环境变量。只记录 Python 包锁文件不足以固定整个构造环境。

## 3. 阶段 A：生成并冻结候选

### 3.1 候选记录

给构造器的最小记录：

```json
{"org":"Cyan4973","repo":"xxHash","commit":"e573d4d2aaeaba0f3e5a0a9a54144a1f2b4b56e7","language":"c","category":"compression-crypto"}
```

此例来自本地样例，只用于验证复现链路，不应被统计为新增题。正式扩集应换成排除名单和现有集合之外的项目。

**[复现约定]**：

- `org`、`repo` 保存展示用名称；去重键统一为小写 `org__repo`。
- `commit` 必须是已解析的完整 40 位十六进制 SHA，不能把 `HEAD` 直接交给构造阶段。
- `language` 统一为 `rs / go / c / cpp / other`；如使用旧筛选器的 `rust`，在边界转换为 `rs`。
- 分类与来源 topic 分开。`cli`、`terminal` 是发现标签，不自动视为最终功能分类。
- `difficulty` 不作为构建前的可信输入，最终由控制程序计算。

### 3.2 从哪里找候选

第一轮复现只需一条手选候选，无须先恢复整个采集系统。

批量扩展时可复现现有 GitHub 搜索策略：语言为 Rust、Go、C、C++；topic 为 `cli`、`command-line`、`terminal`、`tui`、`command-line-tool`、`console`；按 stars 区间 40–150、150–600、600–3000、3000–20000 搜索，排除 archived/fork，按 stars 降序取每页 100 项、最多 3 页。查询参数需正确 URL 编码。

搜索结果按统一键合并，并在两个位置执行排除：进入候选池时、发布 instance 前。排除内容包括 `exclusion.txt`、本次已选仓库和既有已发布仓库；fork/mirror 等同源关系还需额外判断，字符串去重不能替代同源判断。

模型建议的仓库也必须经过真实仓库存在性和版本验证，不能直接进入构建。

### 3.3 资格判定

| 条件 | 操作 | 不满足时 |
| --- | --- | --- |
| 有真正 CLI/TUI | 阅读 README、构建配置和入口文档 | `REJECT_NOT_CLI` |
| 能交付单一主可执行文件 | 明确具体 binary target；不能只是库 | `REJECT_NO_BINARY` |
| 核心功能可离线测试 | 设计不依赖远端服务的成功用例 | `REJECT_REQUIRES_NETWORK` |
| 许可证可接受 | 记录目标项目许可证文件和判定依据 | `REJECT_LICENSE` 或人工复核 |
| 版本可固定 | 解析 SHA，并能取得该版本 | `REJECT_UNRESOLVED_COMMIT` |
| 构建成本在预算内 | 初步检查依赖、源码规模和工具链 | 暂缓，不强行构建 |

这些失败标签是本文的补充状态协议，不是原代码已有的所有输出字符串。许可证关键字匹配只作为初筛，不代替许可条件核查。

复杂 TUI、协议工具和巨型项目不适合作为第一条复现样例。优先选择输入可控、输出确定的文本处理或摘要计算 CLI。

### 3.4 批量配额是独立策略

原 balanced 筛选器的候选目标总量为 1308，语言权重为 Rust 53%、Go 23%、C 16%、C++ 6%、其他 2%；类别目标为：

| 类别 | 目标 |
| --- | ---: |
| text-processing | 300 |
| dev-tools | 235 |
| tui | 145 |
| parser-compiler | 125 |
| file-system | 125 |
| network | 90 |
| converter | 90 |
| compression-crypto | 80 |
| data-db | 52 |
| scientific | 40 |
| misc-generative | 26 |

这是一个现有采样配置，不是 ProgramBench instance 格式要求。原算法先同时遵守语言/类别配额，再放松语言限制补类别。第一轮无须套用；批量时必须同时报告候选分布和验收后分布。

## 4. 阶段 B：构建并确立参考程序

### 4.1 身份与工作区

**[原实现]** `instance_id = org + "__" + repo + "." + commit[:7]`。

**[复现约定]** 完整 SHA 才是身份依据，短 SHA 仅用于目录名；目录名冲突必须报错。每次尝试使用独立 attempt，不能让两个进程同时写同一个 instance。

### 4.2 构建操作

1. 在该 attempt 的 scratch 中取得固定版本源码。
2. 核对 `git rev-parse HEAD` 与候选 SHA 完全一致。
3. 根据 README、CI 和构建配置选择真正的 CLI binary target。
4. 使用可记录、可重复的构建命令，保存工具链版本、环境及完整构建日志。
5. 将参考程序复制成约定名称 `executable`。
6. 从独立工作目录运行它，核查是否依赖未打包的资源、配置、动态库或构建路径。

语言级命令只是起点，不是通用保证：Rust 通常从 `cargo build` 开始；Go 从项目对应的 `go build` 目标开始；C/C++ 从 README 指定的 make/CMake target 开始。不要假设所有项目都能用同一个命令。

**[原实现]** Kimi 提示词偏好快速构建，避免 release/LTO，目标约 6 分钟；格式说明中的 release build 要求与此有差异。**本复现基线采用 Kimi 的快速构建策略，并要求 Dockerfile 与实际成功命令一致。**

约 6 分钟是提示目标，原控制器没有单独强制该阶段总时限。重建控制器时应把它设为明确阶段预算，并在需要延长时显式调整，而不是默默反复重试。

### 4.3 构建成功证据

**[复现约定]** 构建阶段输出：

```text
gold/executable
build.sh                 # 可重放的构建步骤，不只是自然语言描述
build.log
build_manifest.json      # 目标 SHA、实际 SHA、工具链、命令、binary SHA256
license_record.json
```

这些是构造侧证据，不全部属于最终 instance 格式。现有引擎并不自动生成全部这些文件，重建时需增加记录。

构建失败不能通过猜测输出变成高置信题。将失败尝试归档；如保留低置信产物，放在单独隔离区，不参与发布计数。

## 5. 阶段 C：把程序行为变成 oracle

### 5.1 探测单位

一个行为样本不是一句“运行成功”，而是以下完整实验：

```text
给定：argv + stdin bytes + 初始文件集合 + cwd + 环境变量
执行：指定 gold
记录：退出码 + stdout bytes + stderr bytes + 文件副作用 + 是否超时
```

**[复现约定]** 每个实验保存一条 `observations.jsonl`，二进制内容用 base64 或受控附件存储：

```json
{
  "probe_id": "stdin-default-hello",
  "gold_sha256": "<binary-sha256>",
  "argv": ["-"],
  "stdin_base64": "aGVsbG8=",
  "cwd_fixture": "empty-dir",
  "env": {"TZ":"UTC"},
  "input_files": [],
  "exit_code": 0,
  "stdout_base64": "<captured-bytes>",
  "stderr_base64": "<captured-bytes>",
  "output_files": [],
  "timed_out": false,
  "normalization": []
}
```

不要把缺失观测伪装成空输出。locale、时区等应选择目标镜像实际支持的值并固定；不要假定所有镜像都有同一 UTF-8 locale。

### 5.2 探测矩阵

按工具适用范围填写矩阵，每项至少包含一个正常行为和一个边界/错误行为：

| 维度 | 需要探测的内容 |
| --- | --- |
| 接口发现 | `--help`、短 help、version、无参数、子命令列表 |
| 核心功能 | 每个主要转换、计算、查询或生成操作 |
| 参数 | 默认值、显式值、组合、缺失值、非法值、互斥关系 |
| I/O | stdin、文件、多个输入、stdout/stderr、输出文件 |
| 数据边界 | 空输入、空白、Unicode、特殊字符、较长输入、格式错误 |
| 配置 | 配置文件、环境变量、优先级；仅在工具支持时 |
| 错误 | 未知选项、缺失参数、不存在的文件、无法解析的内容 |

重要约束：不要因为容易探测 help/version 就用它们替代核心功能；生成器必须验证生成内容，压缩器必须验证内容恢复或独立已知结果，TUI 不能仅靠 `--version` 宣称覆盖了交互。

### 5.3 确定性与归一化

对代表性探测至少运行两次。记录真实差异，再决定：固定输入/种子/环境、进行有针对性的归一化，或剔除该行为。

可归一化的典型项包括 fixture 临时路径、已知 ANSI 序列、明确不影响语义的时间字段。不能用宽泛的字符串删除规则抹掉真正业务输出，也不能把排序操作施加到顺序本身属于协议的输出上。

**[原实现]** 自动门禁只对 `--help` 做两次归一化比较；不是整套行为的稳定性验证。本文要求逐类代表性探测，是补充流程。

### 5.4 Oracle 来源限制

期望值必须来自该 commit 的 gold 观测或可独立核对的规范，不得由模型凭印象生成。

测试运行时不得调用 gold 获取答案，隐藏评测器只执行替代程序。测试中如果先用待测程序生成期望值，再用同一程序验证，很可能只测了自洽性；应增加独立常量或独立内容检查。

## 6. 阶段 D：生成黑盒测试

### 6.1 生成目标与目录

**[原实现]** 目标为 100–400 个测试、约 5–10 个功能 branch、每 branch 约 20–60 个测试。这些是提示词目标，不是程序强制门槛，也不是必须同时精确满足的配额。

生成阶段目录：

```text
<instance>/eval/branches/
  help/tests/test_help.py
  core/tests/test_core.py
  stdin/tests/test_stdin.py
  files/tests/test_files.py
  errors/tests/test_errors.py
```

这里 branch 是测试分组，不是必须创建一个 Git branch。让模型使用描述性名称，控制器计算最终 ID。

### 6.2 为兼容现有 QC 的测试限制

**[复现约定]** 基线使用顶层 `def test_*`，不使用类测试和参数化 ID。每个模块自包含 helper 与 fixture；不要依赖跨 branch 的 `conftest.py`、兄弟模块或未声明资源，因为现有展平器只搬运 `test_*.py`。

所有测试调用名为 `./executable` 的程序，不读取源码、不依赖内部函数名。存在文件副作用时用测试私有目录，避免测试顺序依赖。

字节级 helper 的推荐形状：

```python
from pathlib import Path
import subprocess

EXECUTABLE = Path("./executable").resolve()

def run(args, stdin=None, cwd=None):
    return subprocess.run(
        [str(EXECUTABLE), *args],
        input=stdin,
        cwd=cwd,
        capture_output=True,
        timeout=10,
    )
```

这是本文的补充模板：原 Kimi prompt helper 使用 `text=True`，但本地 xxHash 样例采用字节模式。需要精确字节契约时使用后者，避免文本解码和换行转换引入歧义。提前保存 executable 的绝对路径，可避免切换到 fixture cwd 后找不到程序。

### 6.3 一个合格测试的要求

- 能追溯到明确的探测输入和 gold 观测。
- 断言对应核心行为，不能只写“未抛异常”或只查成功退出码。
- 对错误用例同时关注退出码与有意义的错误输出。
- 文件生成类用例应检查内容或语义，不仅检查返回码。
- 对可稳定比较的内容使用精确比较；部分断言应说明为何其他内容不属于测试契约。
- 不包含“失败就跳过”“捕获异常后忽略”或双向宽松断言。
- 在全新目录、无网络环境中可独立执行。

### 6.4 Prompt 模板

以下是可用于重建引擎的规范化 prompt，不是历史 prompt 的逐字副本。严格复跑原 prompt 时，以 `kimi_agent.py` 内 `sysmsg` 为准并保存渲染后哈希。

```text
你负责为 {org}/{repo}@{commit} 构造一个黑盒行为重建题。
构造侧可读源码；最终测试只能通过运行 ./executable 验证外部行为。
工作目录为 {scratch}，产物目录为 {instance_dir}。

每次只返回一个 JSON action：
{"tool":"bash","cmd":"..."}
{"tool":"write","path":"绝对路径","content":"..."}
{"tool":"done"}

依次完成：
1. 确认是可离线测试的独立 CLI、许可证符合选择标准；不符合则写 SKIP 原因。
2. 取得指定 commit，核对实际 SHA；在 scratch 构建并得到 scratch/executable。
3. 运行 help/version、主要功能、参数组合、stdin/文件及错误路径。
   记录真实 stdout、stderr、退出码、文件副作用；重复代表性调用检查稳定性。
4. 按功能生成约 5–10 个 branch、目标 100–400 个有意义的测试。
   放入 eval/branches/<descriptive-area>/tests/test_<area>.py。
   使用顶层测试函数和自包含 fixture。期望值来自实际观察，不得猜测。
5. 写 task.yaml、Dockerfile 和初步 meta.json。
   Dockerfile 使用实际成功的构建命令，固定源码版本与基础环境。
6. 完成后返回 done。

不要自行生成最终 tests.json、run.sh、branch hash、测试统计和验收结果。
这些由控制程序执行。不要把“文件写好了”视为通过验收。
每次 shell 从 scratch 开始，需要进入项目目录时在该次命令中明确指定。
```

若采用 `observations.jsonl`、build manifest 等补充证据，应在 prompt 中增加明确文件路径与 schema，或由控制器自动捕获，不能只希望模型“记得保存”。

## 7. 模型工具循环协议

重建一个最小控制器不需要模型原生 function calling。现有实现通过普通消息传递 action。

### 7.1 请求与返回

**[原实现]** 向配置的 base URL 后追加 `/chat/completions`，发送：

```json
{
  "model": "<model-id>",
  "messages": [
    {"role":"system","content":"<rendered-prompt>"},
    {"role":"user","content":"Begin. Send your first JSON action."}
  ],
  "max_tokens": 4096,
  "temperature": 0.2
}
```

读取 `choices[0].message.content`。模型回复作为 assistant 消息追加，工具结果作为下一条 user 消息追加。原引擎没有通用鉴权头配置；需鉴权的服务必须通过经审核的适配方式接入，不能假定设置 API key 环境变量就自动生效。

### 7.2 动作语义

| action | 输入 | 控制器操作 | 给模型的结果 |
| --- | --- | --- | --- |
| bash | `cmd` | 在 scratch 的新 shell 进程中运行 | 退出码及 stdout+stderr 末尾 4000 字符，或超时/错误 |
| write | `path`, `content` | 写入实例或 scratch 范围内文件 | 写入确认或拒绝 |
| done | 无 | 退出生成循环，进入 QC | 不代表整个 instance 成功 |

**[复现约定]** 路径必须 resolve 后验证包含关系，拒绝越界与符号链接逃逸；shell 应在任务隔离环境内运行。只限制 write action 不能约束 shell。

### 7.3 原始预算

| 参数 | 默认值 |
| --- | ---: |
| 交互步骤上限 | 80 |
| 单次响应输出预算 | 4096 tokens，可由 `KIMI_MAX_TOKENS` 覆盖 |
| 模型请求超时 | 200 秒 |
| 模型请求尝试次数 | 最多 5 次 |
| 重试等待 | 第 k 次约 `8 × k` 秒 |
| 单次 shell 超时 | 150 秒 |
| 传回模型的工具结果 | 最多约 4000 字符 |
| help 探测单次超时 | 30 秒 |
| QC 单次全套超时 | 300 秒 |
| QC 单测试超时 | 安装 pytest-timeout 时为 20 秒 |

步数不是总费用预算；两者需分别控制。**[复现约定]** 保存完整模型回复和完整工具 stdout/stderr，截断仅用于模型上下文。记录所有失败尝试，不以删除目录代替状态记录。

## 8. 阶段 E：测试规范化与 QC

### 8.1 展平与编号

**[原实现]** 对每个描述性 branch 名称计算：

```text
branch_id = SHA256(UTF8(commit + ":" + branch_name)) 的前 12 个十六进制字符
```

若输入目录本来就是 12 位小写十六进制，原实现直接使用该名字。为统一复现，模型应始终使用描述名。

`test_<area>.py` 展平为 `eval/tests/test_<branch_id前6位>_<area>.py`。发生重名时原实现不断追加 `_x`；新的尝试应从空的生成目录开始，避免旧文件改变命名。

导出的测试标识为：

```text
tests.<module_stem>.<function_name>
```

例如 `tests.test_abcdef_core.test_stdin_default`。这是本格式的 dotted identifier，不是 pytest CLI 使用的原生 `path::function` nodeid。调用 pytest 时需要显式映射，不能把两者混用。

### 8.2 两套执行

建立两个独立、干净的测试工作区，各自含 `tests/` 和名为 `executable` 的待测程序：

1. dummy：内容为 `#!/bin/sh` 加 `exit 0` 的可执行脚本。
2. gold：构建阶段取得的真实参考程序。

在各自工作区运行同一套测试：

```bash
python3 -m pytest tests/ --junitxml=eval/results.xml -q -p no:cacheprovider --timeout=20
```

命令假设 `eval/` 已建立、插件已存在，解释器为预先批准的环境；外层控制器另设 300 秒全套时限。没有插件时原实现省略 `--timeout=20`。两个运行的 fixture 状态必须独立，避免先运行的一套影响后一套。

### 8.3 原始分类顺序

**[原实现]** 对每个预期测试 ID，按如下优先级分类：

| 条件 | 原始处理 |
| --- | --- |
| dummy 或 gold 的 JUnit 中缺少该 ID | ignore：`slow_or_hang` |
| dummy 被判为 pass | ignore：`dummy_pass` |
| gold 未被判为 pass | ignore：`gold_fail` |
| 其他 | 保留 |

原实现只把 JUnit 中带 failure/error 的节点视为不通过，因此 skip 分类不完善。**本文复现约定：skip、收集失败、环境失败均不能当成有效通过；必须记录真实状态，再映射到兼容清单或单独报错。** 这属于有意纠正，不应写成对原始位级行为的复刻。

设 `T` 为全部登记测试、`I` 为 ignored 测试，`K=T−I` 为有效测试。原实现 `|K| < 8` 时拒绝该 instance；达到 8 是历史最低产出门槛，不是充分的质量证明。

### 8.4 发布前额外门禁

**[复现约定]** R2 至少检查：

- K 中每个测试在独立重建的 gold 上通过。
- K 中每个测试在 dummy 上失败，而不是 skip、收集错误或未执行。
- 核心功能确实被 K 覆盖，不能只剩 help/version。
- 每个 active branch 至少有一个有效测试。
- 再跑一次 gold 验证稳定性；若不稳定则退回修订。
- 清单 ID 与实际收集一一对应；fixture 和辅助资源均已声明。

这是方法复现时为闭合验收所加的门禁，超出原脚本“只保留 8 个以上”的最小判断。

**[扩展]** 增加恒定输出、help-only、固定错误码等退化实现；增加弱断言 linter、覆盖率引导和有预算的失败修订循环。本副本没有完整实现这些机制，不应把它们列为已经复现的原功能。

## 9. 阶段 F：生成最终 instance

### 9.1 目录结构

```text
instances/<org>__<repo>.<sha7>/
  task.yaml
  Dockerfile
  tests.json                 # 注意：在实例根目录，不在 eval/ 下
  meta.json
  eval/
    run.sh
    tests/
      test_<bid6>_<area>.py
```

原引擎运行日志 `.kimi.log` 是调试产物，不属于格式必需项。构造证据应放在独立 author-only 目录，不应随题目公开给解题方。

### 9.2 `task.yaml`

```yaml
repository: ExampleOrg/example-cli
commit: "<full-40-hex-sha>"
language: c
difficulty: easy
source_globs:
  - "src/**"
  - "*.c"
  - "*.h"
eval_clean_hashes:
  - "<sha256-of-source-file>"
```

`source_globs` 表示清除实现相关文件的路径模式；`eval_clean_hashes` 是按内容识别原始文件的哈希列表，不是二进制指纹列表。具体消费方式依赖 runtime。

**[原实现]** 哈希来自 scratch 中找到的 Git 工作树的 tracked files，并带有文档/许可排除启发式。

**[复现约定]** 必须显式绑定目标仓库和实际 commit，保存“路径 → SHA256”的构造侧 manifest，再导出最终哈希列表。不能在取得目标 commit 失败时悄悄换成 HEAD；也不能把空哈希列表视为清理已完成。文档与许可证哪些公开、哪些保留在构造侧，要单独列清单。

### 9.3 `tests.json`

```json
{
  "branches": {
    "abcdef123456": {
      "ignored": false,
      "ignore_reason": "",
      "tests": [
        "tests.test_abcdef_core.test_stdin_default",
        "tests.test_abcdef_core.test_weak_exit_only"
      ],
      "ignored_tests": [
        {
          "name": "tests.test_abcdef_core.test_weak_exit_only",
          "reasons": [{"id":"dummy_pass"}]
        }
      ]
    }
  }
}
```

这是结构示例，branch ID 必须实际计算。ignored 的测试仍在 `tests` 列表中，消费者要扣除 `ignored_tests`；不是把清单中所有测试直接当成有效测试。

### 9.4 `eval/run.sh`

与现有格式一致的入口：

```bash
#!/bin/bash
set -e
cd "$(dirname "$0")/.."
python3 -m pytest eval/tests/ --junitxml=eval/results.xml -v || true
```

重要：该脚本运行全部测试，**不自行读取 `tests.json` 来剔除 ignored 项**，并且强制保留成功的 shell 退出状态。因此验收器必须解析 JUnit 并应用有效测试集合；不能把 shell 退出 0 当成通过，也不能因为一个 ignored 测试失败就判整题不合格。

**[复现约定]** 将脚本设为可执行，或规定调用方式始终为 `bash eval/run.sh`。全套与单测试时限由实际评测入口明确设置，不依赖此脚本的默认行为。

### 9.5 元数据和难度

```json
{
  "category": "compression-crypto",
  "language": "c",
  "tool_type": "checksum CLI",
  "difficulty": "easy",
  "n_tests": 120,
  "n_kept": 110,
  "n_branches": 6,
  "build_verified": true,
  "confidence": "high",
  "notes": "<observed-surface-summary>"
}
```

数字仅为 schema 示例，不是任何实际运行结果。统计值由控制器计算。

**[原实现]** 从 `--help` stdout 中提取 long flags 和疑似子命令数，计算：

```text
score = flags + 4 × subcommands + n_branches + floor(n_kept / 30)
若 subcommands=0 且 flags≤2 且 n_kept<20：unrated
否则若 score≥45 或 subcommands≥8：hard
否则若 score≥18 或 subcommands≥2：medium
否则：easy
```

`unrated` 时 task.yaml 省略 difficulty，meta 可保留 `unrated`。这只是接口规模标签，不能宣称等同于实际解题难度。

**[复现约定]** `build_verified` 只表示所记录的 gold 构建和探测成功。另用 author-only 发布记录表示 R2 是否完成，避免一个布尔值同时承担所有验收含义。

## 10. 阶段 G：独立重建、cleanroom 与发布

### 10.1 Dockerfile recipe

复现要求：

1. 固定基础镜像 digest、源码 SHA、编译器与关键依赖版本。
2. 使用与构建阶段相同且已记录的 binary target 和命令。
3. 明确建立 `/workspace` 和 `/opt`。
4. 按现有 recipe 契约将 gold 放到 `/workspace/executable` 与 `/opt/programbench-stashed-executable-do-not-modify`。
5. 显式声明 gold 所需运行库和只读资源。
6. 将实现相关源码、历史、缓存、构建残留与公开材料隔离。

**[复现约定]** 优先分离 builder 与 runtime，只把审核过的二进制、运行依赖和公开文档注入 runtime。不能靠少量 `rm` 路径宣称整个环境已经清理完毕。

固定 Dockerfile 不自动意味着字节级可复现；包仓库、网络下载和构建时间戳仍可能改变结果。最低要求是固定来源并通过行为重放；如要求 binary hash 相同，需要另行设计可复现构建。

### 10.2 三种环境的区别

| 环境 | 允许存在 | 不应暴露给解题方 |
| --- | --- | --- |
| Builder | 完整源码、依赖缓存、构建日志、gold | 整个环境 |
| Solver cleanroom | 公开文档、许可输入资产、可交互执行的 gold | 原始源码、Git 历史、隐藏测试、可复制提交的 gold 通道 |
| Evaluator | 替代程序、隐藏测试与 fixture | 不向解题方开放该环境 |

execute-only 权限需要配合非特权 UID、文件所有权和目录权限；简单 `chmod 111` 不能在 root 可访问环境中充当完整隔离。最终验收应防止答案复用 gold，而不是禁止解题阶段观察 gold。

本副本不足以恢复这些 runtime 细节，所以 R2 完成不等于 R3 完成。

### 10.3 R2 操作顺序

1. 在独立的已审核构建环境重放 recipe，不使用生成阶段 scratch 的偶然残留。
2. 核对源码 SHA 和运行依赖；记录新 gold 的 binary hash。
3. 将隐藏测试和 fixture 注入独立评测工作区。
4. 用新 gold 执行有效集合 K，要求全部通过；保留 JUnit、stdout/stderr 和退出状态。
5. 用 dummy 执行 K，要求没有有效通过项，并排除基础设施错误。
6. 检查 cleanroom 的文件、权限和公开资产清单；待 R3 接入时继续做防包装/复制检查。
7. 将所有验收结果写入发布 manifest，再原子发布 instance。

镜像构建前需另行确认 Docker 存储实际位置；仅设置 `TMPDIR` 不能把 Docker image/layer 存储迁到持久卷。本文不提供未经容量审查的自动拉取或构建命令。

## 11. 最小实际复现操作手册

### 11.1 第一次只做一个 instance

选择本地 xxHash 样例作为端到端对照，固定其 SHA。期望验证的是：同一软件版本能再次构建、模型能生成新测试、QC 可运行、产物能独立重建。不要要求模型生成原来的 21 个测试，更不要把原样例复制过去充当生成成功。

启动前清单：

- [ ] 已批准本次源码下载、模型调用、编译和预期磁盘用量。
- [ ] 已检查 ephemeral 和持久卷容量。
- [ ] 新运行目录已建立，且与 `factory_ref/pb` 分离。
- [ ] Python、pytest、pytest-timeout、编译工具链已存在并核验版本。
- [ ] 模型服务 URL、model ID、访问方式已核验；不依赖源码中的历史默认地址。
- [ ] candidate 已固定 SHA 并通过筛选。
- [ ] 外层有任务隔离、资源上限及持久卷缓存配置。
- [ ] `PB_WT/instances` 中没有会触发跳过的同 ID 半成品。

### 11.2 原引擎的直接调用方式

以下是 **R1 smoke run** 的调用示意，前提是上述准备已完成。它不是无副作用命令：会访问网络、调用模型、下载/构建，并且原引擎会删除自己的失败目录和 scratch。

```bash
# 以下变量由运行配置提供；不要把占位符当成实际配置。
# PB_RUN_ROOT：已建立的持久卷专用运行目录。
# PB_PYTHON：已批准、带 pytest 的 Python 解释器绝对路径。
# KIMI_BASE：已核验服务 base URL，包含 /v1（若服务要求）。
# KIMI_MODEL：已核验服务 model ID。
: "${PB_RUN_ROOT:?set persistent run root}"
: "${PB_PYTHON:?set existing Python interpreter}"
: "${KIMI_BASE:?set verified endpoint}"
: "${KIMI_MODEL:?set served model id}"

export PB_WT="$PB_RUN_ROOT/factory"
export PB_TMP="$PB_RUN_ROOT/scratch"
export TMPDIR="$PB_RUN_ROOT/scratch"
export XDG_CACHE_HOME="$PB_RUN_ROOT/cache"
export PIP_CACHE_DIR="$PB_RUN_ROOT/cache/pip"
export UV_CACHE_DIR="$PB_RUN_ROOT/cache/uv"
export PYTHONDONTWRITEBYTECODE=1
export KIMI_BASE KIMI_MODEL

"$PB_PYTHON" -B /volume/pt-coder/users/mlei/factory_ref/pb/tools/kimi_agent.py \
  '{"org":"Cyan4973","repo":"xxHash","commit":"e573d4d2aaeaba0f3e5a0a9a54144a1f2b4b56e7","language":"c","category":"compression-crypto"}' \
  > "$PB_RUN_ROOT/evidence/engine.stdout.log" 2>&1
```

准备环节还必须建立 `factory`、`scratch`、`cache`、`evidence` 等目录。原引擎内部通过 PATH 调用 `python3` 执行 QC；它必须与 `PB_PYTHON` 指向同一已核验环境，不能只更换外层解释器就假定内部一致。

Rust 等候选还需审核 `RUSTUP_HOME` 和实际工具链缓存；原引擎设置的 Go/Cargo 变量不覆盖所有可能的写入。该命令不能替代资源隔离。

运行结束检查输出目录：

```text
<run_root>/factory/instances/Cyan4973__xxHash.e573d4d/
```

不要只看进程退出码或 `meta.json` 是否存在。检查最终 `OK` 输出、完整产物、`tests.json` 统计与异常日志，并按第 10 节执行 R2。

原引擎会删除成功运行的 scratch，包括 gold，并且不保留完整 QC 证据。要满足本文的完整证据链，应在独立的执行副本或重建控制器中增加归档点；不应修改只读参考目录来偷偷改变实验基线。

### 11.3 xxHash 行为对照

下列值来自本副本已有测试，是静态对照材料，不是本次实测结果。重新构建后应重新观测确认：

| 参数 / 输入 | 本地样例中的关键断言 |
| --- | --- |
| `-`，stdin 为 `hello` | XXH64 为 `26c7827d889f6da3` |
| `-H0 -`，stdin 为 `hello` | XXH32 为 `fb0077f9` |
| `-H2 -`，空 stdin | XXH128 为 `99aa06d3014798d86001c324468d497f` |
| `--little-endian -`，stdin 为 `hello` | 输出摘要为 `a36d9f887d82c726` |
| help/version | 样例从 stderr 检查相关文本，不应默认在 stdout |

这能帮助快速发现编译了错误 binary、版本不符或 stdout/stderr 使用错误。不能用这几条冒烟测试代替完整测试生成。

### 11.4 完成判据

一次最小复现完成后应交付：

1. 一条冻结 candidate 和运行配置。
2. gold 构建 manifest、日志与版本证据。
3. 探测观测及生成测试。
4. dummy/gold QC 报告、有效集合 K 和忽略原因。
5. 一个符合第 9 节契约的 instance。
6. 一份独立重建与 K 全通过的 R2 报告。
7. 明确声明 R3 尚未验证，而不是笼统写“ProgramBench 已完全复现”。

## 12. 从单实例扩展到批量

**[复现约定]** 不从旧 cron/supervisor 恢复起步；先实现一个有限候选队列和持久状态表。

```text
FOUND → CLAIMED → BUILT → PROBED → GENERATED → QC_PASSED → R2_PASSED → PUBLISHED
             各阶段失败 → FAILED(stage, reason, retryable, attempt_id)
             资格不满足 → REJECTED(reason)
```

最小状态记录包括：仓库键、完整 SHA、attempt ID、状态、开始/结束时间、worker、失败原因、证据路径、发布版本。

调度原则：

- 仓库/版本只能被一个 worker claim，claim 有所有者和超时恢复规则。
- attempt 输出相互隔离；已发布实例不可原地覆盖。
- 网络/服务限流可有限退避重试；不合资格项目不重复投入构建。
- 构建失败和测试失败分别计数，不能全部归为“模型失败”。
- 只有 `PUBLISHED` 进入完成统计，不能以 `meta.json` 存在计数。
- 先串行通过一个项目，再做小批跨语言样例，最后依据实际 CPU、内存、磁盘和服务容量增加并发。

总控伪代码：

```text
for candidate in frozen_candidates:
    claim = claim_once(candidate.identity)
    if not claim:
        continue
    attempt = create_isolated_attempt(claim)
    try:
        verify_candidate(candidate)
        gold, build_evidence = build_reference(candidate, attempt)
        observations = probe_reference(gold, attempt)
        proposed_tests = generate_tests(observations, attempt)
        flattened_tests, mapping = normalize_tests(proposed_tests)
        qc = run_dummy_and_gold(flattened_tests, gold)
        require_baseline_acceptance(qc)
        instance = assemble_artifacts(candidate, mapping, qc, build_evidence)
        delivery = rebuild_and_replay(instance, active_tests=qc.kept)
        require_delivery_acceptance(delivery)
        publish_atomically(instance, evidence=attempt.evidence)
    except StageFailure as failure:
        persist_failure(attempt, failure)
        schedule_retry_only_if_allowed(failure)
```

这些函数表达需要实现的模块接口，不是本目录已经存在的可调用 API。原 Kimi 路径把 build/probe/generate 合在一次 agent 会话中，重建时可以保留这种会话结构，同时在控制器中记录阶段证据。

## 13. 故障处理决策表

| 现象 | 先检查 | 处理 |
| --- | --- | --- |
| `SKIP exists` | 是否只有早写出的 meta，还是确已验收 | 不直接删除；核对状态，使用新 attempt |
| `RESOLVE_FAIL` | candidate SHA、仓库可达性 | 在候选阶段解决，不让模型猜版本 |
| `KIMI_ERR` | 服务协议、model ID、鉴权、限流 | 保存请求诊断信息，有限重试；不记录密钥 |
| `INCOMPLETE` | gold、task、Dockerfile、branch tests 哪项缺失 | 回到相应阶段，保留失败证据 |
| `SKIP_GATE` | help 执行、动态库、许可证、输出差异 | 区分环境故障与真正不适合入选 |
| `SKIP_LOWYIELD` | dummy 通过多还是 gold 失败多 | 前者改断言，后者核查 oracle/fixture/环境 |
| 大量 `slow_or_hang` | 是否有收集错误、ID 映射错误、导入失败 | 先修基础设施，不批量当超时忽略 |
| Docker 重建失败 | 版本、网络依赖、目录、工具链 | R2 不通过，不能仅因 R1 成功而发布 |
| gold 全过但 dummy 也大量过 | 是否只查退出码或弱子串 | 重写行为断言并重跑 QC |
| scratch 消失 | 是否为原引擎清理逻辑 | 使用外部日志；下次在清理前增加归档 |

## 14. 未还原项与来源索引

以下内容不能由当前副本确定：历史候选语料、历史完整运行轨迹、服务当前可用性、精确模型权重、缺失的集群 wrapper、上游 runtime 的 branch 执行与计分逻辑、解题提交协议、gold 权限与清理的最终实现。

本地规划文档还谈到覆盖率插桩、原生测试 harvest、断言 linter、交互式 TUI 测试等方法；不能因为它们出现在方案中，就认定现有工厂实现了这些步骤。本文没有核验外部论文，也不使用其统计来定义复现成功。

| 需要追溯的规格 | 本地来源 |
| --- | --- |
| 工厂目标与黑盒任务形态 | [BENCHMARK_CONSTRUCTION_PLAN.md](BENCHMARK_CONSTRUCTION_PLAN.md)、[CLAUDE.md](CLAUDE.md) |
| 原始 prompt、工具循环、预算、QC、编号、难度算法 | [kimi_agent.py](tools/kimi_agent.py) |
| 产物布局和基本 schema | [INSTANCE_FORMAT.md](INSTANCE_FORMAT.md) |
| 搜索与候选合并 | [github_candidates.py](tools/github_candidates.py)、[aggregate.py](tools/aggregate.py) |
| 现有候选配额 | [finalize_balanced.py](tools/finalize_balanced.py) |
| 替代构造引擎 | [build_instance.sh](tools/build_instance.sh)、[build_instance_claude.sh](tools/build_instance_claude.sh) |
| 结构检查的现有边界 | [validate.py](tools/validate.py) |
| 端到端静态样例 | [xxHash Dockerfile](instances/Cyan4973__xxHash.e573d4d/Dockerfile)、[xxHash tests](instances/Cyan4973__xxHash.e573d4d/eval/tests/test_xxHash.py)、[xxHash task](instances/Cyan4973__xxHash.e573d4d/task.yaml) |
| 副本删减范围 | [REFERENCE_NOTES.md](REFERENCE_NOTES.md) |
| 原实现风险审计（补充阅读） | [PIPELINE_ANALYSIS.md](PIPELINE_ANALYSIS.md) |

复现的核心交付不是“把原脚本跑起来”，而是证明：固定版本的软件能够被构建成可靠 oracle；测试来自真实行为并通过判别验收；题目可以在独立环境重建；生成与发布的每一步都有可追溯证据。
