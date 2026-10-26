# 技术设计文档

> 队伍名称：天天向上队
> 作者：杨启桐，刘思源，乔义杰
> 版本：v1.0
> 日期：2026-10-26

---

## 1. 项目概述

### 1.1 背景与目标

赛题要求从鸿蒙 ArkTS 应用的崩溃日志（`jscrash.log`）出发，自动定位到错误代码行，再生成修复并运行验证。难点在于日志里的崩溃行往往不是根因：越界下标可能来自上游写错的循环边界，`undefined` 可能来自被改坏的 mock 数据。只报栈顶会在这类案例上失分。

本项目实现了"日志解析 → 规则定位 → LLM 跨文件追因 → 确定性引用核对"的定位链路，并建立了与诊断端隔离的离线评测。**修复方案生成、补丁应用和运行验证没有实现**，第 5 节给出原设计，第 2.4 节和测试报告如实列出这部分的缺口。

### 1.2 核心功能

- 日志解析：兼容 `.log` 与 `.zip`、UTF-8/UTF-16（BOM）、`\` 与 `/` 混用路径、`file:` 前缀、缺字段的手写日志；去除日志中可能泄露答案的段落。
- 源码映射：栈帧路径 → 工程内源文件，输出 `UNIQUE` / `MULTIPLE` / `NOT_FOUND`。
- 规则定位：行号对齐 + 两条越界规则，只提出带假设的候选，不宣称已确认根因。
- LLM 定位：Claude Opus 5.5 通过 3 个受限工具（`read_file` / `search` / `submit`）在沙箱内读码追因；答案须通过确定性引用核对，否则退回规则答案。
- 离线评测：允许清单沙箱、人工标准答案、exact/range/function/file 四级指标、token 与耗时统计。

### 1.3 非功能需求

| 需求 | 目标值 | 实测（25 个可评分案例） |
|------|--------|------|
| 定位耗时 | 平均 < 60 s/案例 | LLM 平均 22.3 s/案例，批次墙钟 562 s |
| token 消耗 | 单案例上限 160k | 合计 719,442，最高 127,379（ComprehensiveNews1），无案例超限 |
| 防编造 | 编造的行号或引用不得进入最终答案 | 引用核对为确定性代码；有单元测试 |
| 防泄漏 | 诊断端读不到测试脚本、注入报告、标准答案 | 允许清单复制 + 路径越界拒绝 + 答案标记检测，均有测试 |
| 失败不降级 | LLM 超时、超预算、无答案时不丢失规则答案 | 退回规则答案并标注原因，有测试 |
| 可复现 | 结果绑定输入与实现 | 运行配置记录 cases/ground truth/实现/prompt 的 SHA-256 |

---

## 2. 整体方案架构

### 2.1 整体方案

```
┌────────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────────┐   ┌────────────┐
│ jscrash.log│──►│ ingest     │──►│ retrieve     │──►│ locate/rules  │──►│ locate/llm │──► LocateResult
│ + 源码     │   │ 日志解析   │   │ 栈帧→源码映射│   │ 对齐+越界规则 │   │ LLM 追因   │
└────────────┘   └────────────┘   └──────────────┘   └───────────────┘   └─────┬──────┘
                                                                              │ 引用核对失败/
                                                                              │ 超预算/超时
                                                                              ▼
                                                                         退回规则答案

   评测侧（与诊断端隔离）：eval/sandbox 允许清单副本 ──► run_baseline ──► score（对比人工标准答案）
```

### 2.2 模块功能划分

| 模块 | 职责 | 关键文件 | 状态 |
|------|------|---------|------|
| ingest | 日志解码、字段与栈帧提取、泄漏段落剔除 | `src/dfx_repair/ingest/crash_log.py` | 已实现 |
| retrieve | 栈帧路径映射、函数范围、词法扫描（遮蔽字符串/注释/模板） | `src/dfx_repair/retrieve/` | 已实现 |
| locate/stack_top | 栈顶业务帧基线 | `src/dfx_repair/locate/stack_top.py` | 已实现 |
| locate/rules | 行号对齐、R-BOUNDS-PARTIAL、R-BOUNDS-LOOP | `src/dfx_repair/locate/rules/` | 已实现 |
| llm | `claude -p` 后端、stdio MCP 工具服务、会话记录、冻结 prompt | `src/dfx_repair/llm/` | 已实现 |
| locate/llm | 组织 LLM 输入、引用核对、采纳或退回 | `src/dfx_repair/locate/llm.py` | 已实现 |
| eval | 案例盘点、标准答案、沙箱、评分、批处理 | `eval/` | 已实现 |
| spec / repair / verify / orchestrator | 修复规格、补丁生成、分层验证、重试编排 | `src/dfx_repair/{spec,repair,verify,orchestrator}/` | **仅占位，未实现** |
| Skill | 面向 Agent 的定位工作流说明 | `skill/dfx-fault-repair/` | 见该目录 |

### 2.3 数据流

```
jscrash.log ─parse─► CrashContext（错误类型、消息、栈帧、设备路径）
           ─map───► CrashSite（源文件、行、所在函数、UNIQUE/MULTIPLE/NOT_FOUND）
           ─rules─► LocateResult(rules)：status、crash_location、root_cause_locations、
                     evidence、assumptions、context_requests、unresolved_candidates
           ─llm───► submit{file, line, quoted_line, evidence_*, origin_*, reasoning}
           ─check─► 引用核对通过 → LocateResult(rules+llm)；否则沿用 LocateResult(rules)
```

`LocateResult` 中 `root_cause_locations` 依次为 LLM 答案、规则答案、崩溃行（去重并标注 role）；答案落在跨行语句内时附加 `statement_span`。这一结构原本也供修复阶段使用。

### 2.4 预期结果与实际达成

| 赛题维度 | 达成情况 |
|---|---|
| 定位准确性 | 25 个可评分案例：exact 24/25（96%）、range 25/25、function 24/25、file 25/25。对照栈顶基线 exact 18/25、规则 21/25。详见测试报告 |
| 代码修复效果 | **未实现**。仅在 ComprehensiveTool1 上做过一次手工修复验证（第 5 节） |
| 修复方案质量 | 修复通过率**未测**；定位阶段的资源消耗已测：719k token、平均 3.3 轮工具调用（82 次调用/25 例）、平均定位 22.3 s |

---

## 3. 核心设计

### 3.1 关键算法 / 策略

**日志解析。** 先按 BOM 判断编码；ZIP 内按成员名查找日志，兼容两种路径分隔符，成员名歧义时拒绝而不是猜。栈帧路径去掉设备前缀和 `file:`，再按后缀段匹配工程文件。

**源码映射。** 对栈帧路径做后缀匹配：唯一命中为 `UNIQUE`；多个同名文件为 `MULTIPLE`，候选放入 `unresolved_candidates`，不随便选一个。

**行号对齐（align）。** 日志行号来自编译产物，可能与源码差几行。在崩溃行附近找能产生该错误消息的表达式（如 `Cannot read property 'x' of undefined` 对应 `.x` 访问），唯一时对齐，歧义时不对齐并记录。

**越界规则。** 两条规则都只在词法上能闭合时触发：

- `R-BOUNDS-PARTIAL`：`arr[i].p` 读到 `undefined`，且同一函数中更早的 `if` 只检查了 `arr.length`、没检查下标 → 报该 `if` 行（ComprehensiveTool1：83 → 78）。
- `R-BOUNDS-LOOP`：越界错误来自 `new DataView(buf)` 上的 `getInt16(offset)` 一类读取，读取位于 `for (i = ...; i <= bound; ...)` 循环内，`bound` 是"缓冲区长度 / 步长"，`offset` 依赖 `i` 和该步长 → 报循环头（`<=` 应为 `<`；AudioEditing1：58 → 52）。

规则输出始终是 `CANDIDATE` 并带 `assumptions`（例如"假设该路径在运行时可达"）。词法助手不认识的结构（如模板内表达式）直接 `UNRESOLVED`，不报根因。

**LLM 追因。** 每个案例都调用（不按难度分档）。输入为脱敏日志前 5000 字符、规则层完整输出、崩溃行前 40 行与后 15 行的带行号代码。模型只能用 3 个工具：

| 工具 | 约束 |
|---|---|
| `read_file(path, start_line, end_line)` | 单次最多 150 行；路径 resolve 后必须在 app 根目录内 |
| `search(pattern, path_contains?)` | 正则，默认不区分大小写；只搜 `.ets/.ts/.js/.json/.json5`；最多 40 条，超出注明总数；源码首次读入内存后在内存中搜索 |
| `submit(...)` | 只能调用一次；提交后后端立即停止 |

**确定性引用核对。** 核对不依赖 LLM：

- `quote_matches`：`file:line` 原文去首尾空白后等于 `quoted_line`；
- `evidence_matches`：证据行不同于答案行，且原文相符；
- `origin_matches`：值来源行原文相符（只记录，不参与采纳）。

采纳条件：`quote_matches` 为真，且答案就是崩溃行或 `evidence_matches` 为真。是否"答的是崩溃行"按位置判断，不看模型自报的 `hypothesis`。核对能防止编造，但不能证明答案正确。

**预算。** 每案例最多 14 轮，剩 3 轮时提示收尾；token 上限 160k（input + output + 缓存读写），达到 80% 时提示立即提交；单次调用 900 s 超时，失败重试 1 次。超出上限前已提交的答案按核对结果保留并标记 `over_budget`。

### 3.2 工作流设计

1. `eval.inventory` 盘点数据，生成 `resources/cases.json`（27 个案例）。
2. `eval.sandbox` 为每个案例按允许清单复制源码（白名单后缀）和日志；测试脚本、`testcases/*.json`、注入报告、标准答案不进入副本。若源码或日志中出现答案标记，该案例记为 `BLOCKED`。副本按输入内容哈希缓存，任何输入变化都会让缓存失效。
3. `eval.run_baseline --method {stack_top|rules|rules+llm}` 逐案例运行定位器；单案例异常记 `ERROR`，不中断批次。
4. `rules+llm` 中，`locate/llm.py` 生成 MCP 配置，启动 `claude -p --model claude-opus-5-5 --tools "" --strict-mcp-config --mcp-config <cfg>`。内置工具全部关闭，模型只能调用本项目的 stdio MCP 服务（`llm/mcp_server.py`）。后端解析流式输出，按 `message_delta` 统计 token，等到 `submit` 那次调用的用量结算后停止。
5. 核对答案，组装 `LocateResult`；完整对话与工具调用写入 `eval/runs/<run>/llm/<case>.json`。
6. `eval.score` 与 `resources/ground_truth.json` 比较，`BLOCKED`/`ERROR`/`UNRESOLVED` 保留在分母中；输出 `score.json` 与 `report.md`。

### 3.3 Prompt 设计

思路：只写通用的根因推理纪律，不写任何针对具体案例或数据集的词汇（如 mock、author）。prompt 冻结为 v4，存放在 `src/dfx_repair/llm/prompts/localize_v4.txt`，每次运行记录版本和 SHA-256（`12e096761c18…`）。修改规则：理由不能依赖具体案例，改后版本号加 1，不得根据 25 个案例的结果反复迭代。

7 条分析规则（摘录原文）：

```
1. Start from the crash line and name the failing value ... Trace that value
   to where it is produced or last assigned, following calls, lookups and data
   sources across files. Report the crash line only after you have read that
   producer and found it correct; otherwise report the faulty producer line.
2. Do not assume that adding a null or bounds check fixes the root cause.
3. Compare relevant sibling code or data when available. Treat inconsistencies
   as clues, not proof ...
4. Treat comments as unverified ...
5. In a multi-line statement, choose the line containing the faulty
   subexpression, not merely the statement's first line.
6. Evaluate each candidate against the observed crash ... Do not invent
   requirements to prefer one candidate.
7. Back every causal claim with a line you have read.
```

规则 1 解决"停在崩溃行"，规则 4 针对注入代码常带的辩护性注释（spike 中 Express1 曾被注释误导），规则 7 和 `submit` 的原文引用字段配合确定性核对，约束编造。

### 3.4 模型选择依据

同一 v4 prompt 下的 spike 对比（结果目录 `eval/runs/llm_spike_*`，未入库）：

| 配置 | exact | range | 资源 |
|---|---|---|---|
| Kimi + v2 | 23/25 | 25/25 | 2325 s，809k token |
| Sonnet 5.5 + v3 | 22/25 | 23/25 | 463 s，465k token |
| DeepSeek-Flash + v4（无上限） | 22/25 | 23/25 | 2175 s，6.65M token |
| DeepSeek-V4-Pro + v4（160k 上限） | 23/25（含退回） | 22/25 | 2560 s，1.55M token |
| **Opus 5.5 + v4 + MCP 工具（正式）** | **24/25** | **25/25** | **562 s，719k token** |

Sonnet 在 ComprehensiveTool1、Notes1 上 2–3 轮就停在崩溃行；Opus 在这两个案例上答对。Opus 正式运行的 token 少于 Sonnet v3 之外的所有配置。

---

## 4. 关键技术难点

| 难点 | 解决方案 | 效果 |
|------|---------|------|
| 崩溃行 ≠ 根因（25 例中 6 例的崩溃行不在标准答案内，栈顶上限 19/25） | 规则处理同函数内的越界；LLM 按"追失败值的来源"跨文件追因 | exact 18 → 21（规则）→ 24（LLM） |
| LLM 编造行号与引用 | `submit` 必须附原文引用，确定性核对，不过则退回规则 | 编造的答案不能进入结果（有单元测试）；正式运行 25/25 通过核对 |
| 模型读取答案或原始数据（DeepSeek spike 中模型 6 次尝试读沙箱外的 `data-master/`、`eval/`） | 关闭内置工具，只开放 MCP 工具；路径 resolve 后限制在 app 根目录；允许清单副本；答案标记检测 | `../`、绝对路径、指向外部的符号链接均被拒绝（有测试） |
| token 集中在少数难题（spike 中单案例 10 万–138 万） | 14 轮 + 160k 上限 + 80% 提示 + `submit` 后立即停止 | 正式运行单案例最高 127k，无超限 |
| `/mnt/` 盘 I/O 慢（WSL） | 搜索前把源码读入内存；评测副本放 Linux 文件系统 | News1 单次搜索 37.5 s → 0.07 s |
| 日志格式杂乱（UTF-16、ZIP 路径混用、手写日志） | BOM 检测、成员名归一、字段缺失回退 | 25/25 解析成功，0 BLOCKED、0 ERROR |

---

## 5. 未实现部分与扩展性设计

### 5.1 修复与验证（原设计，未实现）

以下为 `plan/framework-design.md` 中的设计，代码只有空模块：

1. `RepairSpec`：针对定位结果写明触发条件、正确行为、需保持的行为、验证方法。
2. 补丁生成：按 `RepairSpec` 生成候选 diff；行为审查拒绝吞错误、删功能、无条件返回假结果的补丁。
3. 分层验证：V0 补丁可应用 + 行为审查；V1 Node 局部复现 + 差分；V2 hvigor 编译；V3 Hypium 用例。每层输出 `PASS/FAIL/BLOCKED/SKIPPED`。
4. 编排：按失败原因分流重试，每轮回滚到原始源码，统一预算。

唯一的手工验证：ComprehensiveTool1 在第 78 行补 `|| index2 >= list.length` 后，Node 局部复现脚本中原崩溃消失，25 组合法输入的差分结果一致；改用可选链的方案会在第 85 行继续崩溃。这说明"定位到守卫行"对修复是有意义的，但只有一个案例，不能代表修复成功率。

### 5.2 泛化性

- prompt 不含案例相关词汇，规则只依赖错误消息和词法结构，原则上不绑定这 25 个案例。
- 没有独立留出集：25 个案例既是 prompt 修改的参照，也是评测集，数字偏乐观（测试报告 3.3 节）。
- 后端接口与模型无关（输入 system prompt、工具、预算；输出答案、对话、用量），模型名从配置读取。OpenAI 兼容后端（提交版拟用 OpenRouter）已在 spike 中用手写循环验证，未合入主代码。

### 5.3 可扩展方向

- 悬空引用检查：在 mock/数据字面量中找引用了不存在 id 的条目，作为线索交给 LLM，降低数据类故障的检索成本（News1 当前 127k token，大部分花在找数据上）。
- 经验记忆：把确认过的根因模式沉淀为规则。
- 故障变异：对现有案例做变异，生成 OOD 测试集。

---

## 附录

### 参考资料

- 赛题说明：`题目要求.md`
- 框架设计：`plan/framework-design.md`
- LLM 定位设计：`docs/superpowers/specs/2026-10-22-llm-localization-design.md`
- 实施计划：`docs/superpowers/plans/2026-10-22-llm-localization-claude-cli.md`
- 标准答案复核：`plan/gt_review.md`

### 术语表

| 术语 | 解释 |
|------|------|
| exact | 预测行属于标准答案的可接受行集合 |
| range | 预测行落在标准答案的相关范围（含崩溃行、注入改动相关行） |
| CANDIDATE | 有证据的假设，不等于已证实根因 |
| UNRESOLVED | 无法唯一定位；保留在分母中 |
| BLOCKED / ERROR | 资料污染或不可准备 / 执行异常；保留在分母中 |
| 引用核对 | 用确定性代码检查 LLM 引用的源码行是否与原文一致 |
| 沙箱 | 按允许清单复制的诊断副本；过滤输入，不是操作系统级隔离 |
