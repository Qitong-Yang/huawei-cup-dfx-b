# 框架设计：DFX 故障定位与自修复

> 状态：v0.2（2026-10-19），已合并 `plan/框架设计改进建议.md` 的七项建议。确认后同步到 `docs/technical-design.md` 第 2、3 节。

## 1. 设计原则
1. **证据驱动**：规则只提出候选，并附带证据和未确认的假设；满足明确退出条件才直接输出，否则继续检索或调用 LLM。
2. **定位"故障行"而非"崩溃行"**：同时输出崩溃位置和根因位置集合。
3. **先定修复目标，再写补丁**：补丁必须满足触发条件下的正确行为和正常输入下的保持条件。
4. **审查行为，不禁语法**：try/catch、可选链可以用；拒绝的是吞掉错误、删除功能、无条件返回假结果。
5. **失败按原因分流**：编译失败、故障仍在、功能回归、环境阻塞、证据不足各回到不同模块。
6. **结果可追溯**：每次运行绑定源码哈希、补丁哈希、工具版本和 `run_id`。
7. **评测按允许清单隔离**：诊断端只拿到指定的源码和日志。

## 2. 整体流程
```
输入与版本固定（源码哈希、run_id）
 ① 日志解析 → CrashContext
 ② 按需代码检索：语句 → 函数与类字段 → 直接调用者/被调用者 → 变量与数据定义（每级有预算上限）
 ③ 证据驱动定位
    规则提出候选 + 证据 + 假设
    ├─ 满足退出条件 → 输出 LocateResult
    └─ 否则 → 扩大检索 / LLM 判定
 ④ 修复规格 RepairSpec：触发条件、正确行为、保持条件、验证方法
 ⑤ 补丁生成（候选 ≤ N）+ 行为审查
 ⑥ 分层验证 V0–V3，每层状态 PASS / FAIL / BLOCKED / SKIPPED
    ├─ 成功：保存补丁、验证证据、指标
    ├─ 失败：按原因分流（见第 6 节），回滚到基准源码后重试
    └─ 阻塞：记录状态和原因，不计为成功或失败
 ⑦ 指标记录：计入全部调用、候选补丁和重试
```

## 3. 模块
| 模块 | 输入 → 输出 | 关键做法 | LLM |
|---|---|---|---|
| ingest | `.log`/`.zip` → `CrashContext` | 兼容路径分隔符、`file:` 前缀、缺失字段；过滤系统帧 | 否 |
| retrieve | 栈帧 + 源码 → 代码片段 | 按需逐级扩展；映射状态区分 `UNIQUE` / `MULTIPLE` / `NOT_FOUND` | 否 |
| locate | 上下文 → `LocateResult` | 规则产出候选与证据；不满足退出条件时交给 LLM | 按需 |
| spec | `LocateResult` → `RepairSpec` | 规则模板 + LLM 补全业务语义 | 按需 |
| repair | `RepairSpec` → 候选 `Patch` | 生成 unified diff；行为审查 | 是 |
| verify | `Patch` → `VerifyResult[]` | V0/V1 本机，V2/V3 设备侧 | 否 |
| orchestrator | 全流程 | 失败分流、回滚、预算控制 | 否 |
| metrics | 全流程 → `metrics.json` | 口径见第 8 节 | 否 |

## 4. 核心数据结构
- `CrashContext`：`error_name`、`error_message`、`frames[]`（`file`、`line`、`col`、`func`、`is_business`）、`page_history[]`、`hilog_key_lines[]`
- `SourceMapping`：`frame` → `status`（`UNIQUE`/`MULTIPLE`/`NOT_FOUND`）、`candidates[]`
- `LocateResult`
  - `crash_location`：异常暴露位置
  - `root_cause_locations[]`：根因位置集合（`file`、`start_line`、`end_line`、`role`，如 `data_source` / `missing_check`），按可能性排序，第一个作为主答案
  - `evidence[]`：日志、源码、调用关系证据
  - `assumptions[]`：尚未证实的推断
  - `score`：**启发式评分，不是正确概率**
  - `source_hash`：定位所依据的原始源码哈希
- `RepairSpec`：`trigger`、`expected_behavior`、`preserve`、`verification`
- `Patch`：`diff`、`patch_hash`、`base_source_hash`、`rationale`
- `VerifyResult`：`layer`、`status`（`PASS`/`FAIL`/`BLOCKED`/`SKIPPED`）、`reason`、`evidence`、`elapsed_s`、`run_id`

## 5. 根因回溯规则（提出候选，不直接定论）
| 错误特征 | 崩溃点 | 候选根因方向 | 需排除的其他假设 | 案例 |
|---|---|---|---|---|
| 读 undefined 属性，来自数组下标 | `list[i].x` | 同一函数内对 `i` 不完整的边界检查 | 数组含空元素、上游加载失败 | ComprehensiveTool1：83→78 |
| JSON 解析失败 | `JSON.parse(x)` | `x` 的数据定义（Mock、接口返回） | 解析分支本身写错 | HomeStay1 |
| 正则语法错误 | `new RegExp(x)` | 模式字符串的定义或拼接 | 用户输入未转义 | B3 正则类 |
| TypedArray 字节不对齐 | `new Uint32Array(buf)` | `buf` 的构造方式 | — | WebShortDrama1 |
| 公共库内报错 | 栈顶在 `.js` 公共库 | 第一个业务调用方传入的参数 | 公共库自身缺陷 | EnterpriseRecruitment2 |
| 无规则匹配 | — | LLM + 检索片段 | — | 兜底 |

**直接输出的退出条件**（初版，按评测结果调整）：栈顶帧映射 `UNIQUE`；规则命中且证据链闭合；需排除的假设都已被源码证据排除。

## 6. 失败分流与预算
| 失败原因 | 回到 |
|---|---|
| 补丁无法应用、编译错误 | repair |
| 原故障仍存在 | locate 或 repair |
| 出现功能回归 | spec + repair |
| 设备、签名、环境、测试前置条件异常 | 标记 `BLOCKED`，不重试 |
| 定位证据不足 | retrieve 扩展上下文 |

- 每轮重试前回滚到 `base_source_hash` 对应的源码，补丁不叠加。
- 统一预算：候选补丁数、重试轮数、LLM 调用数、总 token、总时长，任一超限即停止并记录。

## 7. 验证分层与证据边界
| 层 | 内容 | 位置 | 能证明 | 不能单独证明 |
|---|---|---|---|---|
| V0 | 补丁可应用、行为审查 | 本机 | 修改可应用并满足基本约束 | 工程可编译 |
| V1 | 抽取逻辑到 Node：修复前复现、修复后不复现、正常输入差分一致 | 本机 | 抽取逻辑在指定输入下符合预期 | 鸿蒙运行行为正确 |
| V2 | hvigor 编译 | 设备侧 | 工程通过工具链检查 | 运行稳定、功能正确 |
| V3 | 模拟器运行 Hypium 回归 | 设备侧 | 指定操作和回归场景通过 | 所有场景无问题 |

- **完整修复成功 = V2 PASS 且 V3 PASS**。V1 不适用时记为 `SKIPPED`；设备不可用时报告"尚未完成运行验证"。
- 设备侧交接包：`run_id`、源码哈希、补丁哈希、工具链与模拟器版本、测试脚本版本。

## 8. 评测
- **允许清单隔离**：诊断端只拿到应用源码目录（白名单后缀）和日志；测试脚本与标准答案留在验证端。测试输出回传给模型前，过滤 `fault_file`、`fault_line`、`root_cause` 等字段。
- 标准答案：`resources/ground_truth.json`，人工核验，支持位置集合。
- 指标口径：

| 指标 | 口径 |
|---|---|
| 定位准确率 | 对照核验后的根因位置集合，分别统计精确位置、函数、文件命中；同时报告主答案和位置集合 |
| 首次通过率 | 第一个候选补丁通过完整验证的比例 |
| 最终成功率 | 预算内至少一个补丁通过完整验证的比例 |
| 资源消耗 | 计入全部失败调用、候选补丁和重试 |
| 阻塞案例 | 单独列出数量与原因；每个指标公开分母 |

- 泛化：留一法评测规则；P2 用故障变异生成 OOD 测试集。

## 9. LLM 调用层
- Kimi K3，OpenAI 兼容接口；`MOONSHOT_API_KEY`、`MOONSHOT_BASE_URL`、`LLM_MODEL` 从 `.env` 读取。
- 每次调用记录 token、耗时、用途、`run_id`；保存完整请求与响应。K3 据称不支持 temperature/seed，同一输入多次运行评估稳定性。

## 10. 目录结构
```
src/dfx_repair/{ingest,retrieve,locate,spec,repair,verify,orchestrator,llm,metrics}/ + cli.py
skill/dfx-fault-repair/{SKILL.md,scripts/,references/}
eval/{inventory.py,sandbox.py,score.py}
tests/
resources/{cases.json,ground_truth.json}
docs/  sessions/  plan/
```

## 11. 不做的事
- 第一版不做完整的 ArkTS 跨文件数据流分析，改用按需检索。
- 多 agent 角色分工：增加 token 和链路长度。
- 训练或微调模型：样本约 26 个。
