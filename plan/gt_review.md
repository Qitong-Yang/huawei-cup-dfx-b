# 标准答案复核表（5 条存疑）—— 已全部复核（2026-10-19）

> 数据根目录：`data-master/data_B/`。答案定义见 `eval/build_ground_truth.py`。
> 复核完在每条的"结论"处勾选，或直接告诉 Claude 结论，由 Claude 修改答案表并重新校验。

## 复核方法：每条回答 4 个问题

1. **报错对得上吗？** 崩溃行的代码，能不能产生日志里那条错误消息？
2. **哪几行是"被改过的"？** 注入的代码通常有以下特征：
   - 带一条为"奇怪写法"辩护的业务注释，例如"统一以工厂方式……""绑定数据服务……"；
   - 写法明显绕远，例如用 `ArrayList` 再加 `[]` 取值、用 `BigInt` 再转回 `number`；
   - 改动只服务于一条特定路径。
3. **只改答案行，能修好吗？** 假设只修改 `accepted_lines` 里的行：崩溃会消失吗？功能还对吗？如果必须同时改别的行才能修好，那些行应该加进 `accepted_lines`。
4. **有没有"差一行"的情况？** 如果相邻行也同样算错（例如条件判断和使用分在两行写），就都列进 `accepted_lines`；只是相关的行，放进 `root_cause_locations`。

判定原则（最终版，已写入 `eval/build_ground_truth.py`）：某行算精确命中，需同时满足 ① 属于注入的改动；② 只改这一行就能消除崩溃且不破坏正常功能。只满足其一的行放进范围（`root_cause_locations`）。

---

## 1. EnterpriseRecruitment2（TC_JSError_009）

- **当前答案**：`product/phone/src/main/ets/pages/jobRoute/JobDelivery.ets:186` `if (hasKey(record, 'jobId'))`
- **崩溃点**：`commons/component/src/main/ets/utils/PropUtil.js:3` `return key in obj;`
- **证据**：`hasKey` 全工程只在 186 行使用一次；`PropUtil.js` 是 `commons/component` 里唯一的 `.js` 文件，可能整个文件都是注入的。第 185 行的注释"仅当命中有效投递记录时执行移除"是典型的辩护注释。
- **要判断的**：`PropUtil.js:3` 要不要也算作精确命中？
  - 算：评委可能认为"错误代码行"就是抛错的那一行；
  - 不算：`key in obj` 本身没错，错的是传进来的 `undefined`。
- **怎么查**：打开 `JobDelivery.ets` 第 180–192 行，看 `record` 在"未提交简历就点垃圾桶"时是否必然为 `undefined`。
- **结论**：[x] 加上 PropUtil.js:3 —— `hasKey` 只有第 186 行一个调用者，`PropUtil.js` 是注入的一部分；只在第 3 行加空值判断也能消除崩溃。

## 2. Express1（TC_JSError_010）

- **当前答案**：`features/business_home/src/main/ets/viewModels/ExpressPageVM.ets:354` `const lastHoursStr = timeParts[1];`
- **证据**：第 349–353 行用 `ArrayList` 存放切分结果，第 354 行用 `[1]` 取值（ArrayList 不支持方括号下标，报 "Get Property index out-of-bounds"）。第 349 行的注释"使用ArrayList存储时段片段，便于索引访问"是辩护注释。
- **要判断的**：第 346 行 `if (... === DAILY_PICKUP || this.selectedTemplate.pickupTime)` 也可能被改过：原来可能只有一个条件，注入后让更多模板进入这个分支。第 345 行的注释"固定时间模板需解析默认时段，其他模板仅在已配置时解析"也像辩护注释。
- **怎么查**：看 `selectedTemplate.pickupTime` 的类型。如果它在模板模型里是字符串（`'09:00 - 11:00'` 这类），`|| pickupTime` 可能是原有逻辑；如果是数字时间戳，`.split` 本身就有问题。
- **结论**：[x] 346 也算 —— 已复核（2026-10-19）
  - `Template.pickupTime` 是字符串，默认 `''`；"常规"模板 `shippingMethod = 3`（DAILY_PICKUP）且 `pickupTime = ''`。
  - 去掉 `DAILY_PICKUP ||` 后，空字符串为假值，会走 else 分支 → 不崩溃；所以 346 是让坏数据进入的那一行。
  - 只修 354（加越界判断）也能不崩溃，所以 354 同样算可修复的错误行。
  - 测试脚本 `testcases/TC_JSError_010.py` 文件头的"故障链"写明了这一过程（描述的是较早的注入版本，触发条件相同）。
  - 答案改为 `accepted_lines = [346, 354]`，certainty 升为 high。

## 3. Notes1（TC_JSError_006）

- **当前答案**：`common/datasource/src/main/ets/NoteService.ets` 第 197 行和第 237 行，都是 `note.noteService = this;`
- **崩溃点**：同一文件第 287 行 `JSON.stringify(notes)`
- **证据**：测试脚本写的触发步骤是"新建笔记 → 输入标题 → 保存 → 返回首页"，并说明环是 `note -> noteService -> NoteService.notes[] -> note`。新建笔记走 `addNote`（197 行），之后编辑走 `updateNote`（237 行）。
- **要判断的**：
  1. 两行都算精确命中合理吗？
  2. `components/richeditor/src/main/ets/model/Note.ets:60` 新增的 `noteService: ESObject;` 字段算不算？
  3. 第 287 行的 `JSON.stringify` 算不算？
- **怎么查**：看 `addNote`/`updateNote` 里保存流程实际调用哪个；第 287 行的 debug 日志 `JSON.stringify(notes)` 是不是原有代码（带 `hilog.debug` 前缀，像模板原有的调试日志）。
- **结论**：[x] 维持 197 + 237 —— 测试触发路径（新建→保存）走 `EditNotes.ets:621` → `addNote` → 第 197 行；编辑走第 237 行。`noteService` 字段没有任何其他使用者，是注入的。第 287 行是模板原有的调试日志，不算。

## 4. TeaDrinkOrders1（TC_JSError_020）

- **当前答案**：`features/order/src/main/ets/pages/ConfirmOrderPage.ets` 第 785 行和第 786 行
- **代码**（第 783–786 行）：
  ```ts
  let cutleryCount: number = this.cutleryIndex === 1 ?
    this.carGoods.reduce((count, item) => count + item.num, 0) :
    this.cutleryIndex - 1                                          // 785
  this.selectArray = new Array<string>(cutleryCount).fill(...)   // 786 崩溃
  ```
- **证据**：`cutleryIndex` 来自餐具选择器的下标，默认值是 0。选第 0 项（推测是"不需要餐具"）时，`cutleryCount = -1`，`new Array(-1)` 抛出 "Invalid array length"。B3 没有这个案例的注入报告。
- **要判断的**：错的是第 785 行的计算（没处理下标 0），还是第 786 行（没对负数做保护）？还是第 783 行整段的判断逻辑都是注入的？
- **怎么查**：在 `ConfirmOrderPage.ets` 里找餐具选择器的选项列表（第 740–760 行附近的 `TextPicker`），确认第 0 项和第 1 项分别是什么。
- **结论**：[x] 维持 785 + 786 —— 选项依次为 `[无需餐具, 需要餐具依据餐量, 1份, 2份, …]`，下标≥2 时 `index-1` 正确，只有默认的下标 0（测试脚本保留"无需餐具"）得到 -1。单改 785（处理 0）或 786（限制非负）都能修好。

## 5. ComprehensiveNews1（TC_JSError_003，低可信）

- **当前答案**：`commons/lib_news_feed_details/src/main/ets/views/ArticleFeedDetails.ets:111`，即 `...articleDetails.author.authorNickName`
- **证据**：`author` 只有在 `authorMock.queryAuthorInfo(item.authorId)` 找到作者时才会被赋值（`commons/lib_news_api/src/main/ets/httpsmock/BaseNewsServiceMock.ets` 的 `handleRawNews`）。推测注入方式是某篇 Mock 文章的 `authorId` 被改成了不存在的值，**但我没找到是哪一篇**。这个案例自带的测试脚本描述的是另一个应用，没有触发路径可参考。
- **要判断的**：根因是数据（某个 `authorId`），还是第 111 行缺少 `?.` 保护？已知写法不统一：`ArticleDetailsFooter.ets:65` 和 `NewsContent.ets:253` 用了 `author?.`，而 `NewsContent.ets` 第 94、95、277、287 行也直接写 `author.authorNickName`。所以"缺 `?.`"在模板里很常见，不能单凭这一点认定第 111 行是注入点。
- **怎么查**：
  1. 在 `commons/lib_news_feed_details/` 里搜索 `author.authorNickName` 和 `author?.authorNickName`，对比哪些地方没有 `?.`；
  2. 在 Mock 文章数据里找 `authorId`，对照作者 Mock 列表，找出不存在的 ID。
- **结论**：[x] 改为数据行 `commons/lib_news_api/src/main/ets/httpsmock/mockdata/MockArticle.ets:50` —— `article_2` 的 `authorId: 'Author_2'`（大写 A），作者列表里是 `author_2`，查找失败导致 `author` 未赋值。全部 71 处 `authorId` 中只有这一处对不上（另两处是默认占位空值和内嵌作者对象）。第 111 行不算：模板里多处同样没加 `?.`，且只加 `?.` 会让作者显示为空，掩盖数据错误。
