---
name: dfx-fault-repair
description: Locates the root-cause source line of a HarmonyOS ArkTS/TS app crash from a jscrash.log and the app source. Use when given a JS crash log (TypeError, RangeError, out-of-bounds) and an ArkTS project, and asked which code line is faulty. Covers localization only; it does not generate or verify patches.
---

# DFX 崩溃根因定位

从 `jscrash.log` 和应用源码出发，输出根因代码行（`LocateResult`）。流程：日志解析 → 栈帧映射 → 规则候选 → LLM 跨文件追因 → 确定性引用核对。

**范围：只做定位。** 补丁生成、编译与运行验证尚未实现，不要声称已修复。

## 输入

案例目录，结构与 `eval.sandbox` 产物一致：

```
<case-dir>/
├── app/        # 应用源码（.ets/.ts/.js/.json/.json5）
└── crash.log   # 崩溃日志，UTF-8 或带 BOM 的 UTF-16
```

不要把测试脚本、`testcases/*.json`、故障注入报告或标准答案放进 `app/`。答案泄漏会让结果失去意义。

## 步骤

1. **准备案例目录。** 评测数据用 `uv run python -m eval.run_baseline` 自动生成到 `eval/sandbox/<case>/`；自带数据按上面的结构手工放置。
2. **先跑规则层（不调用模型，秒级）：**
   ```bash
   uv run python skill/dfx-fault-repair/scripts/locate_case.py --case-dir <case-dir> --method rules
   ```
   - `status = CANDIDATE`：`root_cause_locations[0]` 是候选，`assumptions` 列出尚未验证的前提。
   - `status = UNRESOLVED`：栈帧映射不唯一或代码结构不受支持，查看 `unresolved_candidates` 与 `context_requests`。
3. **再跑规则 + LLM（需要已登录的 `claude` CLI）：**
   ```bash
   uv run python skill/dfx-fault-repair/scripts/locate_case.py --case-dir <case-dir> \
       --method rules+llm --record-dir <transcript-dir>
   ```
   模型只能调用 `read_file` / `search` / `submit` 三个工具，路径限制在 `app/` 内。若 `assumptions` 中出现 `llm fallback: ...`，说明 LLM 答案未通过核对，结果已退回规则答案。
4. **人工复核。** 打开 `root_cause_locations` 的第一项，对照 `evidence` 中的引用行，按 [localization-rules.md](references/localization-rules.md) 第 3 节检查：失败值从哪里产生、是否只改这一行就能消除崩溃且不破坏功能。

## 输出

JSON 格式的 `LocateResult`，字段含义见 [output-schema.md](references/output-schema.md)。关键字段：

| 字段 | 含义 |
|---|---|
| `crash_location` | 日志栈顶对应的源码行（已做行号对齐） |
| `root_cause_locations` | 根因候选，第一项为主答案；`role` 标明来源（LLM 答案、规则、崩溃行、`statement_span`） |
| `evidence` / `assumptions` | 支撑证据 / 尚未验证的前提 |
| `status` | `CANDIDATE` 为有依据的假设，不等于已证实根因 |

## 判断要点

- 崩溃行不一定是根因：越界读取的根因常是上游不完整的边界检查或 `<=` 循环；`undefined` 常来自被改坏的 mock 数据或构造处。
- 注释不可信：注入的故障常带一条为奇怪写法辩护的注释。
- 守卫行与使用行二选一时（例如 `if` 检查不完整，下一行使用），两行都可能被认作根因，复核时都要看。
- 引用核对只能证明引用的代码真实存在，不能证明答案正确。

## 参考

- [references/localization-rules.md](references/localization-rules.md)：规则库、根因判定原则、LLM 分析纪律
- [references/output-schema.md](references/output-schema.md)：`LocateResult` 与 `submit` 字段、采纳规则、预算
- 冻结 prompt：`src/dfx_repair/llm/prompts/localize_v4.txt`
- 评测结果：`docs/test-report.md`（25 个案例，精确命中 24/25）
