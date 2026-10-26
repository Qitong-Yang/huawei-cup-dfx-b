# 测试报告

> 队伍名称：天天向上队
> 测试人员：杨启桐，刘思源，乔义杰
> 版本：v1.0
> 日期：2026-10-26

---

## 1. 测试概述

### 1.1 测试范围

已测：

- 日志解析、源码映射、规则定位、LLM 定位（含引用核对与退回）、沙箱隔离、评分与批处理的单元/回归测试；
- 三种定位方法（栈顶基线、规则、规则 + LLM）在官方 `data_B` 上的端到端定位评测。

未测（功能未实现）：修复方案生成、补丁应用、鸿蒙编译、设备运行与功能回归。因此赛题中"代码修复效果"和"修复方案通过率"两项**没有测试数据**。

### 1.2 测试环境

| 项目 | 配置 |
|------|------|
| 操作系统 | Ubuntu 24.04.4 LTS（WSL2，内核 6.6.87.2）；Phase 4 回归另在 Windows 上复核 |
| 运行时版本 | Python 3.10.22（uv 管理）；Claude Code CLI 2.1.310 |
| 硬件配置 | Intel Core i7-13650HX，WSL 分配内存 11 GB |
| 依赖版本 | pytest 9.1.1，ruff 0.17.1；依赖锁定在 `uv.lock` |
| 模型 | Claude Opus 5.5（`claude-opus-5-5`），经 `claude -p` 本地订阅调用，内置工具关闭，只开放本项目 MCP 工具 |

### 1.3 数据与标准答案

- 数据：`data-master/data_B`，共 27 个故障案例（B1 10 个、B2 6 个、B3 11 个）。
- 排除 2 个，不计入分母：
  - `TC_JSError_002@ComprehensiveNews1@B2`：源码中不存在该故障，日志第 48 行是 `} else {`；该目录下的日志实际属于 003 号案例。
  - `TC_JSError_018@ImageProcessing1@B3`：压缩包内是 `MainEntry.ets`，没有崩溃日志。
- 可评分 25 个。标准答案 `resources/ground_truth.json` 由测试脚本元数据和注入报告提取后逐条人工复核（`plan/gt_review.md`）。判定原则：某行算精确命中，需同时满足 ① 属于注入的改动；② 只改这一行就能消除崩溃且不破坏正常功能。只满足其一的行计入 range。
- 标准答案、测试脚本、注入报告不进入诊断沙箱。

### 1.4 复现命令

```bash
uv sync --locked --group dev
uv run pytest -q
uv run ruff check src eval tests
uv run python -X utf8 -m eval.run_baseline --method stack_top
uv run python -X utf8 -m eval.run_baseline --method rules
uv run python -X utf8 -m eval.run_baseline --method rules+llm   # 需要已登录的 claude CLI
```

---

## 2. 测试用例

### 2.1 功能测试：定位准确率

正式运行：`eval/runs/rules+llm_20261023_125333_196639_8334ad2b/`（代码 `a6894f7`，prompt v4 `12e096761c18`）。

**汇总（分母 25）：**

| 指标 | 栈顶基线 | 规则 | 规则 + LLM |
|---|---|---|---|
| exact | 18（72%） | 21（84%） | **24（96%）** |
| range | 21 | 23 | **25（100%）** |
| function | 22 | 22 | **24** |
| file | 24 | 23 | **25** |
| top-3 exact | — | — | 25 |
| CANDIDATE / UNRESOLVED | 25 / 0 | 24 / 1 | 25 / 0 |
| BLOCKED / ERROR | 0 / 0 | 0 / 0 | 0 / 0 |

**逐案例（规则 + LLM）：**

| 编号 | 案例 | 预测位置 | exact | 说明 |
|---|---|---|---|---|
| TC-001 | AccountingApp1 / 002 | `BillBarChart.ets:48` | ✅ | |
| TC-002 | BookRead2 / 004 | `BookViewListPage.ets:21` | ✅ | |
| TC-003 | Calendar1 / 007 | `Utils.ets:53` | ✅ | |
| TC-004 | ComprehensiveTool1 / 001 | `ResourceManageVM.ets:78` | ✅ | 崩溃行 83，根因是 78 行的不完整边界检查 |
| TC-005 | EnterpriseRecruitment2 / 009 | `PropUtil.js:3` | ✅ | |
| TC-006 | Express1 / 010 | `ExpressPageVM.ets:346` | ✅ | 未被辩护性注释误导 |
| TC-007 | MoneyTrack1_2 / 005 | `AssetAnalysisVM.ets:114` | ✅ | 崩溃行 110 |
| TC-008 | Music1 / 025 | `PlayerControlArea.ets:190` | ✅ | |
| TC-009 | Notes1 / 006 | `NoteService.ets:197` | ✅ | 规则层 UNRESOLVED；LLM 跨函数追到 197 行 |
| TC-010 | Teleprompter1 / 026 | `ConfigSheet.ets:232` | ✅ | |
| TC-011 | ChildrenEducation1 / 011 | `VideoPlayPage.ets:72` | ✅ | |
| TC-012 | ComprehensiveMall1 / 001 | `OrderInfoPage.ets:117` | ❌ | 答守卫行 117，标准答案为使用行 119；range、file 命中 |
| TC-013 | ComprehensiveNews1 / 003 | `MockArticle.ets:50` | ✅ | 崩溃在 `ArticleFeedDetails.ets`，根因在 mock 数据，跨文件 |
| TC-014 | MoneyTrack1 / 005 | `CalendarSummary.ets:54` | ✅ | |
| TC-015 | WebShortDrama1 / 024（RangeError） | `ShareView.ets:40` | ✅ | |
| TC-016 | HomeStay1.0.6 / 012 | `MockData.ets:11` | ✅ | 栈顶连文件都错；根因在 mock 数据 |
| TC-017 | DriverLicenseExam1.0.7 / 014 | `ExamService.ets:345` | ✅ | |
| TC-018 | FitnessCenter1.0.3 / 015 | `ResultPage.ets:163` | ✅ | |
| TC-019 | TouristAttraction1 / 016 | `CommonUtil.ets:10` | ✅ | |
| TC-020 | OfficeAttendance1 / 017 | `DataManager.ets:56` | ✅ | |
| TC-021 | Exam1 / 019 | `TopicModel.ets:40` | ✅ | 崩溃在 `MainPage.ets:65`，根因在 model，跨文件 |
| TC-022 | TeaDrinkOrders1 / 020 | `ConfirmOrderPage.ets:785` | ✅ | spike 中在 783/785 间摇摆过 |
| TC-023 | AudioEditing1 / 021 | `EditViewModel.ets:52` | ✅ | 崩溃行 58 |
| TC-024 | HouseholdService1 / 022 | `OrderRequestChecksum.ets:22` | ✅ | |
| TC-025 | WebShortDrama1 / 024（B3） | `FileUtils.ets:173` | ✅ | |

相对规则层：LLM 新答对 Notes1、ComprehensiveNews1、HomeStay1、Exam1（都需要跨函数或跨文件追数据）；丢了 ComprehensiveMall1（规则层答崩溃行 119 恰好命中）。

### 2.2 异常测试

| 编号 | 异常场景 | 输入 | 预期行为 | 实际行为 | 状态 |
|------|---------|------|---------|---------|------|
| ET-001 | 数据异常：日志编码 | UTF-16 带 BOM 的日志 | 正确解码并解析 | `test_decode_utf16_with_bom` 通过 | ✅ |
| ET-002 | 数据异常：手写/缺字段日志 | 无标准字段、含答案描述的日志 | 回退解析，剔除答案段落并标记泄漏 | `test_handwritten_log_falls_back_and_flags_leak` 等通过 | ✅ |
| ET-003 | 数据异常：ZIP 路径 | 成员名混用 `\` 与 `/`；成员名歧义 | 兼容分隔符；歧义时拒绝 | `test_zip_member_slash_variants_are_supported_but_ambiguity_rejected` 通过 | ✅ |
| ET-004 | 数据异常：缺日志 / 源码与日志不符 | ImageProcessing1 018、News1 002 | 排除并在报告中写明原因 | 报告列出 2 个排除案例及原因 | ✅ |
| ET-005 | 答案泄漏 | 源码或日志中含答案标记 | 该案例 `BLOCKED`，不发布副本 | `test_source_with_answer_marker_blocks_publication` 通过；普通标识符不误报 | ✅ |
| ET-006 | 路径越界 | `../`、绝对路径、指向外部的符号链接 | 工具返回错误文本，不读取 | `test_paths_outside_app_are_rejected` 等通过 | ✅ |
| ET-007 | LLM 编造引用 | `quoted_line` 与原文不符 | 退回规则答案并标注原因 | `test_fabricated_quote_falls_back_to_rules` 通过 | ✅ |
| ET-008 | LLM 后端失败 | 超时、进程异常、无答案 | 保留规则答案与 status | `test_backend_error_keeps_rule_answer_and_status` 等通过 | ✅ |
| ET-009 | 超预算 | 越过 160k 的调用中已提交答案 | 按核对保留并标记 `over_budget`（可配置丢弃） | `test_over_budget_answer_is_kept_and_marked` 通过 | ✅ |
| ET-010 | 规则不支持的语法 | Notes1 的模板内表达式 | 规则层 `UNRESOLVED`，不乱报 | 规则层 1 例 UNRESOLVED，保留在分母 | ✅ |
| ET-011 | 缓存篡改 | 修改源码、日志、清洗策略或副本内容 | 缓存失效并重建 | `test_cache_*` 系列通过 | ✅ |
| ET-012 | OOD 场景 | 未见过的应用或故障类型 | 定位能力可迁移 | **未测**：没有独立留出集 | ⚠️ |

### 2.3 性能测试：资源消耗（规则 + LLM，25 例）

| 指标 | 最小 | 中位数 | 最大 | 平均 | 合计 |
|------|------|------|------|------|------|
| token（含缓存读写） | 6,232 | 18,547 | 127,379 | 28,778 | 719,442 |
| LLM 耗时（s） | 11.7 | 20.6 | 51.9 | 22.3 | 556.4 |
| 工具调用轮数（agent 链路长度） | 1 | 3 | 8 | 3.3 | 82 |

- 缓存读占 token 的 72.4%。
- 最贵的两个案例：ComprehensiveNews1（127k token，8 轮，在 mock 数据中查找）、Express1（88k，7 轮）。
- 25 例全部采纳 LLM 答案：0 次退回、0 次超预算、0 次超时。
- 批次墙钟 562 s，包含沙箱准备。仓库位于 `/mnt/` 盘时副本读写约慢 7 倍，正式运行使用 Linux 文件系统上的副本。
- 规则层与栈顶基线不调用模型，耗时可忽略。
- 修复耗时：**未测**（未实现）。

---

## 3. 测试结果

### 3.1 汇总

| 指标 | 数值 |
|------|------|
| 自动化测试用例数 | 174 |
| 通过 / 失败 / 跳过 | 174 / 0 / 0 |
| Ruff | 通过 |
| 定位评测案例（可评分） | 25（另有 2 例因数据问题排除） |
| 精确定位命中 | 24 / 25（96.0%） |
| 修复成功率 / 修复方案通过率 | 未测（功能未实现） |

自动化测试 2026-10-26 在上述 WSL 环境复跑：`uv run pytest -q` 输出 `174 passed in 15.47s`；`uv run ruff check src eval tests` 输出 `All checks passed!`。单元测试使用假后端，不调用真实模型。

### 3.2 缺陷列表

| 编号 | 严重程度 | 描述 | 复现步骤 | 状态 |
|------|---------|------|---------|------|
| BUG-001 | 一般 | ComprehensiveMall1 答守卫行 117，标准答案为使用行 119。ComprehensiveTool1 的标准答案反而是守卫行，两种标注都存在，没有一种固定策略能两边都对 | 运行 `rules+llm`，查看 TC-012 | 已知限制，不再修补 |
| BUG-002 | 一般 | 同一 prompt 单次结果有波动：spike 中 TeaDrinkOrders1 4 次重复里只对 2 次；Express1 单案例 token 在 1.6 万–20 万之间 | 重复运行同一案例 | 已知限制 |
| BUG-003 | 轻微 | 词法助手不是完整 ArkTS 解析器，模板内表达式被遮蔽，规则层对这类代码只能 UNRESOLVED | 规则方法运行 Notes1 | 已知限制，由 LLM 层补 |
| BUG-004 | 轻微 | Phase 4 审查提出的 9 个反例（缓存、隔离、评分、批处理） | 见 `docs/phase4-review-results.json` | 已修复，已转为回归测试 |

### 3.3 未覆盖场景与有效性威胁

- **修复与验证全部未覆盖**：没有补丁生成、编译、设备运行、Hypium 回归，无法给出修复成功率、功能正确性、修复后稳定性、一次通过率。
- **无独立留出集**：25 个案例既用来比较和修改 prompt（v1→v4），也是评测集，96% 偏乐观。prompt 不含案例相关词汇，但这不能代替留出集。
- **单次运行**：正式数字来自一次完整运行，没有多次重复取均值。
- **标准答案主观性**：守卫行与使用行的取舍依赖人工判定（见 BUG-001）。
- **正式测试集分布未知**：是否与 `data_B` 同分布无法确认；`data_A` 未参与评测。
- **沙箱不是安全边界**：它过滤输入，不是操作系统级隔离。

---

## 4. 测试结论

定位链路完成，结果可复现：在 25 个可评分案例上，规则 + LLM 的精确定位 24/25，相关范围和文件 25/25，比栈顶基线多对 6 例；所有编造的引用都会被确定性核对拦下；资源消耗为平均 28.8k token、3.3 轮、22.3 s/案例。

这个数字只说明开发集上的定位能力。修复、运行验证和自修复迭代没有实现，赛题后两个评审维度目前没有可报告的结果。

### 遗留问题

| 问题 | 影响 | 建议 |
|------|------|------|
| 修复与验证未实现 | 赛题"代码修复效果""修复方案质量"两项无成绩 | 按技术设计第 5.1 节实现 RepairSpec → 补丁 → V0/V1 本地验证，再接设备 |
| 无留出集 | 定位准确率可能高估 | 用 `data_A` 或故障变异构造留出集，冻结 prompt 后评测一次 |
| 单次运行波动 | 个别案例结果不稳定 | 对高波动案例做多次重复并报告方差 |
| 提交版后端 | 正式数字来自本地 `claude -p` 订阅；OpenAI 兼容后端只在 spike 中验证 | 合入 OpenAI 兼容后端，用同一 prompt 与工具复跑 |

### 改进建议

- 悬空引用检查：数据类故障（News1、HomeStay1）大部分 token 花在找数据上，先用确定性检查缩小范围。
- 对守卫/使用二选一的情况，在 `root_cause_locations` 中同时给出两行，交给修复阶段按验证结果选择。
