# DFX 故障定位与自修复研究项目

当前为 Phase 4 规则定位阶段，已于 2026-10-21 完成本轮缺陷修复与回归验收。已实现日志到候选代码位置的分析和离线评测；LLM、修复规格、补丁、编排、设备验证仍为占位模块。

## 已有能力与目录

- `src/dfx_repair/`：日志解析、文件映射、粗略函数识别、栈顶定位、行号对齐和两条越界规则。
- `eval/`：案例盘点、标签引用检查、诊断副本和定位评分。
- `resources/`：本地 `cases.json`、人工 `ground_truth.json`，答案不能交给诊断端。
- `tests/`：原有测试及新增审查反例。
- `docs/`、`skill/`、`sessions/`：交付文档、后续 Skill、真实开发记录。
- `.venv/`：本机 Python 环境，不提交。

规划与设计记录见 `plan/`，本轮审查前后摘要见 [核验结果](docs/phase4-review-results.json)。

## 本地运行

在仓库根目录执行。默认路径以项目位置计算，数据默认在 `data-master/data_B`；文件读写已明确指定 UTF-8，`-X utf8` 仅作为终端输出选项保留。

Linux / WSL（uv）：

```bash
uv sync --locked --group dev
uv run python -m eval.inventory                      # 按本地数据重建 resources/cases.json
uv run pytest -q
uv run ruff check src eval tests
uv run python -X utf8 -m eval.run_baseline --method stack_top
uv run python -X utf8 -m eval.run_baseline --method rules
```

Windows（PowerShell）：

```powershell
$env:PYTHONPATH = 'src'

# 当前 .venv 已建立，安装了 pytest 9.1.1 和 ruff 0.17.1。
# 重建时选择 Python 3.10+：python -m venv .venv
# 然后 ./.venv/Scripts/python.exe -m pip install pytest ruff
# 有 uv 时可使用 uv sync --locked --group dev。

# 其他机器的 ZIP 成员路径可能不同，先按本地数据重建索引
./.venv/Scripts/python.exe -X utf8 -m eval.inventory --data-root data-master/data_B

# 测试临时文件留在项目内
New-Item -ItemType Directory -Force .pytest-work | Out-Null
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .pytest-work/manual
./.venv/Scripts/python.exe -m ruff check src eval tests

# 离线定位评测
./.venv/Scripts/python.exe -X utf8 -m eval.run_baseline --data-root data-master/data_B --method stack_top
./.venv/Scripts/python.exe -X utf8 -m eval.run_baseline --data-root data-master/data_B --method rules
```

首次建副本可能较慢。WSL 下若仓库位于 `/mnt/` 盘，副本读写约慢 7 倍（实测单次评测约 64 分钟对 9 分钟），建议用 `--sandbox-root` 与 `--runs-root` 指向 Linux 文件系统目录（如 `~/dfx-eval/sandbox`）。缓存现在核对输入源码、原始／清洗日志、清洗策略和副本实际内容，变更后自动失效。仅替换可验证归属的受管目录；未知目录、路径逃逸、链接、冲突或可信答案标记会明确拒绝，不会默默继续。

标签引用检查现在支持本地默认路径及显式参数：

```powershell
./.venv/Scripts/python.exe -m eval.build_ground_truth --check-only
# 可选 --data-root、--cases、--out
```

库存与评测默认均使用 data-master/data_B；评测可用 --cases、--ground-truth、--sandbox-root、--runs-root 配置路径。显式相对路径相对于调用者工作目录。

## 如何解读结果

`eval/runs/<run_id>/` 保存配置、预测、逐案状态、评分与报告。本次前后摘要在 [核验结果](docs/phase4-review-results.json)，运行目录可再生，不入库。运行 ID 中时间为 UTC，带微秒和随机后缀。

- **108 项测试通过，0 失败、0 跳过、0 XFAIL；Ruff 通过。**原先 9 个反例均已转为正常回归。
- 人工标签精确命中：栈顶 18/25，规则 21/25；两版均处理 25/25 案例、0 BLOCKED、0 ERROR。
- function 辅助指标已修正为两版 22/25，不再从相关范围直接继承。它仍包含无函数位置的既定近邻回退，具体定义见 score.py。
- 规则版 24 个 CANDIDATE、1 个 UNRESOLVED；Notes1 的模板内表达式未被当前词法助手支持，因此保守不报根因。该案例仍计入分母，规则版 file 因此为 23/25。
- CANDIDATE 是有依据的假设，不是已证实根因；assumptions 与 context_requests 记录待补证据。
- UNRESOLVED 时，同名文件或歧义对齐行放在 `unresolved_candidates`，供后续 LLM 步骤核查；该字段不参与评分。
- 资料污染或不可准备记 BLOCKED，执行异常记 ERROR，不能唯一定位记 UNRESOLVED；都保留主分母。BLOCKED／ERROR 批次保存报告后以非零退出码结束。
- 结果绑定完整源码、日志、配置与实现摘要。输入副本过滤不是操作系统安全边界。
- 成绩来自当前人工标签开发集，不是修复成功率或未见案例泛化准确率；当前仍未接入模型、应用补丁和鸿蒙运行验证。

## 后续交付

队伍信息、正式技术设计、Skill、应用运行／回归报告和开发记录仍需补齐。源数据、凭证、环境和临时诊断副本不进入提交代码。
