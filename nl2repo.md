# NL2Repo instance 构造：逆向复现规格

版本：1.0，2026-09-16。

本文的目的不是解释某个类或函数，而是让另一位工程师能够重新实现一条流水线：输入一个 Python 仓库，输出一个让求解模型仅凭自然语言从零实现项目、并可自动评分的 instance。

依据仅为本目录中的静态代码、提示词和测试文件。没有运行构造流水线、Docker、模型或 Harbor，也没有读取原始目录、候选数据、生成数据和外部运行器。本文本身是本次工作的唯一新增交付物；原有代码不作修改。

## 1. 复现对象与完成标准

### 1.1 要复现的核心方法

这是一种“从可运行的参考项目，反向提炼可实现的自然语言规格”的任务合成方法：

1. 找到一个能够在离线容器内通过原有测试的 Python 项目。
2. 从测试、文档、公开接口及实现中提取项目行为，生成足以从空目录重建项目的 start.md。
3. 保留依赖环境，移除学生可见环境中的目标项目实现。
4. 将原有测试和必要资源制作成私有验证器。
5. 将原项目快照制作成参考答案，通过同一个验证器验证 instance 自洽。
6. 如果参考答案不能得满分，在冻结题意和测试的前提下修复环境、打包或参考答案，再验证。

这里的“NL2Repo”不是给已有仓库补一个补丁。求解模型的初始工作区只提供 start.md，目标是创建整个项目。参考答案不是另一个模型生成的完整项目，而是源仓库快照。

### 1.2 三种不同的“复现成功”

| 层次 | 交付要求 | 本文能支持什么 |
| --- | --- | --- |
| 方法复现 | 独立实现上述构造方法，产物能够交给隔离运行器求解和评分 | 给出流程、数据契约、生成任务书、门禁、操作顺序 |
| 行为兼容复现 | 在相同中间输入上，ID、筛选、测试打包、评分、修复判定与当前副本一致 | 给出确定性算法与兼容性用例，保留原有特殊行为 |
| 原批次精确重放 | 得到与历史运行相同的仓库版本、模型输出、镜像、样本数量和成功率 | 无法据此保证：缺少原数据、提交版本、镜像摘要、模型服务快照、外部运行器版本及实际运行证据 |

本文默认目标是前两层，而不是宣称能恢复历史数据集。LLM 生成阶段也没有可保证字节级确定性的条件。

### 1.3 本文的规则标记

- **[基线]**：副本中能够恢复的现有行为；做兼容复现时应对齐。
- **[生成要求]**：提供给模型的任务约束，不代表程序完整验证了它。
- **[复现约定]**：为了使独立实现可操作，本文显式补充的接口、记录或验收方式；不是声称原系统已有。
- **[增强项]**：改变基线的可选改良，不要与兼容复现结果混用。
- **[待确认]**：副本没有足够证据，应在实际接入时验证。

### 1.4 单条 instance 的成功判据

基线的终点是：结构与 API 覆盖检查通过、镜像泄漏检查通过、标准 Oracle 返回至少 0.999999 的奖励。经过受限修复后达到该条件也算成功。

它不等价于：

- start.md 已通过独立人员的可实现性评审；
- 普通求解模型已经成功重建项目；
- 空答案必然零分；
- 所有潜在源码泄漏都已被排除；
- 测试集合覆盖了项目全部行为。

这些更强结论需要额外实验，不能由 Oracle 得满分推导出来。

## 2. 产物和可见性：先定义要交付什么

### 2.1 最终任务目录

以下是逻辑上的必要结构。报告及中间证据可以另外保存，不改变任务本体。

~~~text
<dataset_root>/<task_id>/
├── instruction.md
├── task.toml
├── environment/
│   ├── Dockerfile
│   ├── start.md
│   └── api_manifest.json
├── solution/
│   ├── solve.sh
│   └── oracle/                  # 源仓库快照，参考答案
└── tests/
    ├── test.sh                  # 测试恢复、执行、计分入口
    ├── config.json
    └── reference/               # 原始测试、配置及列出的 fixture

<image_root>/<task_id>.tar         # Docker 镜像归档，task.toml 指向它
~~~

各路径的实际位置应以打包器为准；独立实现应将这种布局作为显式输出契约，而不是依赖操作者手工复制。

### 2.2 四种视角

| 使用者/阶段 | 可以读取 | 不应向其暴露 |
| --- | --- | --- |
| 环境构造 Agent（A1） | 完整仓库、测试、依赖定义、构建和测试结果 | 与当前任务无关的宿主数据、凭据 |
| 规格生成 Agent（A2） | 完整仓库、测试、文档、AST、A1 环境记录 | 与当前任务无关的数据 |
| 求解 Agent（学生） | 通用 instruction、/workspace/start.md、已预置的依赖环境 | solution/oracle、私有测试、原项目实现 |
| 验证器/Oracle | 待验证工作区、私有测试和配置；Oracle 另有参考答案 | 不应把这些私有资产提前交给学生 |

**[待确认]** 学生与私有资产的隔离、资产何时挂载，属于外部运行器的职责。任务目录把 solution 和 tests 分开，并不能单独证明运行期隔离成立。必须在运行器接入验收中检查。

### 2.3 学生收到的任务

instruction.md 使用通用任务指令，要求遵循工作区 start.md 实现整个项目、直接在当前目录运行、满足 API Usage Guide，并禁止抓取外部信息和安装依赖。基线原文如下：

~~~text
According to the start.md in the workspace, implement the entire project as per the requirements specified in the document, ensuring that the final product can be directly run in the current directory. The running requirements should comply with the <API Usage Guide> section of the document.
Note that all required dependencies have already been pre-configured in the local environment. You are strictly prohibited from fetching external information or dependencies. Do not use commands such as git clone, pip install, curl, wget, or similar tools. Please complete this task step by step.
~~~

start.md 承担项目特有语义；instruction.md 不承担补充隐藏行为的职责。

## 3. 实现边界与运行前准备

### 3.1 将系统拆成五个可替换部件

| 部件 | 最小职责 | 返回内容 |
| --- | --- | --- |
| 仓库获取器 | 把输入 URL 获取到任务私有目录 | 仓库路径、获取结果 |
| 工具型模型适配器 | 给模型任务书和文件工具，收集文件改动及最终 JSON | 最终文本/JSON、轨迹、退出与超时信息 |
| 容器适配器 | 构建、离线测试、导出镜像、检查文件系统 | 镜像引用、日志、退出码、归档路径 |
| 确定性构造器 | AST、文档门禁、打包、评分脚本、状态管理 | 中间记录和任务目录 |
| Oracle 运行器 | 按任务格式运行参考答案，再运行正式验证器 | 数值奖励或缺失值、日志、报告 |

基线由本地控制器经 SSH 使用远端文件系统和 Docker，模型也在远端以工具型 CLI 运行。方法复现可以把它们放在一台机器上，但这属于部署替换；不能因此假定所有路径和外部 CLI 选项仍然成立。

模型不是一个只返回自然语言答案的 completion API：它需要读取实际文件、调用 shell、写文件、观察错误并迭代。替换模型服务时应保留这种能力。

### 3.2 先填写路径表，不直接使用复制来的启动脚本

**[复现约定]** 将以下目录全部设置在自己的持久卷下；不要沿用副本中启动脚本的远端根目录和凭据。当前分析没有创建这些目录，也没有访问任何远端。

| 配置项 | 作用 | 约束 |
| --- | --- | --- |
| input_jsonl | 候选仓库记录 | 只读；明确来源 |
| work_root | 每任务阶段记录、下载的 AST、修复证据 | 本次复现专属 |
| run_root | 队列/运行事件与 checkpoint | 本次运行专属 |
| dataset_root | 最终任务目录 | 不与历史有效产物混用 |
| remote_work_root | 源仓库、A1/A2 生成文件、临时构建材料 | 仅指向授权主机与目录 |
| remote_image_root | 导出的镜像 tar | 预算包括旧/新镜像并存 |
| trajectory_root | 模型过程记录 | 视作可能包含源码的私有数据 |
| cache_root、temp_root | 包缓存、构建临时数据、修复临时目录 | 持久卷；按适配器设置缓存和 TMPDIR |

Docker 自身的数据根目录也必须核实。仅设置 TMPDIR 不会改变 Docker layer 存储位置。实施时先估算镜像、仓库副本和修复备份的峰值，再取得执行批准。

### 3.3 必须先解决的外部依赖

1. 可访问候选仓库及其依赖，且有权使用和打包这些内容。
2. Docker 及足够的磁盘、CPU、内存；Python 3.11 AST 扫描容器可取得或预置。
3. 可调用的工具型模型服务与 CLI。基线默认模型字符串只是历史配置标签，不表示今天一定可用。
4. 能理解任务布局及镜像归档的 Harbor 运行器。该运行器代码没有随副本提供。
5. 环境构造时可获取依赖，但正式测试必须离线；学生阶段同样不应下载依赖。

仓库筛选的上游采集方式不在副本内。本文从“已有 JSONL 候选记录”开始，不虚构 GitHub 检索条件、许可证过滤或排名策略。

## 4. 输入、身份与一次构造的状态

### 4.1 输入 JSONL 契约

每个非空行是一个 JSON 对象，例如：

~~~json
{"full_name": "example/tagkit"}
~~~

[基线] 取第一个非空字段：url → html_url → clone_url → full_name。没有可用 URL 的记录丢弃；空行跳过；不去重。不保证 GitHub API 返回的 url 字段会被进一步转换为浏览器/克隆 URL。

URL 归一化顺序：

1. 去除首尾空白。
2. 符合 owner/repo 简写正则的字符串，前置 https://github.com/。
3. git@github.com:owner/repo 转 HTTPS；这个分支移除末尾 .git。
4. 其他字符串只移除末尾 .git。

注意顺序：owner/repo.git 命中简写分支后直接返回，不再执行第四步。不要把“更统一的 URL 规范化”悄悄当成基线。

### 4.2 task_id 算法

~~~text
clean = normalize_repo_url(url).rstrip("/")

如果能匹配 github.com/<owner>/<repo>：
    base = "github-" + owner + "-" + repo
否则：
    base = URL 中连续非字母数字替换为 "-"，去边界 "-"，截前 80 字符
    空串时使用 "repo"

base = 将不在 [a-zA-Z0-9._-] 的字符替换为 "-"
base = 去边界 "-"，转小写
digest = SHA1(clean 的 UTF-8 字节) 的前 7 个十六进制字符
task_id = base + "__" + digest
如配置后缀：清理后缀两端的 "-"、"_"，再附加 "-" + suffix
~~~

哈希对象是 URL，不是 commit、仓库内容或生成规格。相同 URL 的不同提交会复用身份。大小写可能影响 digest，即使可读 base 已转小写。

[复现约定] 另外记录 commit SHA、输入文件摘要、模型/提示词版本、容器镜像摘要和运行器版本，作为复现实验账本；不要为兼容模式擅自改 task_id 算法。

### 4.3 范围选择

先解析并保留有效记录，再按 [start_index, end_index) 切片；end_index <= 0 或超过长度则取到末尾。随后按需反转，最后应用正数 limit。索引是过滤后的列表索引，不能当作原 JSONL 的物理行号。

### 4.4 主状态机

~~~text
候选
  │
  ▼
A1：构建环境并离线测试 ──失败──► 终止/记失败
  │ passed
  ▼
AST：提取结构和测试导入 ──实现 .py 少于 5──► filtered
  │ passed
  ▼
A2：生成规格 → 覆盖/结构门禁 ──失败──► 带反馈重写 A2
  │
  ▼
打包 + 最终镜像泄漏门禁
  ├──已知镜像内容泄漏──► 回 A1，随后重新 AST、A2
  ├──其他可重试错误────► 回 A2/本阶段
  ▼
正式 Oracle
  ├──基础设施瞬态错误──► 有限重试
  ├──reward >= 0.999999 ─► 接收 instance
  └──任务失败──────────► 受限修复 → 验证补丁 → 事务式提交 → Oracle 复查
                                                  ├─通过：接收
                                                  └─失败：回滚，再修复或终止
~~~

基线允许跳过 Oracle，但这种结果只能记作“已构造、未做正式 Oracle 验收”，不应和验收成功的数据混在一起。

## 5. 阶段 A1：把参考仓库变成可信的离线环境

### 5.1 输入与输出

输入：仓库 URL、任务私有目录、镜像名称、可选镜像泄漏反馈。

输出的关键不是一段环境说明，而是四项互相一致的东西：

1. 完整参考仓库。
2. 可供后续剥离源码的基础镜像。
3. 真实执行过的离线测试结果。
4. 足以重新构建和打包测试的结构化环境记录。

### 5.2 给环境 Agent 的任务书

以下任务书是基于原提示词整理的独立实施版本，不承诺逐字相同的模型输出：

> 在任务目录获取指定仓库，检查已有 Dockerfile、Python 版本、依赖及测试入口。已有 Dockerfile 可用时优先使用，否则构造适合项目的环境。依赖安装、项目安装及测试必须在 Docker 内进行。尽可能运行原项目广泛而有意义的测试集合，不通过删除测试或吞掉错误取得成功。
>
> 优先以 editable 方式安装项目并执行测试；项目不支持时可使用普通安装。测试之后移除目标项目安装及相应源码关联，保留第三方依赖，使后续工作区实现能够被验证。最终必须用新容器在断网条件下跑非空测试，记录收集数、通过数、失败数、错误数、退出码与日志。
>
> 测试容器使用 --network none、--cpus 4、--pids-limit 512。依赖外部服务、在线 API、GUI 或无法离线运行的项目不能声称通过。列出运行测试所需的包配置、测试路径和 fixture 路径。清理本任务创建的容器，保留基础镜像。输出 JSON，不用口头成功替代证据。

**重要区别**：CPU/PID、广泛测试、正确移除项目安装等是生成要求；基线 JSON 门禁并不会逐一验证全部实际行为。

原提示词存在“移除目标项目安装”和个别泄漏反馈措辞的冲突。独立实现应明确选择“保留依赖、移除目标实现”的语义；这是符合总体目标的复现约定，不是声称原模型总会如此执行。

### 5.3 A1 结果契约

以下为贯穿示例中的示意响应；命令和版本不是对真实仓库的执行记录：

~~~json
{
  "status": "agent1_passed",
  "docker": {
    "image": "teamfactory-agent1-<task-id>",
    "container": "teamfactory-agent1-<task-id>-ctr",
    "dockerfile_path": "generated/Dockerfile",
    "used_existing_dockerfile": false
  },
  "env_spec": {
    "base_image": "python:3.11-slim",
    "python_version": "3.11",
    "install_commands": ["python -m pip install pytest", "python -m pip install -e ."],
    "test_commands": ["python -m pytest -q"],
    "package_files": ["pyproject.toml"],
    "test_files": ["tests/test_api.py"],
    "fixture_files": [],
    "uninstalled_packages": ["tagkit"],
    "env_notes": []
  },
  "commands": {
    "clone": "<repository acquisition command>",
    "docker_build": "<recorded reproducible build command>",
    "oracle_run": "docker run --network none --cpus 4 --pids-limit 512 <image> <offline-test-command>",
    "cleanup": "<task-container cleanup command>"
  },
  "oracle_report": {
    "ok": true,
    "returncode": 0,
    "passed": 4,
    "failed": 0,
    "errors": 0,
    "collected": 4,
    "network_none": true,
    "stdout_tail": "4 passed"
  },
  "notes": "Illustrative contract, not an observed run."
}
~~~

门禁至少检查：

- env_spec、oracle_report、docker、commands 都是对象。
- install_commands、test_commands 是数组；test_commands 非空。即使声明失败，也需要符合相应结构。
- 声明通过时：ok=true、returncode=0、failed=0、errors=0。
- collected 与 passed 都是大于 0 的整数，不能是布尔值。
- test_files 非空、network_none=true。
- oracle_run 字符串包含符合正则的 --network none 或 --network=none。

这是一套“模型报告的结构与一致性检查”，不是独立重放测试。正式独立验证要等下游 Oracle。

可恢复的传输/超时/JSON 类错误按瞬态重试预算处理，默认额外重试 1 次；结构不合法与模型明确声明失败应区分记录。保存 schema_version=teamfactory.agent1.v1 的阶段记录，并保留轨迹及原始日志。

## 6. 确定性阶段：从仓库提取结构证据

### 6.1 扫描环境与排除集

[基线] 使用 Python 3.11 AST；仓库只读挂载为 /repo，结果写到独立输出目录。按相对路径排序扫描 .py。

任何路径组成部分属于下列集合则排除：

~~~text
.git __pycache__ .pytest_cache .mypy_cache .ruff_cache .tox
.venv venv env build dist node_modules
~~~

Python 3.11 无法解析的语法记入 parse_errors；存在语法错误不自动使整个阶段失败。没有编译、导入仓库模块，也不会通过执行动态代码补全 API。

### 6.2 两套“测试文件”识别，不要混为一谈

用于 AST 测试导入和测试计数的规则：

- 路径包含 tests 或 test 目录；
- 或文件名以 test_ 开头；
- 或以 _test.py 结尾；
- 或恰好是 tests.py。

用于剔除实现目录树中的测试资产，规则更宽：对路径组成部分转小写，识别 test、tests、testing、conftest.py、tests.py、test_ 前缀、_test.py 后缀，以及把下划线按连字符分词后的 test/tests/testing token。

因此，“没有进入实现树”与“被当作测试执行证据”不是同一个集合。

### 6.3 模块、API 和导入

模块名由相对 .py 路径转点分名称；__init__.py 对应父目录。不会专门去掉 src/ 布局前缀。

提取内容：

| 对象 | 记录内容 |
| --- | --- |
| 顶层公开函数/异步函数 | 名称、全限定名、模块、起止行、签名、docstring、装饰器、是否 async |
| 返回证据 | 返回注解、有无裸 return/带值 return、最多 12 个返回表达式摘要 |
| 异常/调用证据 | 最多 30 个 raise、80 个调用摘要；长表达式截断 |
| 顶层公开类 | 名称、全限定名、模块、行号、基类、装饰器、docstring、直接定义的公开方法 |
| 测试导入 | 测试中绝对 import/from-import 所引用的本仓库模块和符号、测试路径、行号、语句 |

名称以下划线开头的顶层函数/类及方法不纳入公开 API 列表。因此构造器和 dunder 行为需要 A2 回看源文件/测试补充。类变量也不是专门抽取的独立表。

对于测试导入：

- import M 形成 M。
- from M import X 形成 M.X；星号导入保留 M.*。
- 只保留命中仓库模块清单或其顶层模块根的导入。
- 忽略相对导入，不进行运行时追踪。
- 按导入类型、符号、测试路径去重。
- required_api_symbols 是最终符号的排序去重集合。

公开类/函数清单也可能包含测试文件中的公开定义；不能直接把整个清单照抄为学生 API。

### 6.4 输出与筛选

AST payload 的 schema_version 为 teamfactory.stage2_ast_payload.v1，关键字段如下：

~~~text
repo_root
project_import_roots, project_modules
project_tree, implementation_tree
implementation_python_files, python_files
public_classes, public_functions
test_imported_repo_symbols, required_api_symbols
test_case_count, parse_errors, summary
~~~

project_tree 和 implementation_tree 各含 entries、entry_count、truncated。entry 包含 path、type、bytes；目录 bytes=0，每棵树最多 5000 项。

[基线] implementation_python_file_count 来自被截断后的 implementation_tree 中 .py 文件条目数量。少于 5 时，返回 stage2_filtered，原因 implementation_python_file_count_lt_5；否则通过。它不是代码行数、复杂度或 API 数量门槛。

test_case_count 是测试文件中顶层 test* 函数和直接定义的 test* 类方法的近似计数，不展开 parametrization，也不等于 pytest 实际 collection 数。

外层阶段记录 schema_version=teamfactory.stage2_ast.v1，携带摘要及完整 payload。不要仅保留摘要而丢失后续 A2 必须读取的详细证据。


## 7. 阶段 A2：把参考项目转成自然语言规格

### 7.1 生成的实质：恢复行为，不是翻译代码

A2 应回答：“不知道原实现的人，需要知道哪些公开行为，才能重新实现并通过原测试？”输出不是文件注释合集，也不是原项目 README 的改写。

建议按以下工作次序实施，其中证据优先级和文档约束来自原任务书：

1. 读测试与 fixture：找输入域、返回形状、异常、状态变化、文件/CLI 协议、异步规则和边界值。
2. 读 README/文档：恢复项目目的、典型使用流程和用户术语。
3. 读导出接口及包元数据：固定导入路径、名称、调用签名和依赖。
4. 读 AST：核对公开 API 和必选的测试导入符号，不凭 AST 推断全部语义。
5. 按需读实现：解释测试未明说的参数默认值、容错、顺序、数据结构、状态和协议。
6. 把上述证据组织成学生可用的功能规格和例子，不复制实现函数体、测试函数体或断言代码。

[复现约定] 独立实现可以维护一张内部“证据 → 规格”工作表，帮助生成和审查，但不要把私有测试路径或测试代码塞进 start.md：

| 内部证据 | 应提炼的内容 | 写入位置 |
| --- | --- | --- |
| 测试导入 pkg.foo | 导入路径与符号必须存在 | Module Import、对应 Core API |
| 参数化测试的多个输入 | 输入域及边界规则，而非逐条照抄断言 | 行为 Node |
| 异常断言 | 异常类型、触发条件；有证据时保留消息契约 | API、行为 Node |
| fixture 文件结构 | 文件格式、字段、读取/输出约定 | 行为 Node |
| 对象多步调用 | 状态机、操作顺序与副作用 | Usage Example、行为 Node |
| packaging 元数据 | Python/依赖要求、必要布局 | Environment、Architecture |

### 7.2 给生成 Agent 的输入

提供仓库目录、完整 AST 文件、A1 环境规格和离线测试报告，并允许读源文件和测试。不能只传几条 API 摘要，然后期待它恢复完整行为。

[基线] 快速上下文包含：

- 全部 required_api_symbols；
- 最多 120 条测试导入记录；
- 最多 12 个公开类、20 个公开函数样例；
- 实现目录树、AST 摘要；
- 上一次覆盖失败报告及重试次数；
- api_budget=min(60, max(20, 公开类数+公开函数数))。

这一 JSON 文本在提示词中截到前 24000 个字符，可能截断为不完整 JSON。完整 AST 文件仍可读取。方法复现应提供完整证据文件；若改成结构化限长上下文，注明属于增强项。

### 7.3 API 选取规则

[生成要求]

- 测试显式导入的本仓库符号全部必须覆盖。
- 重要 API 不超过 60 时尽量完整；超过 60 时按测试、文档、公开导出、类结构及调用关系的重要性选取最多 60 个核心条目。
- 类及其方法作为一个条目；独立函数单独列。
- 文档包含构造器、相关变量和成员约定，必要时从源码补充 AST 没有提取的部分。
- Core API 中可给签名、类型及说明，不给可直接复制的完整实现。
- 所有导入路径、大小写、参数顺序、默认值、同步/异步属性应来自证据。

[待确认] 若必选符号过多、无法在 60 个核心条目内表达，原任务书没有给出完整冲突解决机制。不要擅自漏掉必选符号并声称符合原方法；应显式标记冲突，或将“放宽预算”作为单独版本。

### 7.4 start.md 的模板契约

以下为格式模板，尖括号内容必须用证据填实；模板不是可直接提交的实例：

~~~text
## <Project> Project Introduction and Goals
<项目解决什么问题，完整实现应具备什么能力>

## Natural Language Instructions (Prompt)
<从零实现的明确任务，不要求学生获取原仓库>

## Environment Configuration
All implementation paths must be relative to /workspace. Never create, write, or use /testbed.

### Python Version
<实际环境 Python 版本>

### Core Dependency Library Versions
<实际环境中已配置的关键依赖及有依据的版本>

## <Project> Project Architecture

### Project Directory Structure
<一个 text fenced code block；根是 workspace/，采用树形连接符，
只包含学生需要实现的项目文件，不含测试/验证器/内部辅助资产>

## API Usage Guide

### Core API

#### 1. Module Import
<所有关键 import 的准确写法>

#### 2. <API name> - <summary>
**Function**:
<作用>
**Function Signature**: 或 **Class Definition**:
<签名或类接口，不带实现体>
**Parameter Description**:
<类型、默认值、限制>
**Returns**:
<返回结构、语义；必要时说明异常>

<更多 API 条目>

## Usage Example
<可理解的项目使用流程及期望输出，不调用外部资源>

## Detailed Function Implementation Nodes

### Node 1: <一个内聚的行为主题>
**Function Description**:
<本组功能的外部可观察语义>
**Handling Strategy**:
<分支、顺序、状态、边界、错误处理等规则>
**Input and Output Examples**:
<有证据支持的具体输入输出>

### Node 2: <下一组行为>
<同样三个字段>
~~~

行为节点从 1 连续编号，不固定节点总数，也不是“一 API 强制一 Node”。可将多个 API 的共同解析协议放在同一节点，也可将一个复杂类的生命周期拆成多个节点。

每个节点只使用上述三个字段；在有证据时给 2–4 个具体例子，兼顾常规输入和边界/异常，不靠“正确处理错误”“确保兼容”等空话充数。不要把未经证据支持的理想行为强加给项目。

Core API 不使用单数的 **Input and Output Example** 字段。不要任意新增自由格式章节。实现树中不得出现 tests、conftest、验证器或测试辅助资产。

### 7.5 api_manifest.json 与最终响应

manifest 是覆盖声明，不是另一份实现。示例：

~~~json
{
  "schema_version": "teamfactory.api_manifest.v1",
  "covered_symbols": [
    "tagkit.normalize_tags",
    "tagkit.errors.InvalidTagInput"
  ],
  "core_apis": [
    {
      "name": "normalize_tags",
      "qualified_name": "tagkit.normalize_tags",
      "kind": "function",
      "start_md_heading": "#### 2. normalize_tags - Normalize comma-separated tags",
      "covered_import_symbols": ["tagkit.normalize_tags"]
    },
    {
      "name": "InvalidTagInput",
      "qualified_name": "tagkit.errors.InvalidTagInput",
      "kind": "class",
      "start_md_heading": "#### 3. InvalidTagInput - Invalid input type",
      "covered_import_symbols": ["tagkit.errors.InvalidTagInput"]
    }
  ],
  "notes": "Illustrative manifest."
}
~~~

最终模型响应：

~~~json
{
  "status": "stage3_passed",
  "project_name": "TagKit",
  "start_md_path": "stage3/start.md",
  "api_manifest_path": "stage3/api_manifest.json",
  "notes": "Illustrative response."
}
~~~

这些路径指向任务目录内的生成文件。基线解析器接受绝对路径，但没有据此实现强目录隔离；独立实现应拒绝越界路径，作为安全增强项。

基线还允许一种恢复：最终响应解析失败，但默认位置的两个文件存在且非空时，构造一个通过的候选响应，再执行后续校验。不能把“响应 JSON 解析失败”一概等同于“文件不可用”。

### 7.6 硬门禁：到底检查什么

API 覆盖按下列算法判定，不是语义理解：

1. 从 manifest 的 covered_symbols，以及每个 core_apis 的 name、qualified_name、covered_import_symbols 取字符串。
2. 对每个符号生成别名：完整名、最后一段、最后两段。
3. 必选符号的别名与 manifest 别名有交集，视为覆盖。
4. 否则，只要任何别名以 token 边界方式出现在 start.md 任意位置，也视为覆盖。
5. 所有必选符号通过，才过门禁；必选集合为空则自然通过。

因此，“manifest 写了名字但文档没说明行为”“两个模块有同名函数”都可能被宽松规则误判为覆盖。这是基线兼容行为，不要将通过门禁解释成规格完整。

其他默认门禁：

- 存在形如 ### Node N: 标题的行为节点，非空且从 1 连续编号；若模型报告 node_count，必须一致。
- 下载文档后，清理目录树中的测试条目，检查没有测试树泄漏。
- 文档实现树内至少出现 5 个 Python 文件条目。这检查的是文本树，不是学生已经写了 5 个文件。
- 可选严格样式检查默认关闭。其部分字段要求与提示词存在不一致，见第 16 节。

覆盖失败反馈必须包括缺失符号和相应测试导入来源，再让 A2 更新两份文件。默认覆盖重试预算为 4。不能只让模型修改 manifest 数组而不重新检查文档内容；虽然宽松基线未完全阻止这种行为，复现时仍应保留完整审查证据。

## 8. 打包：把规格、参考答案、测试和环境分离

### 8.1 执行顺序

[基线] 顺序很重要：

1. 确认 A1 基础镜像可用；找不到时尝试使用 A1 记录的构建命令恢复。
2. 将当前参考仓库打包，排除 .git、__pycache__、*.pyc 及 pytest/mypy/ruff 缓存。
3. 用远端生成的 start.md 制作最终环境镜像，保存 tar，执行泄漏检查。
4. 下载参考仓库归档、start.md、manifest。
5. 在本地清理 start.md 的测试目录树，执行文本树与可选样式检查。
6. 创建任务目录；解包参考仓库至 solution/oracle；写环境文件、instruction、solve.sh、测试和 task.toml。

原实现会删除已存在的同名任务目录再创建。因此首次复现应使用全新 dataset_root。不要把重新运行当作无副作用的验证操作。

注意第 3 步和第 5 步：如果本地清理确实改变了 start.md，镜像中的文档可能不同于 environment/start.md。兼容复现应记录这一行为；工程修正版应先定稿，再构建镜像，并比较两份文档摘要，但这改变了原顺序。

### 8.2 环境镜像构造语义

最终镜像以 A1 基础镜像为 FROM，切换 root，执行以下容器内清理：

- 删除 /workspace、/testbed、/repo、/app、/src，重建 /workspace 与 /logs。
- 清理 /usr/local/lib 下的 *.egg-link、__editable__*。
- 删除 site-packages 中引用这些源码路径的 .pth。
- 拷入 start.md 到 /workspace/start.md，设置 WORKDIR=/workspace。
- 必要时追加删除已确认的本地安装 direct_url.json 元数据的指令。

这些删除只描述容器构建语义，不应在宿主机执行。本文没有执行这些命令。

最终镜像名来自 teamfactory-instance-<task_id>：转小写、非法字符替换为连字符、合并连续连字符并去边界。归档保存为 <remote_image_root>/<task_id>.tar。保存镜像和启动容器是不同操作，归档必须能被正式运行器加载。

### 8.3 参考答案

solution/oracle 放入仓库快照，solution/solve.sh 为：

~~~bash
#!/bin/bash
set -euo pipefail
cp -a /solution/oracle/. /workspace/
~~~

Oracle 的功能是把已知参考实现放进正式工作区，再走同一套验证路径，不是调用模型重写一次项目。这验证的是任务的环境/打包/测试闭环。

由于快照通常也有测试，验证器仍需要从私有 reference 覆盖恢复测试，不能信任求解工作区中的同名文件。

### 8.4 私有测试资产的选择算法

package_files：

1. 取 A1 显式列出的存在文件。
2. 补充仓库根部存在的 pyproject.toml、setup.py、setup.cfg、tox.ini、pytest.ini、requirements.txt、requirements-dev.txt。
3. 去重。

test_files：

1. 取 A1 显式列出的测试文件。
2. 合并 AST 的 python_files 中命中测试文件规则的路径。
3. 如果仍为空，再遍历仓库寻找匹配的 Python 测试。
4. 只保留存在路径并去重。

fixture_files 只来自 A1 显式清单，保留存在路径并去重；不会自动推导每一个测试读取的非 Python 文件。

对每个测试路径求 test_root：

~~~text
路径中第一次出现 tests 或 test：
    根 = 从开头到该目录的路径
否则文件名以 test 开头或恰为 tests.py：
    根 = 文件本身
否则有父目录：
    根 = 父目录
否则：
    根 = 文件本身
~~~

然后拷贝整个 test_root，再拷贝显式测试文件、package_files 和 fixture_files 到 tests/reference。拷贝整个根可以带上 conftest 和相邻资源，但不能覆盖所有仓库特有的数据依赖。

例：

| 测试路径 | 恢复根 |
| --- | --- |
| tests/test_api.py | tests |
| pkg/tests/unit/test_api.py | pkg/tests |
| test_api.py | test_api.py |
| pkg/api_test.py | pkg |

最后一种情况可能把生产代码随 reference 一起拷回工作区，是重要边界：基线“测试根”的启发式并不等于“仅测试资产”。做独立复现必须将它列入资产审查；若收窄复制范围，应记录为增强版本。

### 8.5 测试命令与分母

命令中已知构建源码根路径替换成 /workspace，包括 /workspace/repo、/tmp/build/repo、/opt/repo 和当前任务的 repo 路径；替换要求匹配路径/空白/结束边界。

如果测试根恰好只有一个、且不等于 test 或 tests，命令里独立的 tests 或 ./tests 会被改成该根。它不是通用 shell 重写器，不会可靠地理解所有引号、变量或自定义路径。

配置测试总数 N 优先取 A1 的正数 collected，否则用 AST 近似计数。不依据 public API 数量计分。

贯穿示例的 tests/config.json：

~~~json
{
  "pro_name": "TagKit",
  "test_case_count": 4,
  "test_commands": ["python -m pytest -q"],
  "test_files": ["tests/test_api.py"],
  "package_files": ["pyproject.toml"],
  "fixture_files": []
}
~~~

### 8.6 task.toml

示例中归档路径为占位符，实施时必须替换为运行器可访问的实际持久路径：

~~~toml
version = "1.0"

[metadata]
author_name = "TeamFactory"
author_email = "unknown"
difficulty = "medium"
category = "code-generation"
tags = ["code-generation", "0-to-1", "teamfactory", "python", "TagKit"]

[verifier]
timeout_sec = 1200.0

[agent]
timeout_sec = 3600.0

[environment]
build_timeout_sec = 1800.0
cpus = 2
memory_mb = 8192
storage_mb = 20480
docker_image_archive = "<remote_image_root>/<task_id>.tar"
~~~

difficulty=medium 是模板固定值，不是难度估计结果。注意最终任务 CPU=2，与 A1 测试时要求的 4 不同。

## 9. 验证器：必须精确复现的计分协议

### 9.1 运行协议

运行器准备：

- /workspace：学生或 Oracle 的项目；
- /tests：私有测试目录；
- /logs/verifier：可写日志目录。

test.sh 的 Python 逻辑依次执行：

1. 从 /tests/reference 恢复测试根。目标已存在时先移除，包括同名符号链接。
2. 覆盖拷贝 package_files 和显式 test_files。
3. 删除并恢复 fixture_files 对应目标。
4. 若命令涉及 /testbed 且它不存在，则建立指向 /workspace 的兼容符号链接。
5. 在 cwd=/workspace 中依次运行全部 test_commands，使用 shell=True，合并 stdout/stderr，设置 PYTHONUNBUFFERED=1。
6. 把命令、输出、退出码写入 test-stdout.txt。
7. 解析输出，写 report.json、reward.txt。

测试命令失败不会自动中止后续命令；单条命令没有这里定义的独立 timeout，依赖外层运行器限制。shell 的 set -e 不会把 Python 中非 check=True 的子进程错误自动变成失败。

恢复 package_files 也意味着学生自己写的同名包配置可能被覆盖。这是验证器协议的一部分，而不只是测试文件恢复。

### 9.2 第一层：逐项结果解析

识别整行：

~~~text
<nodeid><空白>PASSED
<nodeid><空白>FAILED
<nodeid><空白>ERROR
~~~

实现正则为：

~~~regex
^(?P<nodeid>[^\s].*?)\s+(?P<status>PASSED|FAILED|ERROR)\s*$
~~~

取 nodeid 在第一个 :: 前的文件路径，必须精确命中白名单。白名单由显式测试文件以及恢复测试根内的 .py 文件组成。

对匹配项按出现次数计数，没有按 nodeid 去重。因此重复运行同一用例可能重复计入。带 pytest 进度后缀等不符合上述整行正则的输出可能不被这一层识别。

### 9.3 第二层：摘要回退

逐行提取：

~~~regex
(\d+)\s+(passed|failed|error|errors)
~~~

分别累加通过、失败、错误；记住最后一次 collected N items，作为诊断字段。

只有完全没有计入逐项结果、且摘要总数大于 0，才使用摘要计数；否则采用逐项计数。摘要可以来自任意输出行，不限定必须是 pytest 的最终摘要。

没有统计 skipped、xfailed、xpassed 等类别；命令退出码只进日志，不直接参与奖励公式。

### 9.4 奖励公式

~~~text
observed_total = passed + failed + errors
total = max(configured_test_case_count, observed_total)

若 total > 0：
    reward = min(passed / total, 1.0)
否则：
    reward = 0
~~~

reward.txt 以 10 位有效数字文本写入并加换行。report.json 包含：

~~~json
{
  "passed": 4,
  "failed": 0,
  "errors": 0,
  "total": 4,
  "configured_total": 4,
  "observed_total": 4,
  "pytest_collected": null,
  "reward": 1.0
}
~~~

不要把 pytest_collected 自动用作分母；基线没有这样做。

### 9.5 独立实现的计分一致性用例

下面可直接作为纯字符串解析器的验收数据，不需要下载仓库或运行 pytest：

| N | 模拟输出/前提 | 期望 p/f/e | reward |
| --- | --- | --- | --- |
| 4 | 只有摘要：4 passed | 4/0/0 | 1 |
| 4 | 只有摘要：3 passed, 1 failed | 3/1/0 | 0.75 |
| 8 | 只有摘要：4 passed | 4/0/0 | 0.5 |
| 4 | 空输出 | 0/0/0 | 0 |
| 4 | 只有摘要：4 passed, 2 skipped | 4/0/0 | 1 |
| 4 | 摘要 4 passed，命令退出码为 1 | 4/0/0 | 1，基线确实不以退出码否决 |
| 4 | 四条白名单逐项 PASSED，另有摘要 10 passed | 4/0/0 | 1，逐项优先 |
| 4 | 两条相同白名单 nodeid PASSED，无其他输出 | 2/0/0 | 0.5，不去重 |
| 4 | 两条摘要各 4 passed，无逐项结果 | 8/0/0 | 1，摘要累加 |
| 0 | 摘要 1 passed | 1/0/0 | 1 |
| 0 | 无有效输出 | 0/0/0 | 0 |

这里特意包含“不理想但兼容”的行为。需要更可靠评分时，可以改为结构化 pytest 报告、去重及退出码门禁，但必须另记评分器版本，不能继续声称是原始 reward 协议。

## 10. 镜像泄漏门禁

### 10.1 基线检查对象

基线创建容器并导出合并后的文件系统，对 tar 中的普通文件检查：

| 条件 | 结果 |
| --- | --- |
| app/、repo/、src/、testbed/ 下存在 .py | fatal：visible_forbidden_source_path |
| site-packages 的 *.dist-info/direct_url.json 指向非 editable 的 file: 安装 | fatal：noneditable_file_direct_url |
| 同类元数据为 editable 的 file: 安装 | warning：editable_file_direct_url |
| .pth 引用了已知源码根路径 | warning |

报告 schema_version=teamfactory.final_image_leak_check.v1，含 fatal 数、最多 50 条命中及警告。fatal 使用特定退出码 42，并删除该次保存的镜像归档；其他非零退出按检查基础设施错误区分。

[基线] 对符合安全路径限制的 direct_url.json，可以仅删除元数据，重建镜像并再次保存、扫描。要求路径位于 site-packages 的 .dist-info 下、没有 ..、字符符合允许集，不能把任意日志文本当作删除路径。

仍有已知镜像内容问题时，重试路由指向 A1，而不只让 A2 改文档；其他类型按本阶段处理，默认镜像泄漏重试预算为 1。

### 10.2 这一门禁没有证明什么

它没有完整检查：

- 所有 site-packages 中是否残留目标项目实现；
- /opt、其他任意目录、非 Python 文件或编译扩展中的参考答案；
- 镜像历史层中是否存在可恢复源码；
- tests/reference 或 fixture 是否夹带生产实现；
- 学生是否能访问 Docker socket、宿主路径或外部网络。

因此“leak check passed”只能解释为“未命中这些规则”。删除 direct_url.json 只移除了来源元数据，不能证明相邻实现也被删除。

[增强项] 若需要可信基准，应在构造时显式记录目标包安装文件、执行目标包卸载验证、检查最终工作区和私有资产隔离，并为 fixture 与测试根设置内容边界。这些不能伪装成基线已经具备的保证。


## 11. 正式 Oracle：证明任务闭环成立

### 11.1 Oracle 适配器契约

[复现约定] 对上层编排器暴露一个逻辑接口：

~~~text
run_oracle(instance_dir, image_archive, job_dir, timeout_policy)
    -> {
         reward: number | object | null,
         trial_dir: path | null,
         stdout: text,
         stderr: text,
         exit/exception information
       }
~~~

输入任务内容不应被运行器原地改写。基线会复制任务到独立临时目录、生成唯一 job 名称，启动外部运行器；超时终止进程并清理临时目录。实际复现应显式设置持久卷 TMPDIR，不沿用系统默认临时磁盘。

基线调用形态为：

~~~text
<harbour-python> -m harbor.cli.main run
  --path <copied-instance>
  --agent oracle
  --jobs-dir <jobs-dir>
  --job-name <unique-job-name>
  --n-concurrent 1
  --timeout-multiplier 3.0
  --yes
  --quiet
  --force-archive
  --cleanup-archive-source-image
~~~

调用环境涉及 DOCKER_HOST、REMOTE_DOCKER_HOST、ROOT_NAME、SSH_PASS 等运行器约定。凭据应从受控环境注入，不写入文档或命令历史。

**[待确认]** 这些选项、docker_image_archive、远端 Docker 和任务文件挂载行为来自所引用的外部运行器接口，不能假定任意公开 Harbor 安装都支持。接入前必须对照选定版本验证；本文没有检查外部实现。

若改用自己的 Docker 执行器，至少实现加载镜像、学生/私有资产分阶段隔离、Oracle 注入、运行 test.sh、日志收集、timeout 和 reward 协议，再通过接入测试。这是方法级替代，不是已证明等价的 Harbor 复刻。

### 11.2 结果读取

从 trial/result.json 的 verifier_result.rewards 读取奖励；相应兼容逻辑也处理 verifier_result 等包装情况。数值归一化接受：

- 数字；
- 对象中的 reward、score 或 mean；
- 仅有一个数值成员的对象。

布尔值不作为数字奖励；没有可靠数值时记为 None，而不是 0，也不是通过。

[基线] 分数至少 0.999999 才通过。基础设施瞬态错误识别在成功判断之前进行，识别连接拒绝/重置、临时不可用、SSH 连接、磁盘不足、Docker daemon 不可用、tag 失败、非法引用格式、context deadline 等文本。

默认 infrastructure retries=2，即首次加两次重试。用尽后记录基础设施失败，不能把它包装成“项目无法实现”。

### 11.3 必须保留的失败证据

为下一轮修复准备：

~~~text
oracle_result.json
trial/result.json
trial/agent/oracle.txt
trial/agent/exit-code.txt
trial/verifier/test-stdout.txt
trial/verifier/report.json
trial/verifier/reward.txt
trial/verifier/stdout.txt、stderr.txt（存在时）
~~~

文件名和是否存在应从运行器实际输出确认。主流程生成简化反馈时会截取 stdout/stderr 尾部，但完整证据目录必须可供检查。修复包同时包含候选 instance、证据和 FILE_INDEX.txt，先读索引和关键错误，再定向读文件，避免无目标扫描。

## 12. Oracle 失败后的受限修复

### 12.1 为什么不能“再生成一道题”

Oracle 失败通常说明参考实现、依赖、路径、测试资产或计分分母不一致。此时更改 start.md 或删测试，会改变任务本身，而不是修好同一个 instance。

[基线] 主流程把修复交回同一个 A2 模型；没有独立第三个修复模型。responsible_stage 是缺陷归属说明，不代表总会重新运行那个阶段的完整 Agent。

### 12.2 修复任务书

> 先读失败报告、测试输出、tests/config.json 和相关文件，给出一个具体、可被证据支持的根因。只修改候选副本，做最小充分修复。禁止修改学生可见题意、削弱原测试、制造奖励、把失败变成功退出或下载未经授权的外部材料。
>
> 若需要镜像变更，写入 environment/Dockerfile 的持久 RUN 层，并在 image_commands 返回对应 shell 片段。临时容器里手工修好不算交付。列出全部实际改动文件；若无法安全修复，撤销候选改动并返回 unrepairable。是否真正修好，由下一次正式 Oracle 决定。

### 12.3 不可变与允许修改的边界

冻结为字节级不可变：

- environment/start.md；
- environment/api_manifest.json；
- instruction.md；
- 已识别的原有测试源码。识别来源包括 config 的 test_files，以及 reference 内以 test 开头、以 _test.py 结尾或名为 conftest.py 的文件。

可以提出有证据的环境、测试路径/配置、fixture 打包、参考答案或验证入口修复，但仍受下面的验证器约束。允许修改某个文件，不等于任意修改其语义都合法。

候选验证包括：

1. 声明 changed_files 与实际文件变更一致。
2. 保留测试文件/函数/断言等计数约束，不能增加 skip/xfail 或无条件成功断言以削弱验证。
3. 拒绝符号链接逃出候选目录。
4. task.toml 的镜像归档目标保持不变。
5. Dockerfile 改动与非空 image_commands 相对应，命令应出现在 Dockerfile 中。
6. image_commands 为单行，拒绝 docker/podman、私有 solution/tests 路径、特权或 mount 等危险模式。
7. 旧测试命令经 shlex 分词后，必须按原顺序成为某条新命令的 token 子序列。
8. 新命令拒绝 reward.txt、/logs/verifier、echo、printf、exit 0、|| true 等绕过标记。

第 7 条允许添加参数，并不等于语义上一定没缩减测试。例如追加某些筛选选项可能满足 token 规则。基线依赖模式检查和后续 Oracle，不是完整的程序等价性证明。

### 12.4 测试分母什么时候允许调小

不能因为低分就降低 test_case_count。只有满足全部条件，才能从旧 N 改成更小的新 N：

- 有可信 Oracle 的 verifier/report.json；
- 新 N 等于该报告 observed_total，且大于 0；
- passed 等于新 N，failed=0、errors=0。

例如原 N=8，但正式报告为 passed=4、observed_total=4、failed=errors=0，可以更正为 4；如果报告为 3 通过 1 失败，不允许改成 3 或 4 来规避失败。

底层辅助接口可接受多份可信历史报告；主流程传入当前 Oracle 结果。复现主流程时不要无依据扩大历史证据来源。

### 12.5 修复响应契约

以下是分母更正的示意候选，不表示已发生真实修复：

~~~json
{
  "schema_version": "teamfactory.oracle_repair.v1",
  "status": "repaired",
  "responsible_stage": "stage2_ast",
  "root_cause": "configured test count exceeds observed collection",
  "diagnosis": "The supplied verifier report shows all four observed tests passed while configured_total is eight.",
  "evidence": [
    "verifier/report.json: passed=4, failed=0, errors=0, observed_total=4"
  ],
  "changed_files": ["tests/config.json"],
  "image_commands": [],
  "validation_notes": [
    "Correct only test_case_count from 8 to 4; retain tests and commands."
  ]
}
~~~

status 只能是 repaired 或 unrepairable。repaired 必须声明非空 changed_files；unrepairable 的 changed_files 和 image_commands 都必须为空。responsible_stage 为 agent1、stage2_ast、agent2_stage3 之一。兼容逻辑可从改动路径/根因推断缺失归属，但新实现应显式填写。

### 12.6 提交、复查与回滚

实施顺序：

1. 在隔离候选目录产生修改；保留原任务和原镜像。
2. 通过冻结文件、改动清单、测试、命令、路径和镜像约束。
3. 从当前有效镜像构建候选镜像，执行声明的 RUN，再 COPY 候选 start.md；即使没有 image_commands，也可能重建用于一致化的镜像。
4. 对候选镜像重新执行泄漏检查并保存新 tar。
5. 准备本地候选目录和远端镜像备份；切换 canonical 路径，记录待确认状态。
6. 对这组新任务/新镜像执行正式 Oracle。
7. 通过则确认并清理备份；失败则还原旧任务和旧镜像，保留失败证据供下一轮。

这是一种带回滚的跨本地/远端提交，不是数据库式原子事务。断电、网络中断可能发生在两次切换之间，因此独立实现应持久化每个提交状态并提供恢复检查；后者属于增强项。

默认最多 3 个修复轮次，配置值 0 会被归一化为至少 1，不是“禁用修复”。明确 unrepairable 或预算耗尽必须终止，不能把文件目录的存在当作成功。

## 13. 编排、预算与记录

### 13.1 先串行复现，再做吞吐优化

建议首次只选一个仓库，A1、AST、A2、Oracle 全链路逐阶段审查。并发不改变构造方法，但会放大镜像冲突、重复 URL、资源不足和失败恢复问题。

[基线] 调度使用 tp 条 lane，每条最多 pp+1 个在途任务；同 lane 的同一阶段不会同时执行多个任务。全局 A1/A2/Oracle 各有并发上限，AST 没有同样的非零阶段限流；线程池容量为 tp×(pp+1)。首次 tp=1、pp=1、limit=1 不会启动第二条候选。

默认参数和语义：

| 参数 | 默认值 | 注意 |
| --- | --- | --- |
| tp / pp | 1 / 1 | lane 数 / 每 lane 容量参数 |
| concurrency | 1 | A1/A2 为 0 时回退使用此值 |
| agent1-concurrency / agent2-concurrency | 0 / 0 | 归一化后通常是 1 |
| oracle-concurrency | 4 | 首次复现建议显式设 1 |
| agent-timeout | 3600 s | 远端 Agent 限时；不是整个任务总预算 |
| stage2-timeout / stage3-timeout | 900 / 1800 s | 各阶段调用限时 |
| item-timeout | 1800 s | 部分远端/传输操作默认值，不是端到端 SLA |
| agent1-transient-retries | 1 | 额外重试数 |
| agent2-coverage-retries | 4 | 同时用于一部分通用 A2 重试 |
| image-leak-retries | 1 | 镜像泄漏反馈回路 |
| oracle-infra-retries | 2 | 最多 3 次 probe |
| oracle-max-repair-rounds | 3 | 至少归一化为 1 |
| agent2-repair-timeout / max-turns | 1800 s / 20 | 修复工具 Agent |
| harbour-timeout / multiplier | 7200 s / 3.0 | 外部运行器及其超时倍率 |
| maintenance / transfer / image-rebuild timeout | 7200 / 3600 / 3600 s | 维护/传输/镜像构建 |
| validate-start-md | false | 不是默认开启的质量保证 |

不同失败类型的计数保存在相应阶段记录中，重写记录时可能不保留其他类型计数。因此不能简单把几个默认值相加，声称基线有一个严格端到端最大尝试数。

[增强项] 独立实现最好另设不可重置的 task_total_attempts 和总时限；如果启用，报告会截断哪些基线本来还会继续的任务。

### 13.2 阶段记录而非目录扫描作为事实来源

需要保存：

- 每任务各阶段 JSON：schema、status、URL、task_id、原始索引、lane、时间戳及结果；
- stage_events.jsonl：阶段变化；
- pipeline_events.jsonl：分配和调度事件；
- 模型轨迹与原始工具日志；
- AST payload、覆盖报告、泄漏报告、Oracle 结果及每轮修复证据；
- checkpoint：活动/队列状态和统计。

“done”统计可能只表示阶段函数已返回，包括内部捕获并记录的失败，不能直接当成功率分子。失败后 dataset_root 中也可能已有任务目录，不能仅按目录数量发布样本。

基线主调度器没有完整的 checkpoint 自动恢复加载流程。已有 checkpoint 不代表可以直接从中断点无损续跑；恢复能力应独立验证。

--output、--error-output 是兼容参数，不应假设主流程会写这两个文件。正式结果应从阶段/Oracle 状态汇总。

### 13.3 主编排伪代码

下列为实现蓝图，省略具体异常分类；精确兼容时需按上述预算和状态处理，而不是直接执行这段伪代码：

~~~text
for ref in selected_records:
    acquire_task_identity_and_paths(ref)

    a1 = environment_agent(ref, optional_leak_feedback)
    require_a1_contract(a1)
    if not a1.passed: record_terminal_failure(); continue

    ast_payload = scan_reference_repo_read_only(a1.repo)
    if implementation_py_count(ast_payload) < 5:
        record_filtered(); continue

    specification = specification_agent(a1, ast_payload, optional_coverage_feedback)
    require_api_coverage_and_nodes(specification, ast_payload)

    instance, image = materialize(a1, ast_payload, specification)
    require_image_leak_gate(image)

    result = oracle_with_infrastructure_retries(instance, image)
    if oracle_passed(result):
        accept(instance)
        continue

    for repair_round in configured_budget:
        candidate = same_agent2_repair(instance, result)
        if candidate.unrepairable: break
        validate_candidate_against_canonical(instance, candidate, result)
        transaction = stage_and_switch(candidate)
        result = oracle_with_infrastructure_retries(transaction.new_instance,
                                                    transaction.new_image)
        if oracle_passed(result):
            commit(transaction)
            accept(instance)
            break
        rollback(transaction)
        preserve_failure_evidence(result)
    else_or_no_accepted_result:
        record_terminal_oracle_failure()
~~~

## 14. 操作手册：如何从规格走到首次复现

### 14.1 第一次运行之前

只做静态准备，不安装、不构建、不调用模型：

1. 选定“兼容基线”或“增强版”；记录任何偏离项。
2. 准备一条授权仓库记录，先固定 commit 并记录摘要。固定提交是新增复现实验约定，不是原 URL 输入已经保证的行为。
3. 填全第 3 节路径表、远端主机和模型服务配置，确认所有目录属于本实验。
4. 验证选定运行器的 CLI/归档支持和学生资产隔离；不确定时先解决适配器契约。
5. 估算 Docker layer、两个镜像 tar、参考仓库多副本及临时修复目录的峰值；检查持久卷与宿主临时磁盘余量。
6. 明确审批：下一步会网络访问、运行任意仓库构建/测试代码、写镜像、启动容器并调用付费模型。本分析不包含这种执行授权。
7. 为 Agent 限定账号、目录和网络能力。原 CLI 的权限绕过模式及提示词约束不是强沙箱；不要让不可信仓库代码接触宿主凭据。

### 14.2 两条实施路线

**路线 A：独立实现。** 按第 3 节实现适配器、按第 4–13 节实现确定性逻辑，使用第 15 节示例验收，再接真实仓库。优势是边界和路径可显式控制；必须明确哪些外部行为只是功能等价替代。

**路线 B：使用当前副本。** 不直接执行 launch*.sh。先显式覆盖全部外部路径和服务参数，确认依赖已经存在，再调用主入口。下面只是参数模板，尖括号未替换前不是可运行命令，本文没有执行：

~~~bash
python -B -m teamfactory \
  --repo-jsonl "<input_jsonl>" \
  --limit 1 --tp 1 --pp 1 \
  --concurrency 1 --oracle-concurrency 1 \
  --work-dir "<work_root>" \
  --run-dir "<run_root>" \
  --dataset-root "<dataset_root>" \
  --output "<work_root>/compat-output.jsonl" \
  --error-output "<work_root>/compat-errors.jsonl" \
  --trajectory-output "<trajectory_output>" \
  --remote-host "<authorized_remote_host>" \
  --remote-user "<task_remote_user>" \
  --ssh-port "<ssh_port>" \
  --ssh-pass-file "<protected_credential_file>" \
  --remote-work-root "<remote_work_root>" \
  --remote-image-root "<remote_image_root>" \
  --claude-bin "<remote_agent_cli>" \
  --agent1-model "<environment_model>" \
  --agent2-model "<specification_and_repair_model>" \
  --api-base "<approved_model_service>" \
  --hyperdistill-root "<external_runner_root>" \
  --remote-docker-host "<approved_docker_endpoint>" \
  --harbour-python "<existing_runner_python>" \
  --harbour-src-dir "<existing_runner_source>"
~~~

在可信启动环境中注入 API key，不把真实密钥写入上述命令。模型密钥读取优先级为显式参数、TEAMFACTORY_API_KEY、SIDECAR_API_KEY。--model 会同时覆盖两个 Agent 模型；--oracle-repair-model 是弃用兼容项，不会创建独立修复模型。

还必须在本地及远端相应执行环境配置 TMPDIR、包缓存和 Docker 存储。仅在本地设置变量未必会传入 SSH 远端；当前代码也不能据此假定已有环境变量透传。

本入口不是只读“预览”：参数归一化会创建目录，后续执行 SSH/Docker/模型并产生状态。不要为确认参数而直接运行完整命令。

### 14.3 每个 checkpoint 的人工放行条件

| checkpoint | 必须检查的实物 | 未满足时停在哪 |
| --- | --- | --- |
| A1 完成 | 非空、离线测试证据；依赖记录；基础镜像与源码去除计划 | 不生成规格 |
| AST 完成 | 实现 .py >=5；测试导入集合合理；parse_errors 可解释 | filtered 或修正适配 |
| A2 完成 | 完整 start.md、manifest；API 和行为节点；无私有路径/测试体 | 返回覆盖/内容反馈 |
| 打包完成 | 任务目录与镜像归档；reference 的内容边界；文档一致性 | 不接收任务 |
| leak gate | 原始命中与清理记录；清楚检查范围 | 修复环境或拒绝 |
| Oracle 完成 | 真实 trial、报告、reward>=0.999999 | 分类 infra/任务问题，受限修复 |
| 接收前 | 冻结产物摘要、成功状态和修复链；无遗留候选混入 | 不发布 |

对内容边界和文档一致性的人工检查强于基线默认门禁；应在报告中单列，不误称原程序已硬性强制。

### 14.4 首次成功之后

先用第 15 节的反例检验评分器及修复约束，再增加多个仓库；最后才增加并发。发布统计至少区分输入无 URL、A1 失败、AST 过滤、规格失败、泄漏失败、Oracle 基础设施失败、Oracle 不可修复、无修复通过、修复后通过。

没有观察到真实运行前，不填写成功率、吞吐、平均 token、成本或人工节省比例。


## 15. 贯穿示例与验收用例

本节的 TagKit 是专门为说明复现方法设计的虚构项目，不是从被分析目录中发现的真实样本，也没有声称本次运行过它。不要把它计入实际成功样本。

### 15.1 准备一个足够小、但能过结构筛选的参考仓库

~~~text
tagkit-repository/
├── pyproject.toml
├── README.md
├── tagkit/
│   ├── __init__.py
│   ├── api.py
│   ├── parser.py
│   ├── normalization.py
│   └── errors.py
└── tests/
    └── test_api.py
~~~

参考项目语义：

- 对外导出 tagkit.normalize_tags(text: str) -> list[str]。
- 仅接受 str；否则抛 tagkit.errors.InvalidTagInput，它是 TypeError 的子类。
- 将输入按英文逗号分段，每段 strip 后 casefold，忽略空串。
- 稳定去重，保留归一化后标签首次出现的顺序。
- 不读文件、不访问网络，不持久化状态。
- 第三方运行时依赖为空；测试环境预置 pytest。

私有测试恰好有 4 个非参数化用例：

| 用例 | 行为断言的含义 |
| --- | --- |
| basic | "Red, Blue" 得到 ["red", "blue"] |
| dedup | " red,RED,, blue ,red" 得到 ["red", "blue"]，顺序不乱 |
| empty | " , , " 得到 [] |
| type | None 抛出 InvalidTagInput，且它兼容 TypeError |

测试采用 from tagkit import normalize_tags 与 from tagkit.errors import InvalidTagInput。示例假设库内部辅助函数以下划线命名，以便公开 API 集中在上述两项；不要求学生复刻参考内部实现。

五个实现 .py 中包含 __init__.py，符合基线计数；只有四个时应被过滤。这个例子说明门槛是文件数，不是项目“足够复杂”的证明。

### 15.2 预期中间结果

A1：collected=4、passed=4、failed=errors=0、returncode=0、network_none=true；命令与前文示例一致。

AST：

~~~json
{
  "schema_version": "teamfactory.stage2_ast_payload.v1",
  "implementation_python_files": [
    "tagkit/__init__.py",
    "tagkit/api.py",
    "tagkit/errors.py",
    "tagkit/normalization.py",
    "tagkit/parser.py"
  ],
  "required_api_symbols": [
    "tagkit.errors.InvalidTagInput",
    "tagkit.normalize_tags"
  ],
  "test_case_count": 4,
  "parse_errors": []
}
~~~

上面是 payload 的关键字段摘录，不是完整 schema 实例。完整输出还应包含第 6.4 节列出的目录树、API 记录、模块清单和 summary。public_functions 还可能包括测试定义，不能要求其总数恰好等于公开业务函数数。

### 15.3 可交给学生的 start.md 示例

下面是完整的最小规格示例。实际模型可生成不同措辞，但接口、行为和环境事实必须一致。项目所需行为均来自本节明确构造的示例契约，不是对真实未知项目的推断。

~~~~markdown
## TagKit Project Introduction and Goals
TagKit converts comma-separated user tags into an ordered list of normalized, unique tags. Implement the complete Python package from scratch.

## Natural Language Instructions (Prompt)
Create the tagkit package in the current workspace. Implement the public imports and behavior described below. The package must run using the preconfigured environment without fetching code, data, or dependencies.

## Environment Configuration
All implementation paths must be relative to /workspace. Never create, write, or use /testbed.

### Python Version
Python 3.11.

### Core Dependency Library Versions
The project requires only the Python standard library. No additional runtime package installation is required.

## TagKit Project Architecture

### Project Directory Structure
~~~text
workspace/
└── tagkit/
    ├── __init__.py
    ├── api.py
    ├── parser.py
    ├── normalization.py
    └── errors.py
~~~

## API Usage Guide

### Core API

#### 1. Module Import
~~~python
from tagkit import normalize_tags
from tagkit.errors import InvalidTagInput
~~~

#### 2. normalize_tags - Normalize comma-separated tags
**Function**:
Convert a string into an ordered list of normalized, unique nonempty tags.

**Function Signature**:
~~~python
normalize_tags(text: str) -> list[str]
~~~

**Parameter Description**:
text must be a str. Split using the literal comma character. Whitespace around each segment is removed. Normalize each remaining segment using str.casefold.

**Returns**:
A newly produced list[str], preserving the order of first occurrence after normalization. Return [] when there are no nonempty tags. Raise InvalidTagInput before processing when text is not a str.

#### 3. InvalidTagInput - Invalid input type
**Function**:
Signal that normalize_tags received a non-string input.

**Class Definition**:
InvalidTagInput is a public subclass of TypeError, importable from tagkit.errors.

**Parameter Description**:
No additional constructor behavior beyond ordinary TypeError is required.

**Returns**:
This is an exception type. No particular exception message is specified.

## Usage Example
~~~python
from tagkit import normalize_tags

tags = normalize_tags(" Red, BLUE, red ")
print(tags)
# Expected output: ['red', 'blue']
~~~

## Detailed Function Implementation Nodes

### Node 1: Split, normalize, and preserve unique tag order
**Function Description**:
Process comma-separated strings into a stable sequence of tags without mutating external state.

**Handling Strategy**:
Split on commas, strip surrounding whitespace from each segment, apply casefold, and discard empty segments. Deduplicate the resulting strings while preserving their first appearance. Do not sort the result. Each call returns its own list and performs no file or network I/O.

**Input and Output Examples**:
- "Red, Blue" -> ["red", "blue"].
- " red,RED,, blue ,red" -> ["red", "blue"].
- " , , " -> [].
- "Straße,STRASSE" -> ["strasse"].

### Node 2: Validate the input type
**Function Description**:
Reject non-string input consistently through the public exception type.

**Handling Strategy**:
Check the input before performing string processing. Raise InvalidTagInput for a non-str value. Since InvalidTagInput inherits from TypeError, callers catching TypeError must also catch this failure. Do not coerce non-string values to strings.

**Input and Output Examples**:
- None -> raises InvalidTagInput.
- 123 -> raises InvalidTagInput.
- ["red", "blue"] -> raises InvalidTagInput.
~~~~

注意：行为 Node 的例子可以覆盖“原测试没有逐个列出、但参考实现和文档明确支持”的行为。不能为了增加例子擅自设计原项目不支持的特性。

### 15.4 从规格到最终任务

- environment/start.md 使用上文内容，environment/api_manifest.json 使用第 7.5 节示例。
- environment 镜像仅提供环境与 /workspace/start.md，不提供 tagkit 实现。
- solution/oracle 为完整参考快照。
- tests/reference 至少包含 pyproject.toml 和 tests/test_api.py；拷贝整个 tests 根。
- tests/config.json 使用第 8.5 节内容。
- Oracle 运行 solve.sh 后再运行正式 test.sh，四项全部通过，reward=1。
- 如果学生只实现正常输入、未处理 None，预期是 3 passed、1 failed，reward=0.75。
- 如果因打包漏文件导致收集失败，应该归为环境/打包问题，不把它解释成学生能力不足。

最后三项是示例的预期验收结果，不是本次已测得的结果。

### 15.5 不调用模型即可验证的确定性组件

| 组件 | 测试输入 | 应观察到的结果 |
| --- | --- | --- |
| URL 归一化 | owner/repo；git@github.com:owner/repo.git；HTTPS 末尾 .git | 按第 4 节各分支处理 |
| 身份 | 同一个规范化 URL 的两个不同 commit | task_id 相同；账本应区分 commit |
| 去重 | JSONL 重复两行相同 URL | 基线读入两条，不自动去重 |
| AST 筛选 | 4 个实现 .py；5 个实现 .py | 前者 filtered，后者进入 A2 |
| 测试导入 | from tagkit import normalize_tags | 必选符号包含 tagkit.normalize_tags |
| 相对导入 | from .tagkit import normalize_tags | 不进入基线测试导入必选集 |
| 覆盖正例 | manifest 显式完整覆盖两项 | API gate 通过 |
| 覆盖反例 | 删除 InvalidTagInput 所有别名及文档提及 | API gate 失败并报告缺失 |
| 宽松覆盖 | 只在 manifest 放 normalize_tags 短名 | 仍可能覆盖 tagkit.normalize_tags |
| 节点编号 | Node 1、Node 3 | 拒绝不连续编号 |
| 文本树 | 清理后剩 4 个 .py 条目 | 拒绝，即使参考 AST 原有 5 个 |
| 根选择 | pkg/api_test.py | 基线根为 pkg；提示实现泄漏风险 |
| 计分 | 第 9.5 节所有字符串用例 | 与表中数值一致 |
| 修复冻结 | 修改一个 start.md 字节 | 拒绝候选 |
| 修复命令 | 原 pytest -q，新 pytest -q -vv | 满足 token 子序列规则 |
| 修复命令 | 原 pytest -q，新 echo 4 passed | 拒绝 |
| 分母修复 | N=8，可信报告 4/0/0/observed=4，改 N=4 | 允许候选，仍需正式 Oracle |
| 分母修复 | N=8，可信报告 3/1/0/observed=4，改 N=4 | 拒绝 |
| 回滚 | 提交候选后 Oracle 失败 | canonical 任务和镜像都恢复旧版 |

这些用例可以用独立的标准库测试实现，不需要 import 或运行整个副本。本文没有实际运行这些测试。

### 15.6 运行器接入的最低验收

在取得执行授权后，另做以下集成检查：

1. 从归档启动的工作区确实只有任务规格，不含参考实现。
2. 学生阶段无法读取 /solution、/tests 或它们的宿主映射。
3. Oracle 注入之后，四个测试通过且 report、reward 与 trial 结果一致。
4. 无实现的空答案不能因恢复 reference 中的生产代码而意外通过。
5. 故意破坏一项功能时，评分按预期降低。
6. 超时、容器失败、缺失 reward 与测试失败被分开记录。
7. 修复失败后，目录与镜像摘要均回到修改前。
8. 仅更换部署适配器而不改任务内容时，以上行为保持一致。

第 4、5 项是建议增加的负对照，不是主流水线已经自动运行的 NOP/学生验证。

## 16. 必须单独标注的基线特殊行为

独立实现最容易在下面这些地方无意中改变方法：

| 主题 | 基线行为 | 可选增强，但需另标版本 |
| --- | --- | --- |
| 仓库版本 | 身份仅基于 URL，未强制固定 commit | 提交锁定、源码摘要 |
| 上游筛选 | 候选来源与筛选元数据缺失 | 独立定义许可/语言/规模策略 |
| A1 测试可信度 | 先相信结构化自报，通过检查不等于独立重跑 | 基础环境独立重放 |
| AST 的 src 布局 | 不去掉 src 前缀 | 包布局感知模块映射 |
| API 覆盖 | 别名和全文出现即可 | 逐 API 文档与证据映射 |
| 样式检查 | 默认关闭；严格检查中仍有与提示词不同的单数例子字段要求 | 统一模板和校验器后升级版本 |
| 文档/镜像顺序 | 先构建镜像，后本地清理目录树 | 先定稿、摘要一致后再构建 |
| 测试资产 | 启发式测试根可能包含生产代码 | 显式资产白名单与内容检查 |
| 评分 | 解析文本；不直接否决非零退出；可能重复计数 | 结构化测试结果、去重、退出码门禁 |
| 镜像安全 | 检查部分路径/元数据，不证明无残留 | 全面目标包文件清单和隔离验证 |
| 修复约束 | 模式与 token 检查，不证明测试语义不弱化 | 限定允许补丁类型及独立负对照 |
| 预算 | 多类重试计数可能互相覆盖 | 不可重置的全局预算 |
| 续跑 | 有 checkpoint，主入口无完整恢复实现 | 显式幂等重试/恢复状态机 |
| Oracle 满分 | 验证参考答案和任务闭环 | 再验证空答案、负例、独立学生 |
| Agent 边界 | 提示词约束、工具 CLI 权限配置 | 强制目录、容器、账号隔离 |

不能一边修正这些行为，一边宣称“与原始数据构造完全相同”。推荐同时给出 baseline-compatible 与 hardened 两份实验配置，使用同一组对照样本报告差异。

### 16.1 不要把另一条 tuning 流程混进主方法

副本还包含针对已生成 instance 的 instance_tuning：

- 让真实求解模型尝试任务；
- 非正分/缺分后诊断是否是 instance 问题；
- 达到指定置信度时提出修复；
- 依据修复前后求解分数是否改善决定保留。

它与主 Oracle 修复的目标和不可变边界不同，例如它可以在约束下改写 start.md。它不是本文主线“仓库 → 新 instance”的必要阶段，也不能拿其状态代替正式 Oracle 验收。若要复现完整后处理，应另建规格、版本和实验记录。

## 17. 最终交付检查表与不确定项

### 17.1 Definition of Done

独立实现完成时，应能交出：

- 一份版本化输入清单及来源、commit/镜像/模型/提示词/运行器账本。
- 五个适配器或组件的可检查接口实现。
- 第 15.5 节确定性兼容性用例的结果。
- 至少一条经授权真实运行的完整阶段记录，而不只是示意 JSON。
- 该任务的 start.md、manifest、任务目录和可加载镜像归档。
- 对 API 覆盖、测试资产、镜像内容和学生隔离的检查记录。
- 正式 Oracle 的 trial、报告和奖励；如修复过，包含候选、验证、提交、复查和回滚证据。
- 通过/失败/过滤/基础设施错误的分类汇总。
- 每一处与基线不同的行为清单。

本文已经提供复现所需的规格、模板、算法、运行准备和验收设计；没有声称上述真实执行交付已经完成。

### 17.2 仍需外部确认的项目

| 未知项 | 为什么影响复现 | 如何解决 |
| --- | --- | --- |
| 历史候选集合、采样条件 | 决定样本分布、总产量、偏差 | 由数据拥有者另行提供；本次不访问原目录 |
| 仓库具体 commit | 相同 URL 的代码和测试会变化 | 在新的授权运行中固定并记录 |
| 模型/服务/CLI 的准确版本 | 工具行为、输出格式和质量可能不同 | 固定版本与提示词摘要，保存轨迹 |
| 依赖锁及基础镜像 digest | 同一安装命令可能得到不同环境 | 新运行记录解析后的依赖和镜像身份 |
| 外部 Harbor 定制实现 | 归档加载、挂载和结果 schema 可能不兼容 | 对接运行器源码/文档并完成第 15.6 节验收 |
| 真实已有样本的输出质量 | 静态代码不能证明规格可实现、无泄漏 | 独立样本审查和负对照 |
| 历史运行的成功率和成本 | 当前副本没有足够数据 | 实际运行采集，不作估计冒充事实 |

## 附录 A：仅用于核对的本地证据索引

正文已按构造流程组织；以下索引便于实现者核对边界，不要求逐文件阅读后才能理解方法。所有链接均指向当前副本内文件。

| 规格主题 | 本地证据 |
| --- | --- |
| URL、task_id、输入解析 | [repo_source.py](teamfactory/repo_source.py) |
| 参数默认值及归一化 | [cli.py](teamfactory/cli.py) |
| 调度、反馈重试、队列与 checkpoint | [scheduler.py](teamfactory/scheduler.py) |
| 阶段记录 | [artifacts.py](teamfactory/artifacts.py) |
| 模型工具调用与响应解析 | [remote_claude.py](teamfactory/providers/remote_claude.py) |
| A1 任务书与结果门禁 | [prompt.md](teamfactory/stages/agent1/prompt.md)、[stage.py](teamfactory/stages/agent1/stage.py) |
| AST 规则与文件数过滤 | [stage2_ast/stage.py](teamfactory/stages/stage2_ast/stage.py) |
| 规格任务书 | [agent2_stage3/prompt.md](teamfactory/stages/agent2_stage3/prompt.md) |
| 文档门禁、打包、计分、泄漏和主 Oracle 闭环 | [agent2_stage3/stage.py](teamfactory/stages/agent2_stage3/stage.py) |
| 主线修复任务书 | [repair_prompt.md](teamfactory/stages/agent2_stage3/repair_prompt.md) |
| Oracle 适配、结果契约和修复冻结 | [oracle_runner.py](teamfactory/stages/oracle_repair/oracle_runner.py)、[contracts.py](teamfactory/stages/oracle_repair/contracts.py)、[validation.py](teamfactory/stages/oracle_repair/validation.py) |
| 通用候选约束和镜像事务 | [validation.py](teamfactory/stages/instance_tuning/validation.py)、[image_repair.py](teamfactory/stages/instance_tuning/image_repair.py) |
| 外部运行器命令兼容 | [harbor_cli.py](teamfactory/harbor_cli.py) |
| 对应测试证据 | [test_agent1.py](tests/test_agent1.py)、[test_agent2_stage3.py](tests/test_agent2_stage3.py)、[test_oracle_repair.py](tests/test_oracle_repair.py)、[test_instance_tuning.py](tests/test_instance_tuning.py) |
| 补充代码分析与风险讨论 | [ANALYSIS_REPORT.md](ANALYSIS_REPORT.md) |

## 附录 B：最短实施顺序

1. 实现并测试 ID、AST、覆盖、测试根选择、计分和修复验证器。
2. 对接一个工具型模型适配器和一个容器适配器。
3. 按 A1 任务书获得真实离线通过的参考环境。
4. 按 A2 任务书生成 start.md 与 manifest，运行硬门禁。
5. 打包任务与镜像，检查测试资产和镜像泄漏。
6. 对接并验收 Oracle 运行器，运行同一正式验证器。
7. 失败时只走受限修复、事务提交和复查；成功后冻结产物。
8. 用负对照补足基线未证明的隔离与评分性质，再扩大样本和并发。

