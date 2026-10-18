# Task Plan: B题 应用代码故障定位与自修复框架

## Goal
从 `jscrash.log` + ArkTS 源码出发，自动输出根因行、修复补丁和验证结果，并按比赛模板交付源码、SKILL、技术设计、测试报告和 sessions。

设计细节见 `plan/framework-design.md`，调研与证据见 `plan/notes.md`。

## Phases

### Phase 0: 计划与设计（当前）
- [x] 读题、读模板、盘点数据集结构
- [x] 手工走通 ComprehensiveTool1（定位 83→78，修复方案 A，Node 差分测试 25/25 一致）
- [x] 确定框架设计（`plan/framework-design.md`）
- [x] 确定 LLM：Kimi K3（API 调用）
- [x] 确定设备验证：队友机器装 DevEco + 模拟器
- [ ] 用户确认本计划

### Phase 1: 工程骨架与数据盘点（P0）
- [ ] 1.1 按模板在仓库根目录建 `src/ skill/ docs/ sessions/ tests/ resources/ eval/`，用 `uv` 初始化 Python 项目
- [ ] 1.2 `.gitignore` 加入 `.env`、`eval/sandbox/`、`eval/runs/`；新建 `.env.example`（`MOONSHOT_API_KEY`、`MOONSHOT_BASE_URL`、`LLM_MODEL`）
- [ ] 1.3 盘点脚本 `eval/inventory.py`：列出全部故障案例（app 目录、日志路径、测试脚本路径），输出 `resources/cases.json`；精确核对案例数（预计约 26 个）
- [ ] 1.4 标准答案 `resources/ground_truth.json`：从测试脚本 `META`/文档注释、B3 注入报告提取 `fault_file`、`fault_line`、根因说明；**逐条人工核对**
- 验收：`cases.json` 每条都能找到日志和源码；`ground_truth.json` 覆盖全部有故障案例，缺失项标明原因

### Phase 2: 日志解析 + 代码索引（P0）
- [ ] 2.1 `src/dfx_repair/ingest/`：解析 `.log`/`.zip` → `CrashContext`（错误名/消息、业务栈帧、页面切换记录、HiLog 关键行）；兼容 `\`/`/` 路径、`file:` 前缀、字段缺失、ZIP 内非日志文件
- [ ] 2.2 `src/dfx_repair/index/`：栈帧路径 → 源码文件映射；函数起止行；import 关系
- [ ] 2.3 单元测试：所有案例日志可解析；栈顶业务帧都能映射到存在的源码文件
- 验收：`pytest tests/` 通过；映射失败的案例列入 notes 并说明原因

### Phase 3: 评测打分器 + 栈顶基线（P0）
- [ ] 3.1 `eval/sandbox.py`：把案例复制到隔离目录，删除 `testcases/ docs/ Prompt.md reports/ HypiumProject/` 等含答案内容
- [ ] 3.2 `eval/score.py`：精确行命中、±3 行命中、文件命中；token、LLM 调用次数、耗时汇总
- [ ] 3.3 跑"只报栈顶业务帧"基线，结果写入 `eval/runs/<时间戳>/` 和 notes
- 验收：得到基线三项准确率；已知 ComprehensiveTool1 精确行未命中（83≠78）

### Phase 4: 规则定位（P0）
- [ ] 4.1 `src/dfx_repair/locate/rules/`：按错误类型实现回溯规则（数组下标→边界检查、JSON.parse→数据来源、RegExp→模式来源、TypedArray→buffer 来源、公共库→业务调用方）
- [ ] 4.2 `confidence.py`：规则命中情况 → 置信度分数
- [ ] 4.3 留一法评测，避免规则只记住 27 个案例
- 验收：精确行命中率高于基线；每条规则至少有一个案例和一个单元测试

### Phase 5: LLM 接入 + 修复生成（P0）
- [ ] 5.1 `src/dfx_repair/llm/`：OpenAI 兼容客户端（Kimi K3），从 `.env` 读配置，记录每次调用的 token 与耗时
- [ ] 5.2 低置信案例走 LLM 定位，只送切片代码
- [ ] 5.3 `src/dfx_repair/repair/`：修复模板 + LLM 生成 2–3 个候选 diff；补丁审查规则（禁止 try-catch 吞异常、禁止可选链掩盖、禁止删除业务逻辑）
- 验收：每个案例产出至少一个能 apply 的补丁；token 统计写入 metrics

### Phase 6: 本地验证 V0/V1（P0）
- [ ] 6.1 V0：补丁可应用 + 语法检查
- [ ] 6.2 V1：抽取崩溃函数到 Node 执行，修复前复现、修复后不复现、正常输入差分一致；错误消息归一化表（ArkCompiler ↔ V8）
- [ ] 6.3 失败反馈回修复，最多 3 轮
- 验收：报告"可局部复现的案例数"和"V1 通过率"；不可复现的案例说明原因

### Phase 7: 设备侧验证（P1，依赖队友环境）
- [ ] 7.1 写设备侧验证器 `src/dfx_repair/verify/device_runner.py`：输入补丁 + 案例编号，执行 hvigor 编译 + Hypium 回归，输出结果 JSON
- [ ] 7.2 队友环境验收：模拟器上复现 WebShortDrama1 崩溃（见 notes 安装清单）
- [ ] 7.3 全量案例跑 V2/V3，结果回传
- 验收：每个案例有编译和运行结果；失败案例有原因

### Phase 8: Skill + 文档交付（P1）
- [ ] 8.1 `skill/dfx-fault-repair/SKILL.md` + `scripts/` + `references/`（规则库、修复模板、消息归一化表）
- [ ] 8.2 `docs/technical-design.md`、`docs/test-report.md`（含 OOD、性能、资源消耗）
- [ ] 8.3 `sessions/` 按模板整理开发对话记录
- [ ] 8.4 `README.md` 填写队伍信息与运行说明

### Phase 9: 增强（P2）
- [ ] 故障变异生成 OOD 测试集
- [ ] 经验记忆（成功修复 → 规则库）
- [ ] 故障证据图（页面记录、HiLog 组件信息 → 缩小搜索范围）

## Key Questions
1. 比赛截止日期？决定 P1/P2 能否做完。
2. 提交仓库就是本仓库，还是另有 gitcode 队伍仓库？默认：在本仓库根目录按模板结构开发，`template-master/` 只作参考。
3. Kimi K3 的官方 base URL（`api.moonshot.cn` 或 `api.moonshot.ai`）和计费，需在控制台确认。
4. 比赛是否限定模型？（待在群或 FAQ 确认）

## Decisions Made
- 定位以规则为主、LLM 兜底：大多数案例栈顶附近就是答案，按置信度分级能省 token。
- 定位输出"故障行"，不是"能修的行"：评分认的是注入故障的那一行（ComprehensiveTool1：78 而非 83 或 156）。
- 验证分四层（V0 补丁 / V1 局部复现 / V2 编译 / V3 设备），V2/V3 在队友机器上运行。
- LLM 用 Kimi K3，通过 OpenAI 兼容接口调用，模型名和地址放配置，不写死。
- 评测在隔离沙箱进行，测试脚本和注入文档只给打分器用，防止泄题。
- 不做多 agent 角色分工：会增加 token 和链路长度，而这两项是评分里的扣分项。

## Errors Encountered
- `unzip` 不可用：改用 Python `zipfile` 解压 B3 日志。
- B3 部分 ZIP 内路径用 `\`，解压后成为带反斜杠的文件名；`TC_JSError_018` 的 ZIP 里是 `MainEntry.ets` 而不是日志。→ Phase 2 解析器需要处理。

## Status
**Phase 0 收尾** — 计划已写好，等待用户确认后进入 Phase 1。
