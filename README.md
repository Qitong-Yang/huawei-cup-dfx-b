# DFX 应用崩溃根因定位

## 队伍信息

| 项目 | 内容 |
|------|------|
| 队伍名称 | 天天向上队 |
| 队长 | 杨启桐 |
| 队员 | 刘思源，乔义杰 |
| 项目名称 | DFX 应用崩溃根因定位（华为杯 DFX 竞赛 B 题） |
| 一句话描述 | 从鸿蒙 ArkTS 崩溃日志出发，用"规则候选 + LLM 跨文件追因 + 确定性引用核对"定位错误代码行 |

## 完成情况

| 赛题环节 | 状态 |
|---|---|
| 日志分析 | 已实现：`.log`/`.zip`、UTF-8/UTF-16、路径归一、栈帧映射 |
| 错误代码行定位 | 已实现：栈顶基线、规则层、规则 + LLM |
| 修复方案生成 | **未实现**（`src/dfx_repair/{spec,repair}/` 为占位） |
| 运行验证 | **未实现**（`src/dfx_repair/{verify,orchestrator}/` 为占位） |

官方 `data_B` 中 25 个可评分案例（另有 2 例因数据问题排除）的定位结果：

| 方法 | exact | range | file | token |
|---|---|---|---|---|
| 栈顶基线 | 18/25 | 21/25 | 24/25 | 0 |
| 规则 | 21/25 | 23/25 | 23/25 | 0 |
| 规则 + LLM（Claude Opus 5.5） | **24/25** | **25/25** | **25/25** | 719k（平均 28.8k、22.3 s/案例） |

这 25 个案例同时用于修改 prompt 和评测，没有独立留出集，数字偏乐观。详见 [测试报告](docs/test-report.md)。

## 目录

```
.
├── README.md
├── docs/
│   ├── technical-design.md     # 技术设计
│   ├── test-report.md          # 测试报告
│   └── superpowers/            # LLM 定位阶段的设计与实施计划
├── src/dfx_repair/             # 源码：ingest / retrieve / locate / llm（spec 等为占位）
├── skill/dfx-fault-repair/     # Skill：SKILL.md、单案例定位脚本、规则库参考
├── eval/                       # 案例盘点、标准答案、沙箱、评分、批量评测
├── tests/                      # 174 项单元与回归测试
├── resources/                  # cases.json、ground_truth.json
├── sessions/                   # AI 辅助开发记录
└── plan/                       # 任务计划、框架设计、标准答案复核
```

数据目录 `data-master/`、评测产物 `eval/sandbox/` 与 `eval/runs/`、`.env`、`.venv/` 不入库。

## 快速开始

需要 Python 3.10+ 与 [uv](https://docs.astral.sh/uv/)；比赛数据放在 `data-master/data_B`。`rules+llm` 另需已登录的 Claude Code CLI（`claude`）。

```bash
# 安装依赖
uv sync --locked --group dev

# 测试与静态检查
uv run pytest -q
uv run ruff check src eval tests

# 按本地数据重建案例索引
uv run python -m eval.inventory

# 批量定位评测（结果写入 eval/runs/<run_id>/）
uv run python -X utf8 -m eval.run_baseline --method stack_top
uv run python -X utf8 -m eval.run_baseline --method rules
uv run python -X utf8 -m eval.run_baseline --method rules+llm

# 单个案例定位（案例目录含 app/ 与 crash.log）
uv run python skill/dfx-fault-repair/scripts/locate_case.py \
    --case-dir eval/sandbox/TC_JSError_001_ComprehensiveTool1_B1 --method rules
```

Windows PowerShell：先设 `$env:PYTHONPATH = 'src'`，再用 `./.venv/Scripts/python.exe` 代替 `uv run python`。

WSL 下仓库位于 `/mnt/` 盘时，沙箱读写约慢 7 倍；可用 `--sandbox-root` 与 `--runs-root` 指向 Linux 文件系统目录（如 `~/dfx-eval/sandbox`）。评测可用 `--data-root`、`--cases`、`--ground-truth` 指定路径。

## 如何解读结果

`eval/runs/<run_id>/` 中保存 `config.json`（输入、实现与 prompt 的 SHA-256）、`predictions.json`、`case_outcomes.json`、`score.json`、`report.md`，以及 `llm/<case>.json`（完整对话与工具调用）。

- `CANDIDATE` 是有依据的假设，不是已证实根因；`assumptions` 记录待验证前提。
- `UNRESOLVED`（无法唯一定位）、`BLOCKED`（资料污染或不可准备）、`ERROR`（执行异常）都保留在分母中。
- LLM 答案必须通过确定性引用核对，否则退回规则答案，`assumptions` 中记 `llm fallback: <原因>`。核对只能防编造，不能证明答案正确。
- 沙箱按允许清单复制源码与日志，测试脚本和标准答案不进入；它过滤输入，不是操作系统级隔离。
