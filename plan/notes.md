# Notes: B题 调研与证据

## 1. 题目与评分（`题目要求.md`、`template-master/README.md`）
- 链路要求：日志分析 → 错误代码行定位 → 修复方案生成 → 运行验证成功，并能反复调优。
- B 题评审维度：定位准确率；修复成功率、功能正确性、修复后稳定性；一次通过率；token 消耗量、agent 链路长度、平均定位/修复耗时。
- 提交材料评分：功能完整性 40、技术设计 20、代码质量 20、测试覆盖 10、工程规范 10。
- 交付：设计方案、源码、标准化 SKILL/Spec、测试报告、sessions（JSON，按 `sessions/template-session.json` 格式）。

## 2. 数据集事实（`data-master/data_B/`，已在 `.gitignore` 中）
- 结构：B1（12 个 app 目录 + `DFXBenchMark_TC`）、B2（5 个）、B3（`benchmarkfordfx-better`，11 个 ZIP 日志）。`SchoolLife1` 无故障。
- 平台：HarmonyOS ArkTS（`.ets`），共 8866 个 `.ets` 文件。日志来自 x86_64 模拟器，`Build info: emulator 6.1.0.126`。
- 日志关键字段：`Error name`、`Error message`、`Stacktrace`（`文件:行:列`）、`Page switch history`、`HiLog`。
- 格式不一致：栈帧有/无 `file:` 前缀；ChildrenEducation1 日志缺 `Error name`；B3 ZIP 路径混用 `\` 和 `/`；`TC_JSError_018` ZIP 内是 `MainEntry.ets`。
- **答案泄露源**：`testcases/*.py` 的 `META`（`fault_file`/`fault_line`/`root_cause`）和文件头注释"故障位置"；B3 的 `docs/故障注入报告-App与基线对比.md`、`Prompt.md`。评测时必须从沙箱中删除。
- B3 `HypiumProject/resource/baseline` 存在基线资源。

## 3. 错误类型分布（已解析的日志）
- TypeError：读 undefined/null 属性、BigInt 转数字、类构造器未用 `new`、`in` 用于非对象、循环结构 JSON 序列化、下标越界。
- RangeError：TypedArray 字节不对齐、数组长度非法、栈溢出、数值越界（fraction 0–100）、infinity。
- SyntaxError：正则（nothing to repeat、GroupName、invalid class range 等）、JSON 解析、模块导出名不存在。

## 4. 手工案例：ComprehensiveTool1（TC_JSError_001）
- 日志：`TypeError: Cannot read property sortNum of undefined`，栈顶 `ResourceManageVM.ets:83:33`，调用方 `ResourceManageSheet.ets:156`。
- 过程：`list[index2]` 越界 → `index2` 来自 Grid `onItemDrop` 的 `insertIndex` → 网格比列表多一个"添加"格（第 124 行）→ 拖到"添加"格时 `insertIndex === list.length`。
- 标准答案：第 78 行（边界检查只校验了 `index1`）。**崩溃行 83 ≠ 故障行 78。**
- 修复：方案 A（第 78 行补 `|| index2 >= list.length`）正确；方案 C（可选链）在第 85 行继续崩溃。
- Node 验证（`harness_tc001.js`，scratchpad）：原代码复现 TypeError；方案 C 报 `Cannot set properties of undefined`；方案 A 无副作用；25 组合法输入差分一致。
- 发现：Node 报错文案与 ArkCompiler 不同 → 需要消息归一化表。

## 5. 手工案例：HomeStay1（TC_JSError_012，未完整走）
- 崩溃 `model/Index.ets:65` `JSON.parse(item.images)`；根因在 `utils/MockData.ets` 第 11 行附近，`images` 由数组改为字符串。跨文件的数据来源回溯。

## 6. 环境
- 本机：Windows 11 家庭版，23.7 GB 内存，虚拟化已开（WSL2）。家庭版可能无法运行依赖 Hyper-V 的模拟器 → 由队友安装。
- WSL 有 Node v18.19.1，可用于 V1 局部复现。

### 队友安装清单（已发给用户）
1. Windows 专业版或 macOS，≥16 GB 内存，BIOS 开虚拟化，约 100 GB 磁盘
2. 安装 DevEco Studio，登录华为开发者账号，安装 HarmonyOS SDK
3. 开启 Hyper-V、虚拟机平台、Windows 虚拟机监控程序平台
4. 创建 x86_64 手机模拟器，版本尽量接近 6.1
5. 打开 `data_B/data_B2/WebShortDrama1`，编译安装并启动
6. 安装 Python + hypium，运行 `python setup-regression.py`，退出码应为 0（hypium 安装方式未核实）
7. 运行 `testcases/TC_JSRangeError_001.py`，应判定 FAIL（复现崩溃）
8. 反馈 DevEco/SDK/模拟器版本和第 6、7 步输出

## 7. LLM：Kimi K3（第三方资料，需在官方控制台核实）
- 模型 ID 多数资料写 `kimi-k3`；上下文约 1M token；2026-07 发布。
- base URL 说法不一：`https://api.moonshot.ai/v1`（多数）与 `https://api.moonshot.cn/v1`。→ 放进 `.env`。
- 据称不接受 temperature/top_p/seed，用 `reasoning_effort` 控制推理深度 → 结果不能靠固定 seed 复现，需要记录完整请求与响应，并对同一输入多跑几次观察方差。
- 来源：
  - https://www.datacamp.com/tutorial/kimi-k3-tutorial
  - https://enter.converge.ai/page/en-US/blog/kimi-k3-api-guide
  - https://dev.to/bri_whitmore_88/moonshot-api-complete-guide-from-kimi-k2-to-k3-and-beyond-57bm

## 8. Phase 1 盘点结论（2026-10-19，`eval/inventory.py` → `resources/cases.json`）
- 共 27 个案例，键为 `TC编号@应用@数据集`（TC 编号在不同应用间重复，不能单独作键）。
- 26 个有日志；主日志按"栈顶映射唯一 > 标准 HiviewDFX 格式 > bundle 匹配 > 最新"选择，全部 `UNIQUE` 映射到源码。
- 日志格式问题（已在 `ingest/crash_log.py` 处理）：
  - `TC_JSError_017` 的日志为 UTF-16（带 BOM）；
  - AccountingApp1 用 `Process name` 代替 `Module name`；
  - Express1/MoneyTrack1_2 的合成日志使用设备路径 `file:///data/storage/el1/bundle/...`；
  - 文件名 `jscrash-com.kuaishou.hmapp-...log` 被复用在 7 个应用里，内容各不相同，**不能按文件名识别日志**；
  - 部分合成日志行号与源码不一致（Express1 合成日志 352 vs 真实 354；MoneyTrack1_2 日志 110 vs 源码 114）。
- **第 4 个泄题源**：ChildrenEducation1 的日志自带 `Root cause:` / `Fix:` 段 → 沙箱必须用 `strip_leaky_sections` 清洗。
- `DFXBenchMark_TC` 的同编号脚本可能属于其他应用（`TC_JSError_003.py` 描述的是 income_calculator）；ComprehensiveNews1 自己的 `TC_JSError_003.py` 也描述了别的应用。备用脚本改为按 bundle 名过滤。

## 9. 标准答案（`eval/build_ground_truth.py` → `resources/ground_truth.json`）
- 27 条，25 条可评分，2 条排除：
  - `TC_JSError_002@ComprehensiveNews1`：故障不在提供的源码中（`toDeleteList` 从未为 null，日志行 48 是 `} else {`）；
  - `TC_JSError_018@ImageProcessing1`：ZIP 内没有日志。
- 每条含 `crash`、`accepted_lines`（算精确命中的行，可多个）、`root_cause_locations`（范围，算"范围命中"）、`basis`、`certainty`。
- 依据分布：测试脚本标注、脚本描述、B3 注入报告/设计文档（已对照当前源码复核，报告中 016/018 编号对调）、兄弟版本 diff（MoneyTrack1 vs MoneyTrack1_2、WebShortDrama1 B2 vs B3）、日志 Root cause 段、人工分析。
- `certainty`：复核后 25 条全部为 high（复核过程见 `plan/gt_review.md`）。ComprehensiveNews1 003 的根因找到：`MockArticle.ets:50` 的 `authorId` 大小写错误。
- 精确命中判定规则：属于注入改动 + 单改该行即可消除崩溃且不破坏功能。按此规则补充了 Express1:346、EnterpriseRecruitment2 PropUtil.js:3、AccountingApp1:48。
- 教训：整理答案时只按关键词搜索，漏读了测试脚本文件头的"故障链"说明（Express1、Notes1 都有）；以后先通读文件头。
- 崩溃行不在可接受行内的案例共 6 个（25 个中）：ComprehensiveTool1、Notes1、ComprehensiveNews1、HomeStay1.0.6、Exam1、AudioEditing1 —— 只报栈顶的基线在这些案例上必然失分，精确命中率上限 19/25。

## 10. Phase 3 基线结果（`eval/runs/stack_top_20261019_043501/report.md`）
- 栈顶基线：exact 18/25（72%）、range 21/25、function 23/25、file 24/25；0 次 LLM 调用。
- 沙箱：25 个，按允许清单复制源码 + 清洗后的日志；泄题扫描（fault_file/故障位置/TC 编号等）全部为 0。
- **性能问题**：首次建沙箱在 `/mnt/f` 上耗时约 23 分钟（WSL 访问 Windows 盘很慢）；之后复用缓存。若需频繁重建，改用 Linux 文件系统路径（`--sandbox-root ~/dfx_sandbox`）。
- 7 个精确未命中，按 Phase 4 需要的能力分类：
  | 案例 | 栈顶 | 答案 | 需要的规则 |
  |---|---|---|---|
  | ComprehensiveTool1 | 83 | 78 | 数组下标越界 → 同函数内找不完整的边界检查 |
  | AudioEditing1 | 58 | 52 | 越界读取 → 找循环条件 `<=` off-by-one |
  | MoneyTrack1_2 | 110 | 114 | **日志行号与源码偏移** → 按错误内容在附近对齐（如 `Object.keys`） |
  | Notes1 | 287 | 197/237 | 循环结构 JSON.stringify → 找给对象挂反向引用的赋值 |
  | HomeStay1 | Index.ets:65 | MockData.ets:11 | JSON.parse 失败 → 追参数来源到 Mock 数据 |
  | ComprehensiveNews1 003 | 111 | MockArticle.ets:50 | 读 undefined 属性 → 追查找函数的键，核对数据 |
  | Exam1 | MainPage:65 | TopicModel:40 | 栈溢出 → 递归无访问集合 + 数据成环 |

## 11. Phase 4 第一批规则（`src/dfx_repair/locate/rules/`）
- `align`：日志行与源码不符（空行/注释/不含错误消息对应的语法结构）时，在 ±8 行内找最近的匹配行。修复 MoneyTrack1_2（110→114）。
- `R-BOUNDS-PARTIAL`：`arr[i].p` 读到 undefined，且同函数内更早的 `if` 检查了 `arr.length` 但没检查 `i` → 报该 `if` 行。修复 ComprehensiveTool1（83→78）。
- `R-BOUNDS-LOOP`：缓冲区/视图越界错误，所在函数里有 `for (...; v <= total; ...)` → 报循环行。修复 AudioEditing1（58→52）。
- 结果：exact 18→21/25；只有这 3 个案例结果变化，其余 22 个规则均未触发。单元测试含反例（两个下标都检查、循环用 `<`）。
- 泛化说明：规则按错误语义和代码结构匹配，不含任何案例特有的变量名或文件名；但每条规则目前只有 1 个真实案例支撑，后续用故障变异（Phase 9）补充验证。
- 运行耗时：规则版全量 2 分 27 秒，主要花在 `/mnt/f` 上为每个案例建 SourceIndex；单案例定位逻辑本身 < 0.1 秒。
