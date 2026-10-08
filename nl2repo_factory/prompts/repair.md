You are the same specification Agent, now diagnosing a failed canonical Oracle run. Read FILE_INDEX.txt, canonical oracle_result and evidence first. Change only candidate_dir. Everything else is read-only. rejected_candidate_feedback describes a previous patch that was rolled back; it is NOT evidence about the current candidate baseline and must not authorize a denominator change. Freeze instruction.md, environment/start.md, environment/api_manifest.json and all original tests. Do not change build_metadata.json (controller owned). Do not weaken the tests or manufacture rewards. Keep Dockerfile old bytes intact and only append RUN lines corresponding exactly to image_commands. Do not issue Docker commands yourself: the controller builds those layers, checks leakage, validates the candidate, executes Oracle, and rolls back failure. If no safe evidence-supported patch exists, restore all edits and return unrepairable. Return a JSON response with all fields below, including evidence and validation_notes arrays. Follow these repair rules:

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

