# 输出结构与采纳规则

## LocateResult

定义：`src/dfx_repair/locate/result.py`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | str | 案例目录名 |
| `method` | str | `stack_top` / `rules` / `rules+llm` |
| `crash_location` | Location \| null | 对齐后的崩溃行，`role = crash_site` |
| `root_cause_locations` | Location[] | 第一项为主答案；`rules+llm` 中依次为 LLM 答案、规则答案、崩溃行（去重），跨行语句另附 `statement_span` |
| `evidence` | str[] | 错误消息、栈帧、规则证据、LLM 引用与推理摘要 |
| `assumptions` | str[] | 未验证的前提；退回规则时含 `llm fallback: <原因>` |
| `score` | float | 规则启发式分数，不是概率 |
| `status` | str | `CANDIDATE` / `UNRESOLVED`；评测中另有 `BLOCKED`、`ERROR` |
| `context_requests` | str[] | 还需要补充的证据 |
| `unresolved_candidates` | Location[] | 无法唯一确定时的备选位置，不参与评分 |

`Location`：`file`（相对 `app/` 的路径）、`start_line`、`end_line`、`role`。

## LLM `submit` 字段

| 字段 | 说明 |
|---|---|
| `hypothesis` | `crash_line` 或 `upstream`（只记录，不参与判断） |
| `file`、`line`、`quoted_line` | 答案位置与该行原文 |
| `evidence_file/line/quote` | 支撑答案的另一行（约束、定义、使用或同类对照） |
| `origin_file/line/quote` | 失败值产生或最后赋值的行 |
| `reasoning` | 推理摘要 |

## 采纳规则（确定性代码，`src/dfx_repair/locate/llm.py`）

- `quote_matches`：`file:line` 原文去首尾空白后等于 `quoted_line`。
- `evidence_matches`：证据行不同于答案行，且原文相符。
- `origin_matches`：只记录。

采纳条件为 `quote_matches`，且答案就是崩溃行或 `evidence_matches` 为真；否则退回规则答案。是否为崩溃行按位置判断，不看 `hypothesis`。

## 预算（`LlmConfig` 默认值）

| 项 | 值 |
|---|---|
| 模型 | `claude-opus-5-5` |
| 最大轮数 | 14（剩 3 轮时提示收尾） |
| token 上限 | 160,000（input + output + 缓存读写；80% 时提示立即提交） |
| 单次超时 / 尝试次数 | 900 s / 2 |
| `read_file` 单次行数 | 150 |
| `search` 结果条数 | 40 |
| 日志字符数 / 崩溃行前后窗口 | 5000 / 前 40 行、后 15 行 |

超出上限前已提交的答案仍按上述规则核对，通过则保留并标记 `over_budget`。
