# LLM 辅助定位阶段设计

日期：2026-10-22 状态：草案，待确认 分支：`feat/rule-layer` 之后的新分支

## 1. 目的

规则层（`locate_with_rules`）在 25 个可评分案例上 exact 21/25，其中只有 2 个是规则真正命中，其余靠“答崩溃行”兜底。答错的案例需要跨文件追数据（mock 数据、构造处、调用方）。本阶段在规则层之后加一个 LLM 定位步骤：读取沙箱里的源码，判断根因是崩溃行还是上游某一行，输出与规则层同一种 `LocateResult`，进入现有评分。

本阶段只做定位，不做修复和运行验证。

## 2. 目标、非目标、反目标

目标
- 新增定位方法 `rules+llm`，输出 `LocateResult`，可被 `eval/run_baseline.py` 和 `eval/score.py` 直接评分，评测表新增一列。
- 每个案例记录：答案位置、证据、LLM 调用次数、token（含缓存读写）、耗时、完整对话。
- prompt 冻结、带版本号和 hash；正式数字来自冻结 prompt 的一次完整运行。

非目标
- 修复补丁生成、设备上的运行验证（等服务器确认后另立设计）。
- 多模型投票、按报错类型分档调用 LLM（实测简单案例只花 4k–15k token，分档省不了多少，且分档规则是从这 25 个案例总结的）。
- 为提高 25 个案例的分数继续调 prompt。

反目标（做到了也算失败）
- prompt 里出现针对具体案例的规则或词汇（mock、author、logistic 等）。
- LLM 读到答案：`testcases/*.json`、`*.py` 文件头、`docs/故障注入报告-*.md`、`resources/ground_truth.json`。
- 编造的行号或引用进入最终答案。
- 规则答对的案例因 LLM 运行失败（超时、超预算、网络错误、无答案）而变成无答案。

## 3. 依据（spike 结果）

throwaway 脚本在 `temp/`，结果在 `eval/runs/llm_spike_*`。

| 配置 | exact | range | 说明 |
|---|---|---|---|
| 规则 | 21/25 | 23/25 | 基线 |
| Kimi + v1 prompt | 22/25 | 24/25 | Express1 被注释误导；News1 区分大小写搜索漏掉 `'Author_2'` |
| Kimi + v2 prompt | 23/25 | 25/25 | 4 个关键案例各重复 3 次：Express1 4/4、News1 4/4、Mall1 恒选 117、TeaDrinkOrders1 在 783/785 间摇摆；耗时合计 2325 s，809k token |
| Sonnet + v3 prompt（`claude -p`） | 22/25 | 23/25 | 耗时合计 463 s；查得浅，Notes1、News1 停在崩溃行 |
| Sonnet + v4 prompt，5 个关键案例 | 3/5 | — | News1、TeaDrinkOrders1、Mall1 对；Tool1、Notes1 错 |
| DeepSeek-Flash + v4 prompt（`claude -p`，无上限） | 22/25 | 23/25 | 耗时合计 2175 s，6.65M token（87% 缓存读）；Express1 单案例 1.38M token 仍答错；6 次越界读取均被拒 |
| DeepSeek-V4-Pro + v4 prompt（手写循环，160k 上限） | 23/25（含退回） | 22/25 | 23 个有答案中 21 对；Express1、News1 超上限无答案退回规则；HomeStay1、Exam1 在越线那次调用提交且答对；耗时合计 2560 s，1.55M token |

结论
- 证据核对（引用一行真实代码）25/25 全部通过，它能防编造，不能分辨对错。
- 守卫写错还是使用写错，标注本身两边都有：Mall1 认使用行 119，ComprehensiveTool1 认守卫行 78。没有固定策略能两边都对。
- token 集中在少数难题（单案例 10 万–25 万），需要上限。

## 4. 已决定的部分

### 4.1 位置与流程

```
crash.log ──parse──► CrashLog ──► locate_with_rules ──► LocateResult(rules)
                                                    │
                       sandbox app + 规则结果 ─────► LLM localizer ──► LocateResult(rules+llm)
```

- 每个案例都调用 LLM（不分档）。
- LLM 失败、超预算、答案未通过核对时，退回规则答案，`status` 和 `assumptions` 标明原因。

### 4.2 模型

- 模型：Claude Opus 5.5（`claude-opus-5-5`）。〔用户决定，2026-10-22，取代 Sonnet 5.5〕依据：Sonnet v4 答错的 3 个案例（ComprehensiveTool1、Notes1，以及 v3 只命中 range 的 ComprehensiveNews1），Opus 5.5 + v4 prompt（`claude -p`）3/3 exact；Sonnet 在前两个案例上 2–3 轮就停在崩溃行。3 个案例合计 tokens 295.5k 对 284.5k，耗时 112 s 对 137 s，按标价折算 $0.63 对 $0.25（`eval/runs/llm_spike_v4_opus_sonnetmiss_20261022/`）。这 3 个案例是按 Sonnet 答错挑出来的。25 个案例（同一 `claude -p` 内置工具 spike，`eval/runs/llm_spike_v4_opus_25case_20261022/`）：exact 24/25、range 25/25；比 Sonnet v3 多对 ComprehensiveTool1、Notes1、ComprehensiveNews1，丢 ComprehensiveMall1（选守卫行 117，标注为使用行 119，见第 5 节）；832k token、平均 27.0 s/案例、按标价折算 $2.28（Sonnet v3：465k、18.5 s、$0.86）。
- 此前的决定：Claude Sonnet 5.5（`claude-sonnet-5-5`）。〔用户决定，2026-10-22，取代先前的 Kimi K3〕依据：按赛题评审标准，定位准确率与 Kimi K3 持平，资源消耗（token 消耗量、agent 链路长度、平均定位耗时）明显更低。在两者都跑完的 12 个案例上：exact 10/12 对 10/12；平均 token 15.1k 对 26.8k；耗时 18.4 s 对 125.5 s。Kimi K3 的数字来自 v4 prompt、80k 上限、Kimi Code 订阅端点（`eval/runs/llm_spike_v4_kimi-k3_codeplan_80k_20261022/`，跑完 13/25 后中止）。
- Sonnet 的 22/25 来自 v3 prompt（`claude -p`）；冻结的 v4 prompt 在 Sonnet 上只跑过 5 个案例（3/5），正式数字以 v4 的 25 案例运行为准（见 4.11）。
- 模型名、base URL、API key 从配置读取，不写死。提交版：`LLM_BASE_URL=https://openrouter.ai/api/v1`、`LLM_MODEL=anthropic/claude-opus-5.5`、`LLM_API_KEY`（见 7.4）。spike 脚本仍兼容旧的 `MOONSHOT_*` 变量名。

后端：一个，OpenAI 兼容的 `/chat/completions` 手写工具循环，使用 4.4 的自定义工具（`temp/spike_llm.py` 已在 Kimi 与 DeepSeek-V4-Pro 上验证）。不再做 `anthropic` SDK 与 `claude -p` 后端。〔我的决定〕接口保持后端无关（输入 system prompt、初始消息、工具定义与工具执行函数、预算；输出答案、对话记录、用量），以后换模型只换配置或加后端。

请求参数（与 spike 一致）：
- body 只传 `model`、`messages`、`tools`、`max_tokens=16000`；不传 `temperature`/`top_p`/`tool_choice`，用默认值。
- 助手消息原样追加回历史，保留 `role`、`content`、`tool_calls`、`reasoning_content`（思考模型在工具循环中需要回传 `reasoning_content`）。
- 每个 `tool_call` 用一条 `role: "tool"` 消息返回结果；工具出错返回错误文本，不中断循环。
- 模型没有调用工具时追加一条“请调用 submit”的 user 消息，计入轮次。
- 用量取 `usage.total_tokens`（`prompt_tokens` 已含缓存命中）；缓存命中按返回的字段记录（DeepSeek 为 `prompt_cache_hit_tokens`，Kimi 以实际返回为准）。

### 4.3 输入

- 已脱敏的 `crash.log`（前 5000 字符）。
- 规则层输出：`status`、`crash_location`、`root_cause_locations`、`evidence`、`assumptions`、`context_requests`、`unresolved_candidates`。
- 崩溃行附近的带行号代码窗口（崩溃行前 40 行、后 15 行）。

### 4.4 工具（自定义，路径限制在 app 根目录内）

| 工具 | 行为 |
|---|---|
| `read_file(path, start_line, end_line)` | 返回带行号的源码，一次最多 150 行；路径越出 app 根目录或文件不存在时返回错误文本 |
| `search(pattern, path_contains?)` | 正则搜索，**默认不区分大小写**；只搜 `.ets/.ts/.js/.json/.json5`；最多返回 40 条，超出时注明“共 N 条，只显示 40 条”。每个案例开始时把源码读入内存一次，搜索只在内存中进行（spike 每次搜索都遍历 `/mnt/f` 上的文件，News1 单次 37.5 s，同一份文件放在 Linux 文件系统上只需 0.07 s；Kimi v2 运行中 27% 的耗时花在这里） |
| `submit(...)` | 提交答案，字段见 4.6 |

### 4.5 prompt

- 使用冻结的 v4 原文（`eval/runs/llm_spike_v4_sonnet_5case_20261022/system_prompt.txt` 中的分析规则 1–7 与输出要求），存为包内资源文件，例如 `src/dfx_repair/llm/prompts/localize_v4.txt`。工具名为 `read_file`/`search`/`submit`，与 `temp/spike_llm.py` 中的 v4 `SYSTEM` 一致，分析规则一字不改。
- 每次运行记录 prompt 版本和 SHA-256。
- 修改 prompt 的条件：理由不依赖任何具体案例；修改后版本号 +1；不得根据 25 个案例的结果反复迭代。

### 4.6 答案字段与核对

`submit` 字段：`hypothesis`（`crash_line`|`upstream`）、`file`、`line`、`quoted_line`、`evidence_file/line/quote`、`origin_file/line/quote`、`reasoning`。

核对（确定性代码，不靠 LLM）：
- `quote_matches`：`file:line` 处原文与 `quoted_line` 去首尾空白后相等。
- `evidence_matches`：证据行与答案行不同，且原文相符。
- `origin_matches`：来源行原文相符。

采纳规则：`quote_matches` 为真，且（答案位置等于规则层的 `crash_location` 或 `evidence_matches` 为真），则采用 LLM 答案；否则退回规则答案。是否“答崩溃行”按位置判断，不看 LLM 自报的 `hypothesis`（v2 spike 中 MoneyTrack1_2 自报 crash_line 却答了 114 行，崩溃行是 110）。〔我的决定〕`origin_matches` 只记录，不参与采纳。报告中写明：证据核对只防编造，不证明正确。

### 4.7 输出 `LocateResult`

- `method = "rules+llm"`。
- `root_cause_locations`：第一项为 LLM 答案（被采纳时）；随后附上规则答案、崩溃行（去重），role 分别标注，供修复阶段使用。
- 答案若落在跨多行语句内，附上整条语句的行范围（role 例如 `statement_span`）。
- `evidence`：LLM 的引用原文与 reasoning 摘要；`assumptions`：退回规则答案的原因、预算耗尽等。
- `status`：采纳 LLM 答案为 `CANDIDATE`；退回规则答案时沿用规则结果的 status。

### 4.8 预算

- 每个案例最多 14 轮；剩 3 轮时提示收尾，最后一轮要求必须提交。
- 每个案例 token 上限 160k（含缓存读写，按后端可得的口径）。超出即停止，退回规则答案并标记。
- 单次请求超时、网络断开（含 `RemoteDisconnected`）要重试；重试耗尽算失败并退回。

token 口径：每个案例所有调用的 input + output + 缓存读 + 缓存写之和（与 spike 一致），报告中同时列出缓存读的占比。接近上限（80%）时追加一条“请立即提交”的提示，与轮次提示并行。每次调用前检查累计 token，达到上限即停止；因此最终累计可能超出上限一次调用的量（V4-Pro spike 最高 174.9k）。在越过上限的那次调用中已提交的答案如何处理，见第 7 节。

spike 观察（V4-Pro，160k）：5 个案例触发 80% 提示，其中 3 个随后提交，Express1、News1 没有提交、直到上限被截断。

### 4.9 记录（交付 session 数据用）

- 每次调用：用途、`run_id`、案例、模型（以返回值为准）、input/output/cache 读写 token、耗时、request id。
- 每个案例保存完整对话与工具调用到 `eval/runs/<run>/llm/<case>.json`。
- 不记录 API key；导出到 `sessions/` 前检查无密钥。
- 本阶段不做 `sessions/` 格式的导出器：`sessions/template-session.json` 描述的是开发会话（开发者、目标、对话摘要），不是逐案例的运行记录。逐案例记录只保存在 `eval/runs/`。〔我的决定〕

### 4.10 防泄漏

- LLM 只能读沙箱 `eval/sandbox/<case>/app` 与脱敏日志；工具对路径做 resolve 后检查在 app 根目录内。
- 沙箱 `manifest.json` 含原始数据路径，不得暴露给 LLM（不在 app 目录内，工具读不到）。DeepSeek-Flash spike 中模型 6 次尝试读沙箱外的 `data-master/`、`eval/`，说明模型会主动找原始数据，路径检查必须有测试覆盖。
- 测试覆盖：`../` 越界、绝对路径、符号链接指向 app 外。

### 4.11 评测

- `eval/run_baseline.py` 的 `LOCATORS` 增加 `rules+llm`；报告增加 token、调用次数、耗时列；分母规则不变（BLOCKED/ERROR 计入分母）。
- 正式数字：冻结 prompt 在 25 个案例上跑一次。测试报告同时写明：25 个案例既是 prompt 修改的参照也是评测集；附 spike 中观察到的波动（TeaDrinkOrders1 2/4，Express1 单案例 1.6 万–20 万 token）。
- 单元测试用假后端（返回预设的工具调用序列），不调用真实模型。

### 4.12 悬空引用检查（P1，规则层，可单独交付）

确定性检查：在数据/mock 字面量中找出 id 类字段的值在同类集合中没有对应项的条目（例如引用了不存在的 key），作为 `unresolved_candidates` 或提示传给 LLM。目的是降低“数据被改坏”类故障的检索成本（News1 每次 11 万–16 万 token，大部分花在找数据上）。不改变 LLM 的采纳规则。

## 5. 已知限制（写进测试报告，不再修补）

- 守卫/使用二选一（Mall1、ComprehensiveTool1）：标注两边都有，LLM 选择稳定但与其中一个标注相反。
- Notes1：Sonnet 编造了代码中未出现的循环引用机制，违反规则 7；prompt 不能约束所有推理错误。
- 评测集与 prompt 参照集相同，数字偏乐观；单次运行有波动。
- 正式测试集是否与 data_B 同分布未知。

## 6. 留给实现者

- 模块划分（建议 `src/dfx_repair/llm/` 放后端与工具，`src/dfx_repair/locate/llm.py` 放定位逻辑），遵守单文件 200–400 行。
- 跨行语句范围的计算方式（可基于 `retrieve/lexical.py` 的括号/分号扫描）。
- 后端接口的具体形状、重试与退避参数、日志格式。
- 悬空引用检查的具体匹配规则。

## 7. 待确认

1. 越过 token 上限的那次调用中已提交的答案：保留（按 4.6 核对，标记 `over_budget`）还是丢弃并退回规则。V4-Pro spike 中这样的答案 2 个，都答对；丢弃则该运行从 23/25 降到 21/25。
2. Opus 5.5 + 冻结 v4 prompt + 4.4 工具（计划 Task 8）在 25 个案例上的一次完整运行（正式数字）。
3. 〔已不适用，2026-10-22 模型改为 Sonnet〕已改走官方 API：`MOONSHOT_BASE_URL=https://api.moonshot.cn/v1`（2026-10-22）。原 key 是 Kimi Code 订阅 key，官方端点返回 401，已换成开放平台 key。该账号 `/models` 只列出 `kimi-k2.6`、`kimi-k2.7-code`；`kimi-k3` 返回 “Not found the model or Permission denied”；组织限流 RPM 3。K3 的 spike 结论不能直接套用到 K2.x，用哪个模型待用户决定；`temp/spike_llm.py` 已加 `LLM_RPM` 全局限速和 429 退避。
4. 后端与模型决定冲突：上面"后端"一条只做 OpenAI 兼容 `/chat/completions`，不做 `anthropic` SDK 与 `claude -p`；而 Sonnet 的 spike 都走 `claude -p`（内置 Read/Grep/Glob，不是 4.4 的自定义工具）。〔用户决定，2026-10-22〕本地开发用 Claude 订阅；提交版走 OpenRouter（OpenAI 兼容，模型 `anthropic/claude-opus-5.5`，支持 tools），与"后端"一条一致；不提交 key。测试报告的正式数字可在本地用订阅跑出，条件是本地链路与提交版用同一 system prompt 与同一组 4.4 工具：`claude -p --tools "" --strict-mcp-config --mcp-config <本地 MCP server，暴露 read_file/search/submit>`（CLI 2.1.309 已确认支持这些参数），报告注明调用方式。现有 `temp/spike_claude.py` 用内置 Read/Grep/Glob，不满足此条件。
