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
