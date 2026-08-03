# 回答 SOP：三种模式的路由与回复形式

evidence-a2ui 后端已重构为 **ADK `LlmAgent` + A2A 协议 + `InMemorySessionService`**（B-Hybrid）。
路由不再是独立的 `classify` 一次性分诊，而是**由 agent 用工具自行决策**；多轮状态由
ADK session 天然承担（按 A2A `context_id` 存全量历史）。UI 仍走**确定性 mapper**（LLM 不
手搓 A2UI JSON），保证循证卡永远合法、带证据等级+引用+就医提示。

代码：`backend/app/evidence/agent.py`（EvidenceAgent）、`agent_executor.py`、`__main__.py`（:8700）。
前端官方 A2A 传输：`frontend/web/middleware/a2a.ts` + `src/App.tsx`。

## 一、路由（agent 用工具自选）

单个 `LlmAgent`（DeepSeek via `LiteLlm`）读每条消息（含多轮上下文），按 `_INSTRUCTION` 选择：

```
                    ┌─ 调 search_evidence(question,P,I,C,O) ─→ 循证模式
LlmAgent(msg) ──────┤─ 调 make_questionnaire(symptom) ───────→ 问卷模式
                    └─ 不调工具，直接文本回复 ────────────────→ 闲聊模式
```

- 无独立分诊 LLM 调用；路由=agent 的一次推理，PICO 由 agent 抽取后当工具参数传入。
- 工具**不产 A2UI**，只把结构化 `EvidenceAnswer`/`Questionnaire`（`model_dump()`）压进
  `tool_context.state['render_queue']`，返回一句 ack。
- `EvidenceAgent.stream()` 跑完后按 **per-session 水位线** drain 队列 → 确定性 `mapper` →
  A2UI DataParts；agent 的文本（thought 过滤）→ 一条 TextPart。

## 二、三种模式对照

| 维度 | 循证 evidence | 闲聊 chat | 自测问卷 questionnaire |
|---|---|---|---|
| 触发 | 需文献支撑的临床问题（某病能否用某药、某疗法是否有效） | 寒暄/致谢/闲聊/软健康问题 | "测测我是不是抑郁""评估睡眠"等可量表化症状群 |
| 工具 | `search_evidence` | 无 | `make_questionnaire` |
| 是否检索 | 是（run_search 向量检索指南/Meta/RCT） | 否 | 否 |
| 结构化产物 | `EvidenceAnswer`（build_answer，json_object） | — | `Questionnaire`（build_questionnaire） |
| 前端呈现 | AI 气泡(agent 引导语) + 循证卡(A2UI surface) | AI 气泡（一条整句） | 引导语气泡 + 问卷卡(A2UI surface) |
| 打分/交互 | 卡内 Button（open_reference 等） | — | **前端本地打分**（选项累加→档位→建议），零往返 |

## 三、逐模式细节

### 1) 循证 evidence
- `search_evidence` 工具：`run_search(pico)` 检索 → `build_answer(question,pico,results)` 产
  `EvidenceAnswer` → 入 render_queue。
- 渲染：`mapper.evidence_to_a2ui(answer, surface_id)` = `[createSurface, updateComponents]`。
- 卡片组件：`EvidenceHeader / EvidenceBadge / CautionBox` + basic，组件树根 `id:"root"` Card。

### 2) 闲聊 chat
- agent 不调工具，以「小团健康管家」人设（`_INSTRUCTION` 内）直接输出中文文本：先共情、
  再中肯建议或轻追问，2-4 句。→ 一条 A2A TextPart（非流式；见「五」权衡）。

### 3) 自测问卷 questionnaire
- `make_questionnaire` 工具：`build_questionnaire(symptom)`（`_QUIZ_SYS` 给成熟量表 PHQ-9/
  GAD-7/PSQI/王琦九分法，`assemble_quiz` 纯函数强制分值/补免责声明）→ 入 render_queue。
- 渲染：`createSurface` + `update_components_msg(questionnaire_components(q))`（根 `id:"root"`）。
- **前端本地打分**：`Questionnaire` 组件 `scoreQuiz` 选项累加→匹配 band→出总分/档位/建议，
  全程浏览器内、零往返。

## 四、传输与消息形态（官方 A2A）

- 浏览器 → `POST /a2a`（vite 中间件）body `{contextId, text}` 或 `{contextId, action}`。
- 中间件 `@a2a-js/sdk` `A2AClient.sendMessageStream`，`message.contextId=contextId` →
  后端 `context_id` → 复用 `InMemorySession`。带头 `X-A2A-Extensions: .../a2ui/v0.9`。
- 上游 SSE 每帧 = **A2A `Part[]`**（`{kind:'text',text}` | `{kind:'data',data:<A2UI消息>}`），
  原样透传。前端 `App.tsx`：text→intro 气泡（markdown）；data→`processor.processMessages`，
  记录 `createSurface.surfaceId` 按轮渲染；卡内 action→`{contextId, action}` 回传。

## 五、多轮与权衡
- **有状态**：同一 `contextId` 复用 ADK session = 全量多轮历史，agent 看得到上文再路由
  （已实测：「孕妇能吃氯雷他定吗」后追问「那哺乳期呢？」，agent 正确接住省略的药名）。
- 每张卡独立 `surfaceId`（后端 `card-<uuid>` 生成），同会话内互不覆盖。
- **非流式**：agent 一轮产一条最终 status-update（文本+卡片一次到达）。A2UI shell 把每个
  TextPart 当独立气泡，逐 token 流会碎成多气泡，故不流式。要 token 流需可合并的文本渲染器。
- 兜底：任一步异常 → SSE `error` part → 前端「出错了：…」气泡。

## 六、加一种新模式怎么做
1. `agent.py:_INSTRUCTION` 加一条路由规则；
2. 写一个新工具（`async def foo(..., tool_context)`），产结构化对象入 `render_queue`，注册进
   `LlmAgent(tools=[...])`；
3. 若有新 UI：加 catalog 组件（根 `id:"root"`）+ mapper 的 `xxx_components` + `_render_parts`
   分支；否则纯文本走 agent 直接回复。**前端 App.tsx 通常零改动。**

## 七、e2e / 自检
- 后端渲染路径：`python -m app.evidence.agent`（喂 fixtures 断言 A2UI DataParts）；mapper
  自检 `python -m app.evidence.mapper`。
- 前端传输：`frontend/web/e2e/transport.spec.ts`（page.route 回放真机抓取的 `Part[]` fixture，
  确定性不触网）验循证卡渲染 + 多轮共用 contextId + 问卷本地评分。跑：
  `cd frontend/web && npx playwright test -c e2e/playwright.config.ts`。
