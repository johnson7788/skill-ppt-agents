# 回答 SOP：三种模式的路由与回复形式

evidence-a2ui 后端对每条用户消息先做一次**分诊**（`classify`，一次 LLM 调用同时判定
模式 + 抽 PICO），再按模式走不同 handler。前端 A2UI 随到达的组件自适应渲染，**加模式
不用改前端**。

## 一、分诊（唯一入口）

`pipeline.stream_answer` → `classify(context+question)` → `(mode, reply, pico)`。

- 一次 LLM 调用（`_ROUTE_SYS`）折叠「意图判定 + PICO 抽取」，不额外增加往返。
- `mode ∈ {evidence, chat, questionnaire}`，非法值兜底为 `evidence`。
- `_is_chat(mode, pico)`：显式 `chat`，**或** PICO 无 P 且无 I（无从检索）→ 当闲聊处理。
- **分流顺序（关键）**：`questionnaire` 必须在 `_is_chat` 之前判断——它 PICO 恒空，
  否则会被 `_is_chat` 误降级成闲聊。

```
                    ┌─ mode==questionnaire ─→ 问卷模式
classify(msg) ──────┤
                    ├─ _is_chat(mode,pico) ─→ 闲聊模式
                    └─ 否则 ────────────────→ 循证模式
```

## 二、三种模式对照

| 维度 | 循证 evidence | 闲聊 chat | 自测问卷 questionnaire |
|---|---|---|---|
| 触发 | 某病/症状能否用某药、某疗法是否有效等需文献支撑的临床问题 | 寒暄/致谢/闲聊/软健康问题（"我最近很累"）/无 PICO | "测测我是不是抑郁""评估睡眠""我是什么体质"等可量表化症状群 |
| PICO | 填（供向量检索，禁含年份） | 空 | 空 |
| 是否检索 | 是（指南/Meta/RCT） | 否 | 否 |
| LLM 回复 | json_object 结构化结论 | 纯文本、有人设、流式 | 先一句引导语，再生成量表定义 |
| 前端呈现 | AI 气泡(intro) + 循证卡(A2UI surface) | AI 气泡逐 token 流入 | 引导语气泡 + 问卷卡(A2UI surface) |
| 打分/交互 | — | — | **前端本地打分**（选项累加→档位→建议），零往返 |

## 三、逐模式细节

### 1) 循证 evidence
- `run_search(pico)` 检索 → `build_answer_stream` 让 LLM 产 `EvidenceAnswer`（json_object）。
- 事件流：`status`（旁白）→ `thinking`（`reasoning_content` 流式思考气泡）→
  `answer`（整份结论）。
- server 映射：`answer.intro`→`text`（渲染 markdown 进气泡）；
  `evidence_components(answer)`→`createSurface`+`updateComponents`（循证卡）。
- 卡片组件：`EvidenceHeader / EvidenceBadge / CautionBox` + basic 组件，组件树根 `id:"root"`。

### 2) 闲聊 chat（有人设、会共情、流式）
- **不复用**路由器那句 `temperature=0` 的干巴巴 reply。走独立 `_CHAT_SYS` 人设
  （小团健康管家：先共情、再贴心建议或轻追问，2-4 句，口语，不制造焦虑）。
- `stream_chat`：`temperature=0.7`、非 json、`stream=True`，只取 `delta.content`
  （chat 不需要思考气泡）。逐块 yield `{"kind":"chat_delta"}`。
- 兜底：流式没吐任何内容 → 退回路由器 reply 一句，避免空气泡。
- server 映射：`chat_delta` → SSE `{"kind":"chat","delta":...}`。
- 前端：`chat` delta 逐块拼进 `intro`（`escHtml` 转义防注入 → `dangerouslySetInnerHTML`）。

### 3) 自测问卷 questionnaire
- 先 yield 引导语 `{"kind":"chat","text":reply}`（→SSE `text` 整句气泡）。
- `build_questionnaire`：`_QUIZ_SYS` 让 LLM 给**成熟公认**量表（抑郁 PHQ-9 / 焦虑 GAD-7 /
  睡眠 PSQI / 中医体质王琦九分法），所有题共用一组选项，严格 JSON。
- `assemble_quiz`（纯函数）：强制分值为 int、过滤空题、补默认免责声明。
- 事件流：`status`（"匹配自评量表…"）→ `questionnaire`（量表定义）。
- server 映射：`questionnaire_components(q)`（根 `id:"root"`）→ `createSurface`+`updateComponents`。
- **前端本地打分**：`Questionnaire` 组件渲染表单，`scoreQuiz` 选项累加→匹配 band→出
  总分/档位/建议，**全程浏览器内、零新往返**（action handler 仍是 `console.log`）。

## 四、两套事件词表（勿混淆）

- **PIPELINE 事件**（pipeline 生成器 yield）：`status / thinking / chat / chat_delta /
  answer / questionnaire`。
- **SSE 事件**（server 映射后，前端消费）：`status / thinking / text / chat / data /
  error / done`。
- 映射见 `server_evidence.py:_a2a_events`：
  `chat`(整句)→`text`；`chat_delta`(流式)→`chat`；`answer/questionnaire`→`data`。

## 五、多轮与兜底
- 每轮一张独立卡片（前端每轮生成唯一 `surfaceId`），后端无会话态。
- 追问上下文靠前端回传 `history`（此前各轮问题）拼进 prompt。
- 任一步异常 → SSE `error` 友好提示 + `done`，不留死流。
- `EVIDENCE_MOCK=1`：不触网，关键词粗判走问卷 or 循证 fixture（仅联调）。

## 六、加一种新模式怎么做
1. `_ROUTE_SYS` 加一个 mode 枚举值 + `classify` 放行；
2. `stream_answer` 加一个分支 handler（注意分流顺序，PICO 恒空的模式要排在 `_is_chat` 前）；
3. 若有新 UI：加一个 catalog 组件（根组件 `id:"root"`）+ mapper 的 `xxx_components`；
   否则纯文本走 `chat`/`text` 即可。**前端 App.tsx 通常零改动。**
