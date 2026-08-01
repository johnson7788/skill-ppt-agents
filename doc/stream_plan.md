# evidence-a2ui 流式输出 + 思考(thinking)实施计划

## 0. 目标与背景
现状 `/a2a` 一次性算完再返回 JSON，前端 `fetch + await res.json()` 整体等，
用户盯着「循证医学引擎分析中…」直到全链路（PICO 抽取 → 检索 → 结论生成，两次串行
阻塞 LLM）跑完才出卡片。

本计划把链路改成**流式**：先秒回「思考中」的可流式 thinking 气泡（把 LLM 的
reasoning / 阶段状态实时吐出来），再把循证卡分批填进来。**总耗时不一定变，但感知
延迟大降**——用户第一时间就看到反馈在动。

不改检索结果的正确性、不改 mapper 版式、不加新依赖。

---

## 1. 事件流协议（后端 → 前端）
`POST /a2a` 的响应从「一个 JSON 数组」改成 **SSE 流**（`text/event-stream`）。
每个事件一行 `data: {json}\n\n`，json 形如：

| kind | 字段 | 含义 | 前端动作 |
|------|------|------|----------|
| `thinking` | `delta` | 思考文本增量（reasoning token 或阶段旁白） | 追加进 thinking 气泡 |
| `status` | `text` | 阶段标签，如「检索指南与文献…」 | 更新 thinking 气泡的当前步骤行 |
| `text` | `text` | intro 引导语（可整段，也可后续拆 delta） | 渲染 AI intro 气泡 |
| `data` | `data` | 一条 A2UI 消息（createSurface / updateComponents） | `processor.processMessages([data])` |
| `error` | `text` | 兜底失败提示 | 渲染错误气泡 |
| `done` | — | 流结束 | 收尾，停 loading |

> ponytail：复用 M3 已有的「多条 A2UI 消息按 id 合并」能力——`data` 事件就是把
> 原来一次性返回的那几条消息拆开逐条发，前端处理逻辑几乎不用变。

选 SSE 而非 WebSocket/NDJSON：单向推送、`StreamingResponse` 原生支持、前端用
`fetch` + `ReadableStream` reader 手解析即可（POST 不能用原生 EventSource，沿用
main 分支已验证的「fetch + 手写 parseSSE」范式，无新依赖）。

---

## 2. 分期（可逐段上线，Phase 1 就能让用户感觉快）

### Phase 1 —— 传输改流式 + thinking 气泡（感知提速最大，优先做）
**后端**
- `answer_question` 从「返回 EvidenceAnswer」改成 **async generator**，逐阶段 `yield` 事件：
  - 进 `extract_pico` 前：`status「抽取临床要素(PICO)…」`
  - 进 `run_search` 前：`status「检索指南 / Meta / RCT…」`
  - 进 `build_answer` 前：`status「综合证据、生成循证结论…」`
  - 完成后：`text(intro)` + 若干 `data`（createSurface + updateComponents）+ `done`
- `server_evidence.py /a2a`：改用 `StreamingResponse(sse_gen(), media_type="text/event-stream")`；
  逐事件 `yield f"data: {json.dumps(evt)}\n\n"`。异常兜底 → 发 `error` + `done`（不再 raise）。

**前端 `App.tsx`**
- `ask()` 里把 `fetch + res.json()` 换成读流：`const reader = res.body.getReader()`，
  `TextDecoder` 拼 buffer、按 `\n\n` 切事件、`JSON.parse`；按 kind 分发：
  - `thinking/status` → 更新 `thinking` state
  - `text` → `setIntro(await renderMarkdown(text))`
  - `data` → `processor.processMessages([msg])`
  - `error` → intro 显错；`done` → `setLoading(false)`
- 新增 state：`thinking: string`、`steps: string[]`（或当前步骤）、`thinkingDone: boolean`。

**新组件 + 样式 `ThinkingBubble`**（用户明确要的）
- 一个左对齐的浅色气泡，顶部「🧠 思考中…」带脉冲小圆点（CSS animation），下面是
  实时增长的思考文本（灰、稍小、`white-space: pre-wrap`）+ 已完成的阶段勾选行。
- **卡片首条 `data` 到达时自动折叠**成一行「已完成思考 ▸」（可点开回看），把主视觉让给循证卡。
- CSS 建议类名：`.thinking`, `.thinking-head`, `.thinking-dot`(脉冲), `.thinking-body`,
  `.thinking-steps`, `.thinking.collapsed`。与现有 `.bubble.ai` 同栏但更轻。

**验收**：mock 下也走流（可瞬发完）；真实检索下先看到「思考中」逐字/逐步动，再看到卡片渐显。

---

### Phase 2 —— 真·流式思考（reasoning token）
让 thinking 是模型真实思考，而非仅阶段旁白。
- `pipeline._chat_json` 增加流式变体 `_chat_json_stream(system, user, on_thinking)`：
  `await litellm.acompletion(..., stream=True)`，遍历 chunk：
  - `chunk.choices[0].delta.reasoning_content` → 回调 `on_thinking(delta)`（deepseek 推理内容）
  - `chunk.choices[0].delta.content` → 累加进 buffer
  - 结束后 `json.loads(buffer)` 得最终 JSON（**仍要完整 JSON 才解析**，故 thinking 用
    reasoning_content 通道，正文 JSON 通道单独累加——两条通道天然分离，互不干扰）。
- `build_answer` 改用它，把 reasoning 增量以 `thinking` 事件吐给前端。
- **降级**：若所用模型不返回 `reasoning_content`（非推理模型），`on_thinking` 收不到，
  自动退回 Phase 1 的 `status` 阶段旁白（保持体验不崩）。
- ponytail：不做「部分 JSON 增量解析」——正文 JSON 一次性解析，只有 reasoning 流式；
  想再快看 Phase 4。

---

### Phase 3 —— 骨架卡 + 增量填充（把「白屏等」变「框先出」）
- `build_answer` 之前就 `yield` `createSurface` + **骨架 updateComponents**（header +
  「A 级证据」占位 badge + 「分析中…」结论占位），前端立刻画出卡框。
- `build_answer` 完成后 `yield` 真值 `updateComponents`（同 surfaceId、同组件 id 原地替换，
  复用 M3 的按 id 合并）——骨架被真实结论/文献覆盖，无重建、无闪。
- 需要 mapper 出一个 `skeleton_components()`（复用现有组件类型，文案换占位）。

---

### Phase 4（可选）—— 真正减少总耗时
- **砍 PICO 那次 LLM**：检索本就是 embedding 语义检索，可让检索直接吃原始问题（或用极小
  模型/规则抽关键词），省掉一整轮 LLM 往返（两大耗时之一）。
- **结论生成换更快档位 / 关 reasoning**：结构化产出对推理需求不高时，用更快模型。
- 二者独立于流式，纯粹压低墙钟时间。

---

## 3. 涉及文件清单
- 后端
  - `backend/app/evidence/pipeline.py`：`answer_question` → async generator 产事件；
    （P2）新增 `_chat_json_stream`。
  - `backend/server_evidence.py`：`/a2a` → `StreamingResponse` SSE；会话态 `_SESSIONS`
    的写入时机移到流末（拿到完整 comps 后再存，供追问 diff）。
  - （P3）`backend/app/evidence/mapper.py`：新增 `skeleton_components()`。
- 前端（`frontend/web/src/`）
  - `App.tsx`：`ask()` 改读 SSE 流 + 分发；加 `thinking` 相关 state；渲染 `ThinkingBubble`。
  - `index.css`：`.thinking*` 样式 + 脉冲/折叠动画。
  - （可选）抽 `ThinkingBubble.tsx` 单独组件。
- 无新增依赖（fetch ReadableStream + TextDecoder + FastAPI StreamingResponse 均原生）。

---

## 4. 兜底与边界
- 流中任一步异常：发 `error` 事件（友好中文）+ `done`，前端显错气泡，不留死流。
- `EVIDENCE_MOCK=1`：同样走流，但各事件近乎瞬发（离线联调 e2e 用）。
- 追问（M3）：仍先发 thinking，再只发**差异** `data`（surface 不重建）；`_SESSIONS`
  在流结束、comps 完整后写入。
- 网络代理坑：本机 http_proxy 拦 localhost，playwright/curl 记得 `--noproxy '*'` /
  config 顶层清代理（见 e2e 既有处理）。

---

## 5. 测试
- `frontend/web/e2e/`（EVIDENCE_MOCK=1）新增/改：
  1. 断言 `.thinking` 气泡先出现（有 `status`/`thinking` 文本），随后 `.ev-header` 卡片出现。
  2. 断言卡片仍是 1 张、无「Surface already exists」（流式多条 `data` 合并正确）。
  3. 追问：thinking 再现 + 卡片增量更新（沿用现有增量断言）。
- 后端可加最小自检：把 `answer_question` 的 generator 收集成事件列表，断言顺序为
  `status…* → text → data(createSurface) → data(updateComponents) → done`。

---

## 6. ponytail 备注（有意的偷懒与上限）
- 不做部分 JSON 流式解析：正文 JSON 一次性 parse，只有 reasoning/status 流式——
  代价是「结论文字本身」不逐字蹦，但骨架卡(P3)已让页面先动，够用。要逐字再说。
- thinking 优先用模型 reasoning_content；拿不到就退化成阶段旁白，不为「一定要真思考」
  去强上推理模型。
- 传输用 SSE 单向流，不引 WebSocket；前端手写 parseSSE，不引 EventSource/SSE 库。
- 先上 Phase 1（感知提速 80%），P2/P3/P4 按需再加。
