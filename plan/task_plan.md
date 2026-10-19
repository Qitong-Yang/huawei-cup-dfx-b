# Task Plan: B题 应用代码故障定位与自修复框架

## Goal
从 `jscrash.log` + ArkTS 源码出发，自动输出根因位置、修复补丁和分层验证结果，并按比赛模板交付源码、SKILL、技术设计、测试报告和 sessions。

设计见 `plan/framework-design.md`（v0.2，已合并 `plan/框架设计改进建议.md`），证据见 `plan/notes.md`。

## Phases

### Phase 0: 计划与设计
- [x] 读题、读模板、盘点数据集结构
- [x] 手工走通 ComprehensiveTool1（定位 83→78，修复方案 A，Node 差分 25/25 一致）
- [x] 框架设计 v0.1 → v0.2（合并七项改进建议）
- [x] LLM：Kimi K3（API）；设备验证：队友机器
- [x] 用户确认开工

### Phase 1: 工程骨架与数据盘点（P0）
- [x] 1.1 按模板在仓库根目录建 `src/ skill/ docs/ sessions/ tests/ resources/ eval/`，`uv` 初始化 Python 项目
- [x] 1.2 `.gitignore` 加入 `.env`、`.venv/`、`eval/sandbox/`、`eval/runs/`；新建 `.env.example`
- [x] 1.3 `eval/inventory.py`：列出全部故障案例（app 目录、日志、测试脚本），输出 `resources/cases.json`，精确核对案例数
- [x] 1.4 `resources/ground_truth.json`：从测试脚本 `META`/注释、B3 注入报告提取根因位置**集合**；逐条核对源码，标注来源和存疑项
- 验收：每个案例都有日志和源码；标准答案覆盖全部故障案例，缺失和存疑项写明原因

### Phase 2: 日志解析 + 按需检索（P0）
- [x] 2.1 `ingest/`：`.log`/`.zip` → `CrashContext`；兼容路径分隔符、`file:` 前缀、字段缺失、ZIP 内非日志文件
- [ ] 2.2 `retrieve/`：栈帧 → 源码映射（`UNIQUE`/`MULTIPLE`/`NOT_FOUND`）；按级别取语句、函数、调用者、数据定义，带预算上限
- [ ] 2.3 单元测试：全部日志可解析；统计各映射状态的案例数
- 验收：`pytest` 通过；`MULTIPLE`/`NOT_FOUND` 案例记录到 notes

### Phase 3: 评测隔离 + 打分器 + 栈顶基线（P0）
- [x] 3.1 `eval/sandbox.py`：**允许清单**复制（源码白名单后缀 + 日志），测试脚本和答案不进入诊断端
- [x] 3.2 `eval/score.py`：精确位置/函数/文件命中（主答案与位置集合分别统计）；资源消耗含失败调用；阻塞案例单列；公开分母
- [x] 3.3 跑"只报栈顶业务帧"基线，结果写入 `eval/runs/<run_id>/` 和 notes
- 验收：得到基线指标；ComprehensiveTool1 精确位置应未命中（83≠78）

### Phase 4: 证据驱动定位（P0）
- [~] 4.1 `locate/rules/`：每条规则输出候选位置、证据、需排除的假设、待读代码
- [ ] 4.2 退出条件：映射 `UNIQUE` + 证据链闭合 + 假设已排除 → 直接输出；否则扩大检索或交给 LLM
- [ ] 4.3 留一法评测
- 验收：精确位置命中率高于基线；每条规则有案例和单元测试

### Phase 5: LLM 接入 + 修复规格 + 补丁生成（P0）
- [ ] 5.1 `llm/`：Kimi K3 客户端，记录 token、耗时、用途、`run_id`，保存完整请求/响应
- [ ] 5.2 LLM 定位：只送检索片段，输出同 `LocateResult` 结构
- [ ] 5.3 `spec/`：生成 `RepairSpec`（触发条件、正确行为、保持条件、验证方法）
- [ ] 5.4 `repair/`：按 `RepairSpec` 生成候选 diff；行为审查（吞错误、删功能、无条件假结果 → 拒绝）
- 验收：每个案例至少一个可应用补丁；`RepairSpec` 与补丁一同存档

### Phase 6: 本地验证 + 编排（P0）
- [ ] 6.1 V0：补丁可应用 + 行为审查
- [ ] 6.2 V1：Node 局部复现 + 差分；错误消息归一化表；不适用时 `SKIPPED` 并写原因
- [ ] 6.3 `orchestrator/`：失败分流表、每轮回滚到基准源码、统一预算（候选数、轮数、调用数、token、时长）
- 验收：每个案例每层都有 `PASS/FAIL/BLOCKED/SKIPPED` 状态和原因

### Phase 7: 设备侧验证（P1，依赖队友环境）
- [ ] 7.1 `verify/device_runner.py`：读交接包（`run_id`、源码哈希、补丁哈希、版本），执行 hvigor 编译 + Hypium，输出结果 JSON
- [ ] 7.2 队友环境验收：模拟器复现 WebShortDrama1 崩溃
- [ ] 7.3 全量 V2/V3；测试输出回传前过滤答案字段
- 验收：完整成功 = V2 PASS 且 V3 PASS；阻塞案例单列

### Phase 8: Skill + 文档交付（P1）
- [ ] 8.1 `skill/dfx-fault-repair/`：SKILL.md、scripts、references（规则库、修复模板、消息归一化表）
- [ ] 8.2 `docs/technical-design.md`、`docs/test-report.md`
- [ ] 8.3 `sessions/` 开发对话记录
- [ ] 8.4 `README.md` 队伍信息与运行说明

### Phase 9: 增强（P2）
- [ ] 故障变异 OOD 测试集
- [ ] 经验记忆（成功修复 → 规则库）
- [ ] 故障证据图（页面记录、HiLog 组件 → 缩小检索范围）

## Key Questions
1. 比赛截止日期？
2. 提交仓库是否就是本仓库？默认：本仓库根目录按模板开发，`template-master/` 只作参考。
3. Kimi K3 官方 base URL 与计费（需控制台确认）。
4. 比赛是否限定模型？

## Decisions Made
- 规则只提出候选，证据充分才直接输出；评分是启发式，不当作概率。
- 定位输出崩溃位置 + 根因位置集合，评分以原始源码为基准。
- 修复前先生成 `RepairSpec`；补丁审查看行为，不禁 try/catch、可选链语法。
- 验证状态四值；完整成功 = V2 + V3 通过；V2/V3 在队友机器。
- 失败按原因分流，重试前回滚，统一预算。
- 评测用允许清单隔离；资源消耗计入所有失败调用。
- LLM 用 Kimi K3，地址和模型名放配置。
- 不做多 agent 分工、模型微调、完整跨文件数据流分析。

## Errors Encountered
- `unzip` 不可用：改用 Python `zipfile`。
- B3 ZIP 路径混用 `\`；`TC_JSError_018` 的 ZIP 内是 `MainEntry.ets` 而非日志。→ 已处理：按 ZIP 成员读取；018 排除。
- UTF-16 日志解析为空 → 加 BOM 检测解码。
- `inventory.py` 文本替换误把 `primary_log` 插入 B3 生成器 → 已删除该行。
- 测试期望写错（后缀匹配段数 2→3）→ 已改正测试。

## Status
**Phase 4 进行中**（2026-10-19）。规则定位 `eval/runs/rules_20261019_062946/`：exact 21/25（基线 18）、range 23/25、function 23/25、file 24/25；0 次 LLM 调用。
已实现：行号对齐（align）、R-BOUNDS-PARTIAL、R-BOUNDS-LOOP；其余 22 个案例规则均未触发（无误报）。
剩余 4 个精确未命中都需要跨文件追数据（HomeStay1、ComprehensiveNews1 003、Exam1、Notes1）→ 计划用"规则缩小范围 + LLM 判定"（Phase 5，需要 Kimi API key）。
