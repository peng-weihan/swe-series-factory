# 项目协作约定

本文件适用于项目根目录及其子目录。默认使用中文沟通；代码标识符沿用现有英文命名。用户在当前任务中的明确指令优先于本文件。

## 项目目标与入口

本项目依据三份规格构造 SWE benchmark instances。区分 instance 构造、求解 rollout、验证器执行和最终接收，不把某一阶段成功视为整条流水线成功。

开始工作时先阅读 `README.md`；涉及 DeepSWE 或 ProgramBench 时，再阅读 `docs/factory_reproduction.md` 和相关规格章节。文档中的历史测试数量、环境可用性和完成状态需要结合当前代码与产物核实。

| 路径 | 职责 |
| --- | --- |
| `nl2repo_factory/` | 仓库获取、AST、题面生成、打包、Docker Oracle、受限修复和事务恢复 |
| `deepswe_factory/` | ablation / query / specialize 三条路线、红绿矩阵、打包和独立 grader |
| `programbench_factory/` | CLI 构建、字节探测、测试 QC、独立镜像重建和交付 |
| `swe_factory/` | 共享源码快照、执行器、角色接口、配置和原生报告解析 |
| `tests/` | 基于 `unittest` 的项目测试，包括适配器及宿主人工示例 |
| `examples/` | 配置与候选输入示例；真实运行前替换占位值 |
| `scripts/verify_factories.py` | 测试及原始规格完整性验证，输出机器可读记录 |
| `runs/` | 运行产物、报告和历史证据 |

## 原始规格与历史证据

- `nl2repo.md`、`deepswe.md`、`programbench.md` 是只读原始规格。除非用户明确要求修改原件，否则不得修改内容、解除只读权限或替换文件；设计补充写入 `docs/` 或另建副本。
- 不通过更改 `scripts/verify_factories.py` 中的基线摘要来掩盖规格变化。发现摘要或权限不符时报告实际差异。
- 修改实际源码目录，不修改 `build/`、`*.egg-info/`、`.venv/` 或既有 `runs/` 中的副本来实现修复。
- 每次 demo、验证和真实运行使用尚不存在的新输出目录，例如 `runs/<用途>-<时间戳>`。保留失败日志、原始报告和中间产物，不覆盖或删除旧证据。
- 本地配置从示例复制为 `examples/*.local.toml`。凭据使用现有环境变量接口，不写入配置、日志、文档或版本控制。

## 实现约定

- Python 最低版本为 3.11；优先使用现有 `.venv/bin/python`。主体目前仅依赖标准库，新依赖需有明确必要性并同步声明。
- 沿用现有模块分工、`pathlib.Path`、类型标注、错误类型与 JSON 报告约定。保持改动聚焦，不顺带重构无关模块。
- 共用执行、快照、报告或角色逻辑优先放在 `swe_factory/`；NL2Repo 的既有接口按实际依赖关系维护，避免为了统一结构扩大改动。
- 变更 CLI、配置字段、角色响应、状态或产物格式时，同时检查调用方、示例、prompts、文档和相关测试。
- 执行命令使用已有 argv 接口；不得为方便而放宽路径越界、符号链接、输出覆盖、超时、所有权或报告完整性检查。
- 保持串行运行及事务语义；不得默认多个控制器可以安全写入同一任务，也不得宣称 checkpoint 已支持整条流水线自动续跑。

## 执行与验收边界

- `backend="host"` 是非隔离模式，仅用于可信人工示例和本地验证。真实候选按配置使用 Docker；Docker 不可用时记录阻塞，不静默降级为 host。
- 外部 Agent CLI 的权限由其部署环境决定，controller 的 diff 检查不能作为进程沙箱证据。保持 Docker 断网及资源限制，只清理本次创建的资源。
- 修改评分器、修复逻辑或质量门禁时，明确生产代码、候选答案、可信测试和 grader 的边界。不得通过删除必需测试、降低分母或恢复参考生产代码使错误答案通过。
- 评分依据实际执行的原生报告。缺失报告、缺失必需测试、超时、collection error、skip 或自称成功不能当作通过；重复测试 ID 按现有较差状态规则处理。
- 保留源码 revision、运行配置、prompt/产物摘要、命令、退出码、原始报告和状态之间的关联。快照可能不保留 `.git`，不得假设执行工作区能获取 Git HEAD。
- Oracle 满分加空答案负对照只能证明当前答案与测试的有限一致性。涉及质量门禁时，按改动风险增加 near-miss、mutation、伪 gold 或 grader 篡改等有针对性的反例。
- NL2Repo 成功需核对 `status=accepted`；DeepSWE 核对实际 level、`accepted` 和外部 Harbor 验证记录；ProgramBench 核对 `status=published`、level 及 R3 证据。目录存在、CLI 退出 0、`qc_passed` 或 `built_unverified` 均不能代替接收。
- 分别记录是否调用真实模型、执行真实 Docker 和验证目标 Harbor。宿主示例、mock 适配器和本地验证不能证明这些外部环节已完成。

## 常用验证命令

在项目根目录执行，输出目录名仅为示例，使用前确保它不存在：

```bash
# 针对改动模块运行测试；替换文件名
.venv/bin/python -m unittest discover -s tests -p 'test_factory_shared.py'

# 全量项目测试
.venv/bin/python -m unittest discover -s tests

# 留存全量测试与三份只读规格的完整性报告
.venv/bin/python scripts/verify_factories.py --output runs/factories-verification-<时间戳>

# 宿主人工示例，按改动范围选用
.venv/bin/python -m nl2repo_factory demo --output runs/tagkit-demo-<时间戳>
.venv/bin/python -m deepswe_factory demo --route all --output runs/deepswe-demo-<时间戳>
.venv/bin/python -m programbench_factory demo --output runs/programbench-demo-<时间戳>
```

示例命令中的 `<时间戳>` 必须替换后再执行。Demo 需要 pytest，ProgramBench 示例还需要 `cc`；先核实工具是否可用。新环境安装方式见 `README.md`。

- 先运行与改动直接相关的测试；涉及共享执行、报告、打包、事务或验收逻辑时运行全量验证并检查 `verification.json` 与日志。
- Demo 按相关链路选择；仅修改文档时检查内容、路径和命令，不要求重跑整个测试集。
- 不硬编码历史测试数量作为当前完成依据。受环境影响未执行的验证要明确列出，不能记为通过。

## 交付要求

汇报说明改了什么、为什么、实际验证结果及剩余限制，并给出关键文件或证据路径。尚未验证的部分直接说明。

执行 Git 操作前确认项目是否已初始化；不得因目录含 `.gitignore` 而假定它是 Git 仓库。初始化、提交和推送按用户任务范围处理。
