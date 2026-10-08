# DeepSWE 风格实例构造：逆向复现规格与操作手册

版本：2026-09-16。证据范围：本目录下三个 pipeline 的源码、配置、Prompt 和测试。

本文回答的是：**给定固定版本的仓库，怎样生产题面、问题环境、隐藏测试、参考解和评分器，并证明它们组成了一道可交付的题？** 代码位置只放在末尾作为证据索引，正文按重建系统、执行单题和验收产物组织。

建议阅读顺序：第 1—4 节定义交付契约；第 5—7 节选择构造路线；第 9—10 节完成单题运行与包装；第 11—13 节验收、恢复和扩大规模；第 14 节指导独立重建。

## 1. 复现对象和可承诺的范围

### 1.1 三条可替代路线

| 路线 | 必要起点 | gold solution 从哪里来 | 首次试验的用途 |
|---|---|---|---|
| A：Ablation，反向构造 | 镜像中真实存在、能测试的功能 | 删除/禁用实现，再由程序反转修改 | 最清楚地理解 instance 如何成立 |
| B：Specialize，参考题演化 | 完整参考题：题面、镜像、答案、测试、评分器 | 先完成新功能，再遮蔽实现，反转遮蔽 | 围绕既有难点扩增新题 |
| C：Query → Testcase → Solution，正向构造 | 固定版本的仓库及可运行环境 | 根据新需求和测试编写实现 | 从新的仓库生成任务候选 |

三者不是必须顺序执行的三个阶段。B 在工程上复用了 C 的 Agent/Harbor 适配器，但不要求先跑一批 C 的任务。

复现对象是当前副本体现的 **DeepSWE 风格合成工艺**，不是已获证实的“原始 DeepSWE 官方制作全过程”。“未出题 repo”属于输入数据选择条件，代码不能自动证明其未进入训练或其他评测集。

### 1.2 区分三种复现目标

1. **工艺复现**：相同的状态转换、角色约束、实验矩阵和准入规则，产出同类型实例。本文主要提供这一规格。
2. **单实例回放**：冻结输入、镜像、Agent 产物和补丁后，不再调用模型，也能重新得到相同问题态与验收结果。这应作为交付底线。
3. **历史批次逐题复刻**：还需当时模型服务、采样设置、调度、响应和完整外部资产。当前副本不具备这些条件，不能承诺同题面、同数量或同通过率。

本文区分两种表述：**副本行为**是静态阅读能够确认的现有流程；**发布补充**是为了可靠复现与发布建议增加的检查，不表示原程序已实现。

下面所有命令均为未来运行模板。本次只读检查副本并写文档，没有执行构造、测试、安装、容器启动或模型请求。

## 2. 交付契约：一个 instance 是一项可执行实验

### 2.1 六个组成部分

```text
I = (E, Q, S, T, V, P)

E：求解者得到的问题态环境，包括代码、依赖、工具、工作目录
Q：题面，包括接口、可观察行为、兼容性、边界条件、非目标
S：gold solution patch，只含允许求解者修改的内容
T：验证时注入的隐藏测试与 fixture 补丁
V：补丁应用、测试命令、报告解析、测试 ID 集合和计分逻辑
P：来源、版本、hash、构造与验证记录
```

只有 Q 是 query；Q/S/T 在构造阶段通过是内部有效 instance；最终任务包在指定平台中通过 Nop/Oracle，才证明包装可用。三者不能混为一个 completed 状态。

### 2.2 必做实验

| 实验 | 状态 | 需要证明 |
|---|---|---|
| Nop | E + T，不提交答案 | 目标行为确实缺失，不是空解题 |
| Oracle | E + S + T | 参考解实现目标并通过验收 |
| Regression-before | E 上适用的已有测试 | 初始状态具有正常可测试性 |
| Regression-after | E + S 上相同回归测试 | 实现没有破坏受保护行为 |
| Near-miss，可选 | E + 可应用的不完整/错误实现 + T | 测试拒绝某种明确的错误答案 |

补丁无法应用是无效实验，不是目标行为失败。测试 runner 崩溃也不是有效的红灯。

F2P（fail-to-pass）应是问题态失败、gold 态通过的**真实测试 ID**；P2P 是两态均通过的回归 ID。严格的行为级 F2P 要在问题态报告中明确出现 `failed`；部分现有路径也接受 before missing，本文会逐路线注明，不能悄悄等同于断言失败。

二元通过条件是：F2P 非空、所有 F2P 通过、所有要求的 P2P 通过。发布补充要求 P2P 也非空。missing、skipped、超时不得作为 passed。

### 2.3 最终资产布局

以下是语义布局，三套导出器的实际字段和附加文件不完全相同：

```text
task/
├── task.toml                     # 任务、环境和来源元数据
├── instruction.md                # 求解者可见题面
├── environment/
│   ├── Dockerfile                # 重建 E，或 FROM 已构建的问题态镜像
│   └── ablation.patch            # 需要时携带
├── solution/
│   ├── solution.patch            # 不能进入 solver 环境
│   └── solve.sh                  # oracle 应用方式
├── tests/
│   ├── test.patch                # 验证时才注入
│   ├── test.sh                   # 执行命令、产生原生报告
│   ├── config.json               # 测试 ID、报告路径和评分设置
│   ├── grader.py                 # 解析与计分
│   └── Dockerfile                # 独立 verifier 模式需要
└── pre_artifacts.sh / start.sh   # 随导出器和平台生命周期不同

audit/                           # 发布补充，可置于任务包外
├── provenance.json
├── behavior_test_matrix.json
├── nop/                         # 报告、日志、退出码和 reward
├── oracle/
└── near_miss/                    # 若执行
```

文件存在不代表它们实际被执行。必须证明 model.patch 被捕获、T 被注入、真实测试运行、报告正确解析、reward 正确生成。

## 3. 开工前需要补齐什么

### 3.1 共同输入

| 资产 | 必须记录 | 不记录的后果 |
|---|---|---|
| 仓库 | URL、完整 commit SHA、源码快照 hash | patch 基线不可确定 |
| 镜像 | 名称、不可变 digest/ID、archive hash、CPU 架构 | 同 tag 不保证同环境 |
| 仓库根目录 | 容器内实测路径，如 `/app`、`/testbed` | patch、测试与捕获脚本错位 |
| 工具链与依赖 | 版本、现有环境、离线依赖可用性 | 环境失败被误当行为失败 |
| 原生测试入口 | argv、cwd、必要 env、报告格式 | 无法回放测试或对齐 ID |
| Agent runtime | Harbor checkout/版本、离线 Agent 包、适配器版本 | 构造角色无法启动或行为不同 |
| 模型配置 | 模型标识、服务版本（若可得）、推理与预算参数 | 无法解释不同批次的产出差异 |
| 数据访问 | 输入任务/镜像访问权限，凭据通过环境注入 | 源码副本不能代替数据和服务 |

当前副本没有完整 Harbor、原始任务集、镜像 archive、生产日志或模型服务。包的 Python 依赖不是整个系统的依赖闭包。

B/C 声明 Python ≥3.10；C 声明 PyYAML、Jinja2，Python 3.10 还需 tomli；B 自身依赖少，但会复用 C 并调用 Harbor；A 也读取 YAML/模板。没有证据支持指定一套“已验证兼容”的精确依赖版本。

### 3.2 A 的输入

优先用 `run-task-folder`，输入来源 task 目录及其可构造镜像；测试复用模式还需要对应测试资产。来源镜像可能已经是原题的缺陷态：A **不先应用原题 gold**，新消融必须针对另一个仍完整存在的功能，避开原题受保护区域。

若用 `run-repo`，另外需要 `repos.yaml` 登记 repo URL、base SHA、安装/测试信息；不要假设当前副本已包含完整仓库登记。

### 3.3 B 的输入

输入根目录必须有 `manifest.json`，每条参考题必须完整。最小 manifest 形状示意如下，值均为占位：

```json
{
  "tasks": [{
    "task_id": "reference-001",
    "ext_id": "reference-external-001",
    "repo": "owner/repo",
    "repository_url": "https://example.invalid/owner/repo",
    "language": "python"
  }]
}
```

必需文件：`task.toml`、`instruction.md`、`solution/solution.patch`、`tests/test.patch`、`tests/config.json`、`tests/test.sh`、`tests/grader.py`、`environment/Dockerfile`、`tests/Dockerfile`。

加载器以 task_id/ext_id 对目录，要求 manifest 中两个 ID 和可解析的 base commit；base 可来自 task metadata 或 tests config。环境必须提供镜像或 archive。

hardness 为可选输入，可包含模型通过次数、集合标记或失败尝试的 `model.patch`。表格中的 `near_miss=true` 不等于已经提供可执行的 near-miss 答案。

### 3.4 C 的输入

可以输入已有 task，也可输入只有 `task.toml + 空 instruction.md + environment/Dockerfile` 的仓库 source scaffold。后者不是可评分任务，无需预先存在 gold 或测试。

现有转换器接收按语言归档的 repo-image metadata，形状示例：

```json
{
  "identity": {
    "language": "python", "owner": "example", "repo": "configlib",
    "commit_hash": "0123456789abcdef0123456789abcdef01234567",
    "commit_prefix": "01234567"
  },
  "repository_metadata": {"url": "https://example.invalid/example/configlib"},
  "image": {
    "id": "example-configlib-01234567",
    "file_name": "example-configlib-01234567.tar.gz"
  }
}
```

这是形状示例，不对应真实资产。约束：SHA 为 40 位十六进制；archive 以 `.tar.gz` 结尾；同一 owner/repo 不重复；输出目录尚不存在。

archive 路径拼成 `<remote-images-root>/<language>/<file_name>`，root 不应重复语言层。没有 namespace/tag 的 image ID 会补 namespace 和 `:latest`，必须核对实际镜像并记录不可变身份。生成 Dockerfile 默认 `/testbed`，不表示所有镜像本来都兼容该目录。

## 4. 共同构造协议

### 4.1 每次修改都有明确基线

```text
固定输入快照 → 角色读取/编辑 → 结构化产物与基线明确的 patch
                                      ↓
                               新鲜验证环境重放
                                      ↓
                              原始报告 → 准入决定
```

不能让上一角色、上一测试残留的工作区成为下一步的隐式输入。A/B 的 S 应由程序对完整态与问题态求差生成，不让模型另猜一份“等价恢复”。C 的 T/S 分别相对同一 base 生成，并检查测试代码与生产代码所有权。

### 4.2 阶段完成必须有证据

发布补充建议每阶段保存：输入 hash、镜像/commit、Prompt hash、角色和尝试号、输出 JSON、改动路径集合、命令/cwd、退出码、原始报告、接受/拒绝原因。

Agent 声称“通过”“覆盖充分”不是机器证据。宿主必须检查 JSON、路径范围、patch 可应用性、测试实际出现、报告健康、Nop/Oracle 结果。

角色权限必须分离：只读分析不改库；测试作者不实现功能；solution 作者不改测试；题面编辑不改变代码/答案/测试。原实现分别保存阶段状态，但没有统一的跨 pipeline 审计 schema。

## 5. 路线 A：从已有功能反向构造

### 5.1 状态图

```text
F：选定功能完整存在
 └─ A：消融生产实现，移走需隐藏的测试
      ↓
E：问题态
 ├─ 注入 T           → 目标红，回归绿
 └─ 应用 S，再注入 T → 目标绿，回归绿
```

F 只保证选定功能完整，不保证来源仓库没有其他原题缺陷。

### 5.2 逐阶段配方

| 阶段 | 操作 | 产物 | 放行条件 |
|---|---|---|---|
| A1 理解 | 查公共入口、实现、测试、原题禁区 | 架构与功能证据 | 功能真实存在且可测 |
| A2 选题 | 选可独立移除的行为，写契约 | candidate、路径/入口/测试线索 | 不重复原题，不依赖原 gold |
| A3 消融 | 删除或 stub 实现，保留必要 API/类型骨架 | `ablation.patch` | patch 可应用；能构建并收集测试 |
| A4 求逆 | 在干净 F 应用消融，程序反向 diff | `solution.patch` | 非空、可应用于 E、不夹带隐藏测试 |
| A5 测试 | 选择测试来源，生成 T、运行计划与 ID | test.patch、F2P/P2P、评分配置 | ID 来自真实报告 |
| A6 内检 | 执行 E+T 与 E+S+T | selfcheck、报告 | 满足严格红绿和回归门槛 |
| A7 打包 | 固定 E、清理可见历史、生成任务文件 | task 目录 | 环境与 gold/test 隔离 |
| A8 外检 | 用目标 Harbor 跑 Nop/Oracle | 平台报告 | 捕获、verifier 与评分闭环成立 |

副本先提议最多约 2N 个候选，再从 complete 候选中按预计生产文件数偏大者选 N 个，同分稳定排序。虽然有 `--max-modified-files` 且帮助文案称其为限制，不能据此将当前选择器理解为严格文件数硬上限；需核对候选与实际补丁。

### 5.3 首次试验优先 existing 模式

1. 在 F 上确认目标既有测试能运行并通过，保存原生 ID。
2. 明确隐藏测试文件集合；消融 patch 必须将选定文件整文件删除，不能改动其他测试。
3. 同时消融生产实现。仅删除测试而功能仍在，不是一道有效任务。
4. S 只恢复允许求解者恢复的生产等内容，T 单独恢复隐藏测试。
5. E+T 中目标测试必须以真实 failed 行出现。缺符号导致包编译失败、collection error 或没有报告，不满足这一模式。
6. E+S+T 中目标与回归均通过；从 reporter 读取规范 ID，必要时对齐后重新验证两态。

尤其在 Go/Rust 等编译型仓库中，应保留公开类型、函数声明和模块骨架，否则删除功能会使整个测试二进制无法构建，失去行为级红灯。

默认 `generate` 模式让测试作者观察完整功能，新增隐藏测试与 test plan；`reuse-original` 则复用来源题的 tests。原 tests 是否区分新消融，必须靠执行证明，不能只因它来自已验证任务就跳过检查。

### 5.4 求逆不是反转整个补丁

对生产/合法恢复路径集合 C：

```text
E = apply(F, A)
S = binary_safe_diff(E[C], F[C])
T = 把隐藏测试安装到 E 的补丁
assert apply(E, S)[C] == F[C]
```

副本用反向 Git diff 实现。若直接反转整个 A，会把测试也塞进 solution，破坏隐藏测试隔离。文档等非测试内容是否恢复也要明确归属。

### 5.5 副本实际门槛与不足

接受要求：消融态所有 F2P 明确失败；P2P 非空且两态均通过；恢复态全部 F2P 通过；Nop=0、Oracle=1；verifier 包装流程及其他 selfcheck 条件正常。

测试命令返回非零可以是预期红灯；verifier 包装器仍应正常完成并记录结果。两类退出码不可混用。

当前主路径传给 selfcheck 的 `leak_probes` 为空，所以 `leak_clean` 不证明做过充分泄漏扫描。配置中的评分/LOC 阈值及 `stability_repeats=3` 也不能仅凭存在就当作已执行门禁。

accepted 与 rejected 分别进入 `output_task`、`failed_output_task` 等位置。进程结束不等于产出 accepted。外层 Docker 验证独立于内部 selfcheck；`run-task-folder` 默认 `--validate none`，验收时应明确启用或标注尚未完成。

## 6. 路线 B：抽取参考题机制，再演化新题

### 6.1 先重建 Base，再做 specialization

```text
F0：来源问题态
 ├─ 原 gold + 原 tests → F → 加边界测试 → F′
 │                                      └─ mask → E1 → 求逆/分测
 │                                                     → Base Goldcheck
 │
 └─ 仅原 tests scaffold + 新计划 → 新实现与测试 → F*
                                                └─ mask → E* → 求逆/分测
                                                               → Goldcheck → 改写题面
```

Specialize 分支从 F0 加参考测试 scaffold 出发，**不是直接在已完整恢复的参考 gold 上继续堆功能**。参考答案作为规划证据与起始代码状态是两件事。

### 6.2 Base 重建配方

1. 固定 F0 的镜像、repo root 与真实 Git HEAD。
2. Understand 只读参考题、gold、tests、评分配置与可选 hardness，输出困难机制、实现区域和测试机会。
3. 应用参考 S/T，恢复完整参考态 F。
4. 只增加测试和必要 runner 接入，形成 F′；输出 `test_paths`、`added_test_node_ids`、命令和覆盖理由。
5. 追踪 verifier 实际命令/cwd；新增测试必须在真实报告中出现并通过。写了文件但 runner 没选中，不算测试。
6. Mask 删除选定实现和验收测试，形成 E1；保留无关行为与可构建性，同时写自洽题面。
7. 程序反转 mask 并拆 code/test/doc patch；solution 为 code 加允许恢复的 doc，T 单独保存；实现要求 code/test 部分均非空。
8. 执行 E1+T 与 E1+S+T，派生 F2P/P2P，并区分历史失败与新增失败。
9. 打包后使用产物中的 test.sh/grader/config 进行 Goldcheck。Base 通过才进入演化。

测试 ID 必须来自 reporter，不应靠源码函数名猜测。实现支持部分 ID 对齐，但必须能唯一定位，不能用模糊匹配扩大验收白名单。

### 6.3 演化角色的输入输出

| 角色 | 职责与约束 | 输出内容 |
|---|---|---|
| Plan | 用参考难点和仓库证据提出不同的新需求 | surface_gap、transferred_mechanisms、plan、implementation_areas、test_plan、non_goals |
| Evolve | 在 F0+测试 scaffold 上完成新实现与测试，不削弱旧测试 | summary、implemented_behaviors、test_node_ids、test_paths、commands_run、known_limits |
| Mask | 在 F* 中只遮蔽新机制及验收测试，保留 API 骨架 | summary、removed_implementation_areas、removed_test_node_ids、preserved_api、instruction |
| 系统 | 固定 E*，求逆拆补丁、执行、打包 | S/T、报告、F2P/P2P、任务包 |
| Rewrite | 只改题面组织与文风，不改语义或资产 | instruction，可选 rewrite_notes |

迁移应保留“难点结构”，不是替换名词。例如参考题的优先级/冲突机制可以启发另一种层级覆盖需求，但必须证明仓库确有相应扩展点。

新测试要接入原 verifier 命令。优先写现有测试文件；新增文件时同步注册 runner。`test_paths` 只能声明测试拥有的路径，不能把生产实现混入隐藏测试。

### 6.4 红绿与修复规则

副本 F2P：after passed、before 非 passed，包含 before missing；P2P 为两边 passed。但 before 仍需真实测试结果，包装流程和 patch 应用必须健康。

未改变的历史红灯不必然阻断；新增或要求执行的测试失败则阻断。因此 Goldcheck 证明的是**所选契约**，不是整仓库全测试绿。

默认要求 base 全红、gold 全绿，near-miss 默认关闭。开启后需真正可应用、可执行的错误答案，且至少一个 F2P 未通过。只有 hardness 标记不够。

Goldcheck 使用导出 verifier 文件在容器中验证，但不等于完整 Harbor solver 生命周期；还要外检提交捕获、隔离和平台集成。

相关内容修复循环默认初次尝试加最多 3 次修复；基础设施重试耗尽应退出，不用改需求来掩盖。不是所有阶段都在同一个自动修复循环内，不能假设最终 Base Goldcheck 会自动回到合适角色修复。

Rewrite 有结构/长度等检查，但没有充分的语义等价门禁。发布补充要求改写前后逐条对齐需求—测试矩阵，不能因为 gold 没变就认为题面也没变。

## 7. 路线 C：新需求、测试先行、实现后写

### 7.1 从仓库到 query

```text
仓库清单 → 分层/组件证据 → 判断适用类型 → 按类型挖 seed
 → 每 seed 产出 0 或 1 个 query → 契约/路径核验与诊断 → query pool
```

逐步操作：

1. 建立包含生产入口、模块、测试、配置和依赖的清单，避免仅依据目录头部几个文件。
2. 用稳定 layer/component ID 关联真实路径和符号，区分仓库自己、外部标准、依赖库所拥有的契约。
3. 选择结构来源：`agent` 做语义分析，`call_graph` 程序投影，`hybrid` 程序证据加语义补全。Python 部分动态证据是真实调用边；其他语言 coverage 不等于 caller→callee。
4. 从 runtime、language、schema、query、network、ui、workflow、analysis、performance 九类判断适用性，不强迫每类出题。
5. seed 至少有不同角色的仓库证据支撑，例如公共入口与底层机制；没有机会就返回 0。
6. query 定义新行为、接口、错误、边界、兼容性和非目标；不擅自改变仓库不拥有的外部标准。
7. 宿主检查路径/架构引用和契约归属；保存 raw/accepted 与拒绝理由。

当前配置每 repo raw ceiling 55、每类型 seed 上限 12，是上限而非产量保证。4 层/4 组件/6 生产文件等复杂度指标在 `enforce_complexity_thresholds=false` 时不是硬门禁；重复分数、family representative 和不少难度指标也是报告建议。query pool 不是经过最终困难度和重复筛选的 benchmark。

### 7.2 测试作者的真实交接接口

每条 query 有版本 v；需求语义一旦变化，应增加 v，旧 T/S 不再自动有效。

Testcase Author 只改测试和测试拥有的 fixture，不能实现功能；在旧代码上产生如下接口：

```json
{
  "summary": "目标行为及为何应在旧代码失败",
  "test_paths": ["tests/test_new_behavior.py"],
  "target_test_command": ["python", "-m", "pytest", "tests/test_new_behavior.py"],
  "native_test_command": ["python", "-m", "pytest", "tests/test_existing_behavior.py"],
  "mutation_command": [],
  "patch": "完整 git diff --binary HEAD 输出"
}
```

这是形状示例，不是可直接应用的测试。command 必须是 argv 数组，不能是 shell 字符串或 `bash -c`。实际 argv 必须来自仓库；空 mutation 数组表示没有该可执行检查。

宿主校验 patch 非空、改动路径与声明一致且属于测试、命令结构合法。旧代码运行目标测试后，由只读 Failure Critic 返回：

```json
{
  "verdict": "valid",
  "failure_matches_query": true,
  "reason": "断言观察到旧行为，而非题面要求的新行为",
  "evidence": ["具体测试 ID、日志、actual/expected"]
}
```

其他 verdict 为 `testcase` 或 `query`，分别修测试或题面。缺依赖、import/collection 错误、fixture 错误和超时都不能冒充行为红灯。

### 7.3 实现作者的真实交接接口

Solution Author 在同一 base 上修改生产实现与必要配置/文档，可见题面与 testcase，但不能改测试或 hard-code 其输入：

```json
{
  "summary": "实现与兼容性说明",
  "build_command": ["仓库实际构建程序", "参数"],
  "patch": "完整 git diff --binary HEAD 输出"
}
```

没有 build 时返回空数组；副本将此项跳过视为通过，审计时必须区分“执行通过”和“不适用/未执行”。

### 7.4 六项交叉执行

每项从受控干净状态开始，不能继承角色自行测试的残留：

| 项 | 代码状态 | 执行对象 | 期望 |
|---|---|---|---|
| C1 | base | native regression | pass |
| C2 | base + T | target tests | fail，且 critic 认定为目标行为失败 |
| C3 | base + S | build | pass，或明确未提供 |
| C4 | base + S | native regression | pass |
| C5 | base + S + T | target tests | pass |
| C6 | base + S + T | native regression | pass |

C6 检验测试补丁是否污染回归，不能由 C4 替代。native 命令由 Agent 给出，可能只覆盖子集，不能天然称作全仓库 suite。

### 7.5 失败路由和质量标记

- Q 自相矛盾/缺关键约定：修 Q，版本加一，重新生成 T/S。
- 断言、fixture、测试选择或 argv 错误：修 T，重新检查相关矩阵。
- 实现缺失/兼容性破坏/build 失败：修 S，再做交叉执行。
- 原生基线不健康、无法归因于生成测试设置：infra/manual review，不修改需求掩盖。
- 未知失败或预算用尽：保存证据进入 manual review，不冒充成功。

默认修复总预算 3，CLI 允许 2 或 3，三条修复路线共享，不是各自有 3 次。

Strength review 与可选 mutation 会产生 `strength_gate_passed`、mutation 状态和 `fully_covered` 等标记。副本主要把它们作为软标记，**不能假设 strength failed 一定阻断导出**；要求 mutation 的配置也不能直接等价为所有路径都硬拒绝缺失项。

发布补充：显式制定这些标记的接受策略。无 mutation 可分档，但不能声称已有 mutation 证据；最好保存若干具体错误实现被测试拒绝的证据。

### 7.6 三层产物与隐含依赖

```text
output/query_pool/QUERY_RUN/repo/candidate/
    query.json + instruction.md

output/instance_pool/CASE_RUN/repo/candidate/
    instance.json + validation.json + query.json + instruction.md
    test.patch / testcase.patch + solution.patch

最终 task 目录/
    task.toml + environment + tests + solution + 捕获脚本
```

case synthesis 还会读取 `output/runs/QUERY_RUN/campaign_config.json` 来恢复仓库来源。因此不能单独搬走 query 目录；需保留该元数据及相对布局，或明确重写加载接口。

## 8. 一个贯穿三条路线的微型实例

以下是**人为构造的说明例子，不是副本内真实任务或实测结果**。

假设配置库提供 `load_config(path)`，要支持父配置继承：子级覆盖父级，多级继承稳定，循环引用返回确定错误，无父配置时旧行为不变。

| 需求 | 目标测试 | 应被拒绝的错误实现 |
|---|---|---|
| R1 单层合并 | `test_parent_merge` | 忽略 parent |
| R2 子级覆盖 | `test_child_override` | 合并顺序反转 |
| R3 多级继承 | `test_multilevel` | 只处理一层 |
| R4 循环报错 | `test_cycle_error` | 无限递归或静默截断 |
| R5 普通加载不变 | `test_plain_config`，P2P | 无条件改变默认路径 |

A 输入已经有 R1—R4，消融继承实现、隐藏测试，S 恢复原实现。B 从参考题抽取类似优先级机制，在 F0 上完成新功能和测试，再 mask 求逆。C 先以仓库证据提出需求，写红灯测试，再写新实现。

最终都要得到逐 ID 的记录：

```text
test_parent_merge:   base=failed gold=passed → F2P
test_child_override: base=failed gold=passed → F2P
test_multilevel:     base=failed gold=passed → F2P
test_cycle_error:    base=failed gold=passed → F2P
test_plain_config:   base=passed gold=passed → P2P
```

若 cycle 测试在 gold 报告中根本没出现，不能凭其他三项通过接受 R4；若题面不定义循环语义，测试强加一个精确错误约定也不公平，应修正契约。

## 9. 单题运行手册

### 9.1 存储与环境先决条件

当前节点临时盘只有 6 GiB。未来运行前必须检查节点 `/`、持久卷，以及**实际 Docker daemon 所在机器**的 data-root 与空间；远端 Docker 存储不由本地 TMPDIR 控制。

将输出、cache、临时目录放在独立持久路径，不混入取证源码。使用既有兼容环境，不把 `uv run` 隐式建环境当 smoke test。安装、构建/拉取镜像、可能大量写入的测试须先估算并获得授权。不要直接 source 旧 `env.local.sh` 或批次/fleet 脚本，它们可能携带历史凭据、机器地址和大规模并发。

以下只设置路径，不会创建目录或准备资产；操作者应先按容量计划创建所需目录：

```bash
export DS_ROOT=/volume/pt-coder/users/mlei/factory_ref/ds
export REPRO_ROOT=/volume/pt-coder/users/mlei/deepswe_repro/pilot_001
export TMPDIR="$REPRO_ROOT/tmp"
export UV_CACHE_DIR="$REPRO_ROOT/cache/uv"
export PIP_CACHE_DIR="$REPRO_ROOT/cache/pip"
export XDG_CACHE_HOME="$REPRO_ROOT/cache/xdg"
export PYTHONDONTWRITEBYTECODE=1
```

另设 `REPRO_PYTHON` 为已有解释器绝对路径、`HARBOR_ROOT` 为已准备的 Harbor、`SOURCE_TASKS` 为输入目录、`SOURCE_TASK_ID` 为选中任务。模型凭据通过环境安全注入，不写入文档和配置快照。

### 9.2 A：一个来源、一个候选

A 使用源码根 `pipeline.config.yaml`，没有 B/C 那样的 `--config` 参数。优先用环境覆盖输出/runtime 路径；若需改 YAML，在单独运行副本修改，保留取证源码。

```bash
cd "$DS_ROOT/deepswe_ablation_pipeline"
export PIPELINE_RUNS_DIR="$REPRO_ROOT/ablation/runs"
export PIPELINE_OUTPUT_DIR="$REPRO_ROOT/ablation/outputs"
export PIPELINE_HARBOR_TASKS_DIR="$REPRO_ROOT/ablation/harbor_tasks"
export HARBOR_VENV_PYTHON="$REPRO_PYTHON"

"$REPRO_PYTHON" -m pipeline run-task-folder \
  --tasks-dir "$SOURCE_TASKS" --only "$SOURCE_TASK_ID" \
  --n-candidates 1 --case-concurrency 1 --candidate-workers 1 \
  --use-existing-tests --validate docker
```

还需设置 `MODEL_NAME`、endpoint、offline agent/compose 等实际参数。该命令不是只读：会请求模型、启动/构建容器、写出任务；Docker 验证包含外层 Nop/Oracle。改用 `none` 可以先只构造，但必须标注外层尚未验收。

通过标准：实际 accepted 任务存在，F2P/P2P 非空、严格红绿成立、外层 Nop=0/Oracle=1、报告齐全。0 个 accepted 不能用进程结束代替。

### 9.3 B：准备单题配置

以 `configs/default.toml` 为**完整模板**准备 `$REPRO_ROOT/specialize.pilot.toml`，检查并替换：

| 配置 | 单题设置 |
|---|---|
| `input.tasks_dir` | 完整参考 tasks 的绝对路径 |
| `input.hardness_dir` | 实际可用路径；没有就不声称有 near-miss |
| `harbor.root/claude_offline_dir` | 已准备 runtime |
| `harbor.reuse_pipeline_root` | 本地 C 副本绝对路径 |
| `runner.model/openai_base_url/openai_api_key_env` | 本次服务参数；无明文 key |
| `images.repo_root` | 实测根目录，不盲用 `/app` |
| `scheduler.pair_repeat/parallel` | 1，固定 seed |
| `specialize.specialize_repeat` | 1 |
| `output.root` | 独立持久输出路径 |

TOML 中的 `$DS_ROOT` 等不应假设会自动展开；写入真实绝对路径。不要保留旧机器相邻目录假设。

```bash
cd "$DS_ROOT/deepswe_specialize_pipeline"
"$REPRO_PYTHON" -m specialize_pipeline \
  --config "$REPRO_ROOT/specialize.pilot.toml" validate-config
"$REPRO_PYTHON" -m specialize_pipeline \
  --config "$REPRO_ROOT/specialize.pilot.toml" discover

"$REPRO_PYTHON" -m specialize_pipeline \
  --config "$REPRO_ROOT/specialize.pilot.toml" run \
  --run-id pilot-specialize-001 --runner harbor-claude \
  --only-task "$SOURCE_TASK_ID" \
  --limit 1 --pair-repeat 1 --specialize-repeat 1 --parallel 1

"$REPRO_PYTHON" -m specialize_pipeline \
  --config "$REPRO_ROOT/specialize.pilot.toml" summarize \
  --run-id pilot-specialize-001
```

`validate-config --runtime` 会额外验证运行环境，不是纯文本检查；`fake` 只用于编排测试，不能证明生成了真实实例。

逐层核查输出：

```text
output/datasets/RUN/                       # Base
output/specialized/datasets/RUN/           # 演化任务
output/deepswe_specialize/datasets/RUN/     # 改写后的最终任务
output/runs/RUN/summary.json
output/runs/RUN/cases/                     # 阶段证据
```

CLI 的成功退出条件以 Base 完成情况为核心，不保证所有 specialization 成功；必须分别核查 Base、specialized、final 数量和 Goldcheck 状态。

### 9.4 C：新 repo 来源、一个 query、一个 instance

已有来源 tasks 可跳过转换；否则按第 3 节准备 metadata：

```bash
cd "$DS_ROOT/deepswe_query_pipeline_testcase_solution"
"$REPRO_PYTHON" scripts/repo_images_to_harbor_sources.py \
  --metadata-root "$REPRO_ROOT/inputs/repo_metadata" \
  --language python --limit 1 --out "$REPRO_ROOT/source_tasks" \
  --remote-images-root "$REMOTE_IMAGE_ROOT" \
  --image-namespace "$IMAGE_NAMESPACE"
```

REMOTE_IMAGE_ROOT 是 Docker 主机能访问的 archive 根，不一定是当前节点路径。转换仅生成来源描述，不准备镜像与依赖。

以 `query.config.yaml` 为完整模板准备 `$REPRO_ROOT/query.pilot.yaml`：替换旧 endpoint 和 Harbor/offline/compose 路径；taxonomy 使用本副本绝对路径；`output.root` 设为 `$REPRO_ROOT/query/output` 的实际路径；明确模型和资源，各级并发先设 1。

首次可用 `analysis.deepwiki_source=agent` 减少调用图相关测试成本；若复现当前默认结构证据则用 hybrid 并准备额外成本。设 `generation.drop_last_unfinished_type=0`，避免按完成时间取消最后类型带来比较困难。这些 pilot 变更要记录，不声称完全照搬当前默认配置。

```bash
"$REPRO_PYTHON" -m query_pipeline \
  --config "$REPRO_ROOT/query.pilot.yaml" validate-config
"$REPRO_PYTHON" -m query_pipeline \
  --config "$REPRO_ROOT/query.pilot.yaml" discover \
  --tasks-dir "$REPRO_ROOT/source_tasks" --limit-repos 1

"$REPRO_PYTHON" -m query_pipeline \
  --config "$REPRO_ROOT/query.pilot.yaml" run-task-folder \
  --tasks-dir "$REPRO_ROOT/source_tasks" \
  --runner harbor-claude --run-id pilot-query-001 \
  --limit-repos 1 --raw-target 1 --max-seeds-per-type 1 \
  --seed-miner-concurrency 1 --proposer-concurrency 1 \
  --repo-concurrency 1 --deepwiki-source agent

"$REPRO_PYTHON" -m query_pipeline \
  --config "$REPRO_ROOT/query.pilot.yaml" run-case-synthesis \
  --pool "$REPRO_ROOT/query/output/query_pool/pilot-query-001" \
  --runner harbor-claude --run-id pilot-case-001 \
  --limit 1 --case-concurrency 1 --max-repair-rounds 3
```

raw target 1 允许生成 0，不强行凑题；也不代表全流程仅调用一次模型。单题验收需要 accepted query、完整 instance、六项执行证据与质量标记处理结果；然后还要导出 task 并外检。

## 10. 包装、评分与最终平台验证

### 10.1 必须重建的评分流程

```text
solver 在 E 上修改
 → 捕获 model.patch
 → verifier 在干净 E 应用 model.patch
 → 确保隐藏测试路径不受模型答案控制
 → 注入 T → 执行真实命令 → 原生报告
 → 按真实 ID 解析 → F2P/P2P → reward
```

不能让 solver 修改 grader、覆盖 T 或削弱断言获得奖励。A 标准 grader 会处理隐藏测试路径复位/注入，missing/skipped 按未通过，重复 ID 取较差结果。重建系统需明确采用同等保护。

报告适配必须读取真实 pytest/JUnit、CTRF、Go、Rust/JS runner 输出，不能根据白名单合成“已执行”结果。记录原始输出、退出码和报告缺失原因，防止 parser 宽松行为造成假通过。

### 10.2 C 的导出器不是等价替代品

| 脚本 | 用途 | 复现限制 |
|---|---|---|
| `pool_to_harbor.py` | query 包装 | 不表示已有测试和 gold |
| `export_instances_to_harbor.py` | 较早的命令级评分导出 | F2P/P2P 字段不等于逐测试比率；需核验 gold 等资产 |
| `export_pool_to_vanilla_tasks.py` | 派生真实 ID、生成任务、可自检 | 需要外部 reference；不是通用多语言或完整 Harbor E2E |

第三个脚本派生 ID 时追加 `--junitxml`，应首先用于兼容 pytest 的测试入口，不直接套到任意 Go/Rust/JS 命令。

必须补齐 `VANILLA_REFERENCE_TASK`，其中 `tests/grader.py` 和 `tests/test.sh` 提供共享框架。外部 reference 不在这三个副本内；A 也有 grader 并不证明能无损替换。

```bash
cd "$DS_ROOT/deepswe_query_pipeline_testcase_solution"
"$REPRO_PYTHON" scripts/export_pool_to_vanilla_tasks.py \
  --instances-root "$REPRO_ROOT/query/output/instance_pool" \
  --out "$REPRO_ROOT/exported_tasks" --run-id pilot-case-001 \
  --vanilla-ref "$VANILLA_REFERENCE_TASK" --repo-dir /testbed \
  --only "$CANDIDATE_ID" --limit 1 --jobs 1 --self-check
```

`--instances-root` 传 instance_pool 总根：脚本搜索 `*/*/*/instance.json`，传入某个 CASE_RUN 会少一层。`--run-id` 是输出任务 ID 前缀，不过滤输入，需用 `--only` 和隔离 pilot 根避免混入其他批次。

该脚本按 base SHA 前 8 位匹配镜像清单，必须额外核对完整 SHA 和镜像身份。派生要求 F2P 存在，但不能据此假设 P2P 非空。self-check 失败仍可能留下任务目录，只发布通过报告明确接受的条目。

### 10.3 外层验收还要验证提交捕获

至少在目标平台进行：

1. Nop：不修改，reward=0，verifier 正常执行。
2. Oracle：按平台实际 gold 提交方式应用 S，reward=1。
3. 可控修改：确认新文件、新分支、commit 在实际生命周期中被捕获，patch 不丢失也不夹入缓存垃圾。

本副本不含完整 Harbor 版本，无法给出所有部署通用且已验证的外层命令。A 已有 `--validate docker` 入口；B/C 要在补齐目标 Harbor 后冻结其真实命令与报告格式，不能虚构已测通的一键命令。

题面要求新建分支并提交时尤其要实测：问题镜像可能被 squash/orphan 为新 root commit，不能把上游 SHA 当 verifier 当前 Git 基线；不同导出器的 pre_artifacts/start 脚本也不等价。这些错误会让正确答案变成空 model.patch。

## 11. 验收与发布规则

### 11.1 分层声明

| 等级 | 必须有的证据 | 可以声称 |
|---|---|---|
| L0 | 固定仓库/镜像、原生测试可运行 | 输入可用 |
| L1 | 需求有仓库证据、无明显矛盾 | 有效 query |
| L2 | S/T 可应用，内部矩阵和报告检查通过 | 内部有效 instance |
| L3 | 最终包 Nop=0/Oracle=1、捕获与隔离正常 | 指定平台可回放的任务 |
| L4 | 语义、泄漏、强度、稳定性、去重/划分检查 | 符合声明发布策略的 benchmark 条目 |

该分级为本文建议，不是源码现成状态枚举。

### 11.2 发布补充清单

- [ ] 冻结输入与产物后，不调用 Agent 也能回放。
- [ ] F2P 非空，失败由需求相关断言触发，不是编译/依赖错误。
- [ ] P2P 非空，注明全量 suite 或所选子集。
- [ ] 每个需求有实际执行的断言，测试不强加题面未定义的行为。
- [ ] solver 无法访问 gold、隐藏测试、构造日志、备份、答案相关挂载。
- [ ] 检查可访问 Git refs/对象；明确 Docker 历史层、镜像存储等访问假设，Git gc 不等于擦除所有镜像层。
- [ ] 保存具体错误实现被拒绝的证据，或标记 near-miss/mutation 未执行。
- [ ] 干净环境重复 Nop/Oracle，固定种子/时区等；建议至少 3 次并真实执行，而非只读取配置中的 3。
- [ ] 改写前后需求—测试矩阵一致。
- [ ] 同 repo、参考题、seed family 的近重复按 group 划分，避免跨 train/test 泄漏。
- [ ] 许可、私有数据与凭据经过检查；轨迹不带明文 key。

部分检查需要额外数据/平台权限。本次没有执行，不将其标为通过。

### 11.3 单实例复现记录

建议统一保存以下结构。它是推荐的发布 schema，不是现有 CLI 可直接读取的输入：

```json
{
  "instance_id": "pilot-example-001",
  "route": "ablation",
  "source": {
    "repo": "owner/repo",
    "upstream_commit": "FULL_SHA",
    "problem_state_git_head": "ACTUAL_IMAGE_HEAD",
    "image_digest": "RECORDED_DIGEST",
    "repo_root": "/testbed"
  },
  "construction": {
    "source_snapshot_hash": "HASH", "config_hash": "HASH",
    "prompt_hashes": {}, "model": "RECORDED_MODEL",
    "parent_reference_id": null, "query_version": 1, "repair_count": 0
  },
  "artifacts": {
    "instruction_sha256": "HASH", "solution_patch_sha256": "HASH",
    "test_patch_sha256": "HASH", "verifier_bundle_sha256": "HASH"
  },
  "verification": {
    "fail_to_pass": ["NATIVE_REPORT_ID"],
    "pass_to_pass": ["OTHER_NATIVE_REPORT_ID"],
    "base_missing_ids": [], "nop_reward": 0, "oracle_reward": 1,
    "outer_harbor_verified": false, "near_miss_status": "not_run",
    "repeat_count": 1, "evidence_dir": "audit/"
  },
  "release": {
    "level": "L2", "accepted": false,
    "pending": ["outer Harbor verification", "release checks"]
  }
}
```

未知项使用 unknown/not_run/null，不推测填充。模型名相同不证明权重相同，镜像 tag 相同不证明环境相同。

## 12. 故障诊断与恢复

| 现象 | 优先判断 | 下一步 |
|---|---|---|
| 原生 suite 无法启动 | runtime/依赖/目录错误 | 修环境，不改需求掩盖 |
| patch apply 失败 | base 不同或 patch 损坏 | 核对完整 SHA、镜像与基线 |
| 目标 base 已绿 | 消融不充分、需求已存在或测试弱 | 修候选/消融/测试，不篡改预期结果造红灯 |
| base 没有测试报告 | 编译/collection/runner 错误 | 恢复可测试性，不直接计为 F2P |
| gold 的新 ID missing | 未接入 runner 或命名错误 | 查实际 argv 与报告，修可达性 |
| target 绿而 regression 红 | 实现破坏兼容性或测试污染 | 比较矩阵，修实现/fixture 隔离 |
| 内检过、外层 Oracle 失败 | 打包、根目录、Git 基线、捕获问题 | 检查实际 model.patch 和 verifier 日志 |
| rewrite 增删需求 | 题面漂移 | 撤回该改写产物，重新对齐契约 |

恢复约束：

- A 的 `--resume` 接受旧 runs 路径，不是布尔开关；保留阶段目录并核对 schema。
- B 的 `--resume` 配合同 run ID；`--redo-from specialize/rewrite` 可保留 Base，Base 链的 redo 会重建整条 Base 链，不是只跑一个小步骤。
- C 的 query/case 各有 run ID；role cache 与 completed case 跳过逻辑不同，已完成 case 不保证重新验证所有新输入 hash。
- 修改 base、Prompt、题面语义、测试命令或重要配置，优先新 run ID 和独立输出，防止证据混用。
- 失败目录保留用于诊断；不能因含 task.toml 就并入发布集合。
- 清理镜像、容器、cache 和旧任务是独立的有风险写操作，恢复执行不等于授权清理环境。

## 13. 扩大规模与统计口径

建议顺序：先 A-existing 跑通一题；完成最终平台 Nop/Oracle 与捕获验证；再按目标选择 B 的一条 Base+一次演化或 C 的一条 query+case；各语言分别验报告/导出；最后小批多 repo，再提高并发。A 是学习建议，不是 B/C 技术依赖。

至少分别统计：输入 repo、raw candidate、accepted query、内部有效 instance、导出 task、外层通过 task、发布接受 task。每级记录分母和拒绝原因。

```text
生成产率 = accepted query / raw candidate
构造成功率 = L2 instance / 进入 case synthesis 的候选
包装成功率 = L3 task / 尝试导出的 L2 instance
发布产率 = L4 task / 原始候选
```

同时记录模型调用/token、容器墙钟、测试次数、修复轮数、镜像/轨迹/日志大小。副本不足以给出实际成本、产率或吞吐。

repo、type miner、proposer、case 等多级并发可能相乘，默认批次配置不能直接作为首轮启动参数；`storage_mb` 是声明，不代表节点有相应真实容量。

难度分数不是难度证据。要发布 benchmark，还需冻结求解模型与协议，进行多次求解实验并报告通过率；出题模型给的复杂度标签不能替代外部校准。这属于发布补充，本次未执行。

## 14. 不复用旧工程时的最小重建方案

### 14.1 六类接口

下面是语义接口，不是旧包已有 API：

```text
Environment.create(snapshot) -> isolated_workspace
Environment.exec(workspace, argv, cwd, env, timeout) -> exit/logs/artifacts
Role.run(role_spec, inputs, workspace) -> structured_output + captured_diff
Patch.check/apply(base, patch) -> status
Patch.reverse/split(complete, problem, ownership) -> solution/test/doc
Test.run(state, test_plan) -> native_report + execution_health
Test.parse(native_report) -> {test_id: status}
Gate.evaluate(before, after, contract) -> accepted/rejected + reasons
Pack.emit(instance, platform_profile) -> task_bundle
Pack.verify(task_bundle, nop/oracle) -> platform_evidence
```

关键约束是状态不串、角色不能绕过改动范围检查、结果来自真实执行、打包后重新验证。

### 14.2 最小实现次序

1. 先做环境快照、patch 应用与报告解析，用人工 S/T 跑通 Nop/Oracle。
2. 实现 A 的确定性求逆和测试隔离，加入一个消融角色，即可形成最小反向构造器。
3. 加 Q/T/S 角色与六项执行矩阵，形成最小正向构造器。
4. 加失败路由与有限预算修复，记录 query version 与每次尝试。
5. 再加架构分析、taxonomy/seed 和 specialization；它们提高候选来源，不替代底层验收。
6. 最后加 cache、resume、调度和多机，先稳定单题契约再优化吞吐。

### 14.3 Prompt 复现原则

尽量一致的工艺复现应直接复用副本模板，记录模板 hash、变量与最终渲染文本；仅复用角色名字或本文摘要不保证相同输出分布。

重写 Prompt 时必须包含：当前代码状态、可见输入、允许修改范围、禁止行为、执行检查、输出 schema、输出路径和失败报告。源码与其他角色输出都是任务数据，不得覆盖角色权限。

Git 权限也不同：C 的 testcase/solution 要求 `git add -N` 捕获新文件；B 的相关角色禁止自行使用 git，由 harness 捕获 diff。求解者题面的 branch/commit 指令不适用于构造角色。

## 15. 尚需外部补齐的复现缺口

| 缺口 | 当前可确认 | 运行前动作 |
|---|---|---|
| Harbor 精确版本与本地改造 | 三条路线依赖它，B 复用 C 适配器 | 提供固定 checkout/环境并验单题 |
| 原始 tasks 和镜像 | 可恢复资产/字段契约 | 提供合法可访问的任务及不可变镜像 |
| 模型服务与权重版本 | 有历史配置，不能证明当前可用 | 指定实际服务、记录差异，不照抄旧 endpoint |
| Vanilla reference | C 的较新导出器显式依赖 | 提供参考 verifier 或开发并验收替代导出器 |
| 多语言最终打包 | 部分构造与 verifier 支持多语言 | 各框架独立验报告/包，不能外推 pytest 模板 |
| 官方制作过程 | 当前是本地衍生工艺 | 不宣称官方复刻，另需官方材料 |
| 历史成本/产率/质量 | 没有完整生产数据日志 | 用新 pilot 测量或补齐历史证据 |

因此本手册可指导重建和准备执行，但不承诺仅靠三个源码目录离线一键复刻原始 DeepSWE 数据集。

## 附录：复用资产与证据索引

| 契约/步骤 | 本地入口 |
|---|---|
| A 角色模板 | [prompts](deepswe_ablation_pipeline/prompts/)：deepwiki、propose、ablate、testcase、leakage |
| A 构造/候选选择 | [driver.py](deepswe_ablation_pipeline/pipeline/driver.py)、[candidates.py](deepswe_ablation_pipeline/pipeline/candidates.py) |
| A 接受标准 | [selfcheck.py](deepswe_ablation_pipeline/pipeline/construction/selfcheck.py)、[validate.py](deepswe_ablation_pipeline/pipeline/stages/validate.py) |
| A 包装/运行 | [templates](deepswe_ablation_pipeline/templates/)、[CLI](deepswe_ablation_pipeline/pipeline/cli.py)、[配置](deepswe_ablation_pipeline/pipeline.config.yaml) |
| B 输入协议 | [dataset.py](deepswe_specialize_pipeline/specialize_pipeline/dataset.py)、[contracts.py](deepswe_specialize_pipeline/specialize_pipeline/contracts.py) |
| B 角色模板 | [prompts](deepswe_specialize_pipeline/prompts/)：understand、extract_add_tests、extract_mask、specialize_plan、specialize_evolve、specialize_mask、rewrite_instruction |
| B 阶段/Goldcheck | [orchestrator.py](deepswe_specialize_pipeline/specialize_pipeline/orchestrator.py)、[stage_goldcheck.py](deepswe_specialize_pipeline/specialize_pipeline/stage_goldcheck.py)、[gates.py](deepswe_specialize_pipeline/specialize_pipeline/gates.py) |
| B 测试可达性 | [verifier_trace.py](deepswe_specialize_pipeline/specialize_pipeline/verifier_trace.py)、[reachability.py](deepswe_specialize_pipeline/specialize_pipeline/reachability.py) |
| B 打包/配置 | [emit_harbor.py](deepswe_specialize_pipeline/specialize_pipeline/emit_harbor.py)、[CLI](deepswe_specialize_pipeline/specialize_pipeline/cli.py)、[default.toml](deepswe_specialize_pipeline/configs/default.toml) |
| C 来源转换 | [repo_images_to_harbor_sources.py](deepswe_query_pipeline_testcase_solution/scripts/repo_images_to_harbor_sources.py)、[sources.py](deepswe_query_pipeline_testcase_solution/query_pipeline/sources.py) |
| C 角色/类型 | [prompts](deepswe_query_pipeline_testcase_solution/prompts/)、[taxonomy](deepswe_query_pipeline_testcase_solution/taxonomy/deepswe_task_types.runtime.v2.yaml) |
| C query/执行矩阵 | [stages.py](deepswe_query_pipeline_testcase_solution/query_pipeline/stages.py)、[case_pipeline.py](deepswe_query_pipeline_testcase_solution/query_pipeline/case_pipeline.py) |
| C 运行/导出 | [CLI](deepswe_query_pipeline_testcase_solution/query_pipeline/cli.py)、[query.config.yaml](deepswe_query_pipeline_testcase_solution/query.config.yaml)、[export_pool_to_vanilla_tasks.py](deepswe_query_pipeline_testcase_solution/scripts/export_pool_to_vanilla_tasks.py) |

原 [DEEPSWE_PIPELINE_ANALYSIS.md](DEEPSWE_PIPELINE_ANALYSIS.md) 保留为实现审阅和风险分析的辅助材料；面向复现，以本文的输入、步骤、命令模板和验收协议为主。
