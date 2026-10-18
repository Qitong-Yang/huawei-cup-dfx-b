# 框架设计：DFX 故障定位与自修复

> 状态：v0.1 草案（2026-10-18）。确认后同步到 `docs/technical-design.md` 第 2、3 节。

## 1. 设计原则
1. **规则优先，LLM 兜底**：能用规则确定的步骤不调用 LLM，降低 token 消耗和链路长度。
2. **定位"故障行"而非"崩溃行"**：从崩溃点沿数据流回溯，找到写错的那一行。
3. **修根因，不掩盖**：禁止用 try-catch 吞异常、用可选链跳过错误。
4. **验证由便宜到昂贵**：前一层不通过就不进入下一层。
5. **评测防泄题**：框架只能看到源码和日志。

## 2. 整体流程
```
输入：jscrash.log + 应用源码（隔离沙箱）
 ① 日志解析 → CrashContext
 ② 代码索引 → 栈帧映射、函数边界、import 关系
 ③ 根因定位
    第 1 级 规则：栈顶帧 + 按错误类型回溯 → 置信度够高则直接输出
    第 2 级 LLM：只送切片代码
    第 3 级 LLM：扩大上下文
    输出：文件:行、置信度、根因说明、证据链
 ④ 修复生成：修复模板 + LLM，2–3 个候选 diff → 补丁审查
 ⑤ 分层验证：V0 补丁/语法 → V1 局部复现+差分 → V2 hvigor 编译 → V3 Hypium 回归
    失败 → 错误信息反馈 ④，最多 3 轮
 ⑥ 记录：定位/修复结果、token、LLM 调用次数、各阶段耗时
```

## 3. 模块
| 模块 | 输入 → 输出 | 关键做法 | LLM |
|---|---|---|---|
| ① ingest | `.log`/`.zip` → `CrashContext` | 兼容路径分隔符、`file:` 前缀、缺失字段；过滤系统帧 | 否 |
| ② index | 源码 → 索引 | 函数起止行、import 图、符号定义与引用 | 否 |
| ③ locate | 上下文 + 索引 → 候选行 | 列号锁定表达式，按错误类型规则回溯 | 低置信时 |
| ④ repair | 根因 → unified diff | 模板 + LLM；沿用原代码风格；补丁审查 | 是 |
| ⑤ verify | diff → 各层结果 | V0/V1 本机，V2/V3 队友机器 | 否 |
| ⑥ metrics | 全流程 → `metrics.json` | 对应评分资源消耗指标 | 否 |

### 核心数据结构
- `CrashContext`：`error_name`、`error_message`、`frames[]`（`file`、`line`、`col`、`func`、`is_business`）、`page_history[]`、`hilog_key_lines[]`
- `Candidate`：`file`、`line`、`confidence`、`rule_id`、`evidence[]`、`root_cause`
- `Patch`：`diff`、`strategy`、`rationale`
- `VerifyResult`：`layer`、`passed`、`detail`、`elapsed_s`

## 4. 根因回溯规则库（Skill `references/` 主体）
| 错误特征 | 崩溃点 | 往哪找根因 | 案例 |
|---|---|---|---|
| 读 undefined 属性，来自数组下标 | `list[i].x` | 同一函数内往上找对 `i` 不完整的边界检查 | ComprehensiveTool1：83→78 |
| JSON 解析失败 | `JSON.parse(x)` | 追 `x` 来源至数据定义（Mock、接口返回） | HomeStay1 |
| 正则语法错误 | `new RegExp(x)` | 追模式字符串的定义与拼接 | B3 正则类案例 |
| TypedArray 字节不对齐 | `new Uint32Array(buf)` | 追 `buf` 的构造 | WebShortDrama1 |
| 公共库内报错 | 栈顶在 `.js` 公共库 | 跳到第一个业务调用方，检查参数 | EnterpriseRecruitment2 |
| 无规则匹配 | — | LLM + 切片 | 兜底 |

## 5. 验证分层
| 层 | 内容 | 位置 | 适用范围 |
|---|---|---|---|
| V0 | 补丁可应用、语法检查、补丁审查规则 | 本机 | 全部 |
| V1 | 崩溃函数抽到 Node：修复前复现、修复后不复现、正常输入差分一致 | 本机 | 纯逻辑函数 |
| V2 | hvigor 编译（ArkTS 严格类型检查） | 队友机器 | 全部 |
| V3 | 模拟器运行 Hypium 回归脚本 | 队友机器 | 全部 |

V2/V3 由设备侧验证器执行：输入补丁 + 案例编号，输出结果 JSON，通过 git 同步。

## 6. LLM 调用层
- Kimi K3，OpenAI 兼容接口；`MOONSHOT_API_KEY`、`MOONSHOT_BASE_URL`、`LLM_MODEL` 从 `.env` 读取。
- 每次调用记录 prompt/completion token、耗时、用途（定位/修复/重试），写入 `metrics.json`。
- K3 据称不支持 temperature/seed：保存完整请求与响应以便复查，同一输入多次运行评估稳定性。

## 7. 评测
- 沙箱：复制案例，删除 `testcases/`、`docs/`、`Prompt.md`、`reports/`、`HypiumProject/`。
- 标准答案：`resources/ground_truth.json`，只给打分器使用。
- 指标：精确行命中、±3 行命中、文件命中；修复成功率、一次通过率；平均 token、LLM 调用次数、耗时。
- 泛化：留一法评测规则；P2 用故障变异生成 OOD 测试集。

## 8. 目录结构
```
src/dfx_repair/{ingest,index,locate,repair,verify,llm,metrics}/ + cli.py
skill/dfx-fault-repair/{SKILL.md,scripts/,references/}
eval/{inventory.py,sandbox.py,score.py}
tests/
resources/{cases.json,ground_truth.json}
docs/  sessions/  plan/
```

## 9. 不做的事
- 多 agent 角色分工：增加 token 和链路长度。
- 训练或微调模型：样本只有约 26 个。
- try-catch 包裹式修复：功能正确性会失分。
