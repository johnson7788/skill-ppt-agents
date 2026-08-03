Agent 端（samples/agent/adk/）— 这才是关键

  1. restaurant_finder（Python + ADK + A2A） — 官方旗舰样例
  - 单个 LlmAgent（agent.py:174）：一个 system prompt + 一个工具 get_restaurants。没有独立的"分诊器"LLM 调用。要不要查数据、出哪个 UI
  模板、还是问用户订位细节，全由这同一个 agent 靠工具调用 + 上下文自己决定。
  - 真会话态：Runner 挂 InMemorySessionService（agent.py:144）。每轮 get_session(session_id) → 没有才 create_session（:203-214）。多轮连续性来自
   ADK 存的完整历史（用户+助手+工具调用全在），不是靠前端重传问题串。
  - UI 生成 = DirectJsonFormat：把 catalog schema + 示例塞进 system prompt，LLM 直接吐 <a2ui>…</a2ui> 包裹的 A2UI
  JSON；DirectJsonStreamParser（每 session 一个）在 token 流里增量解析出组件。
  - 校验+重试：JSON 不合 schema 就把错误回喂、重试 1 次（:377-390）。
  - 交互回灌：按钮/表单点击经 A2A DataPart 带 action 回来，agent_executor.py:101-128 把 action 翻译成自然语言 query（"USER_WANTS_TO_BOOK:
  …"）喂回同一 session。所以"提交订位"只是对话里的又一轮。

  2. custom-components-example（Contact/多 surface） — 和我们最像
  - 架构与 restaurant 完全一致（同样 get_session/create_session、单 agent + 工具 get_contact_info）。
  - 差别：accepts_inline_catalogs=True（:103）——把自定义 catalog 内联进 prompt，让 LLM
  认识自定义组件（org_chart/contact_card/floor_plan/WebFrame）；多 surface 靠 LLM 吐多条 createSurface。

  3. agent_executor.py / __main__.py：标准 A2A 服务外壳。A2AStarletteApplication + DefaultRequestHandler，会话键 = A2A context_id（=
  task.context_id）。

  Client 端（4 个渲染器，同一个 restaurant UI）

  - React shell（跟我们同栈）：POST /a2a + SSE；MessageProcessor + basicCatalog；action 经 sendAndProcess 回灌；靠 middleware 走 A2A
  协议，session 由服务器隐式维护。
  - Angular：显式带 contextId——从响应里取出、下一轮再传回（这就是它固定同一 session 的方式）。
  - Lit：直接用 A2AClient SDK（JSON-RPC），session 在协议层。
  - Flutter：genui SDK + 有状态 RestaurantSession 对象。

  Community（了解即可）

  - mcp/a2ui-over-mcp-recipe：MCP 工具直接返回 A2UI JSON（无 agent）。
  - mcp/a2ui-in-mcpapps / mcp-apps-calculator：A2UI 嵌进 MCP App 资源，双 iframe 沙箱隔离。
  - web/pong：静态游戏 demo，非 agent 驱动。
  - client/shared/mcp_apps_inner_iframe：双 iframe 安全代理（postMessage 中转），非完整客户端。

  二、官方架构的三条核心（正好戳中我们的痛点）

  ┌──────┬──────────────────────────────────────────────────────────────────┬───────────────────────────────────────────┐
  │      │                               官方                               │                 我们现状                  │
  ├──────┼──────────────────────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ 状态 │ ADK session 存完整多轮历史（含助手回答/工具调用），键=context_id │ 无状态，前端每轮重传仅问题串 + 循证味前言 │
  ├──────┼──────────────────────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ 路由 │ 无分诊器，单 agent 靠工具调用隐式决定                            │ 独立 classify LLM 先判 mode 再手写分支    │
  ├──────┼──────────────────────────────────────────────────────────────────┼───────────────────────────────────────────┤
  │ 交互 │ 点击/提交 → 翻译成自然语言 → 回喂同一 session 成新一轮           │ 问卷本地打分，不回后端；跨模式追问会误判  │
  └──────┴──────────────────────────────────────────────────────────────────┴───────────────────────────────────────────┘

  我们上一轮讨论的"多轮分不清模式"，官方是从结构上消掉的：LLM 每轮都能看到真实完整对话，用"调了哪个工具"表达
  mode，根本不需要一个脆弱的前置分诊器。

  三、怎么改我们的智能体（三档，从懒到彻底）

  A 档（最小，保留现架构）：加真 server 会话态。
  - 前端一次性生成稳定 session_id（不再每轮换），后端 dict[session_id]→[{role,content,mode}] 存转录；classify 拿完整转录（含助手回答 + 上轮
  mode），删掉那句循证味前言。
  - 约 30 行，不引 ADK。直接修好"从 evidence 切 chat 被误判"。匹配 ADK session 的精神，成本极低。

  B 档（推荐，抓住官方精髓、不搬整套 ADK）：干掉 classify，改单次带工具的 LLM 调用 + 持久历史。
  - 工具 = search_and_answer(pico)、make_questionnaire(symptom)；没调工具就流式共情闲聊。
  - mode = "LLM 调了哪个工具" → evidence 工具→循证卡；questionnaire 工具→量表卡；无工具→chat。
  - 一举消除：独立分诊调用、循证味前言、重传问题 hack、跨模式误判。litellm 原生支持 function-calling + streaming，SSE 映射沿用现有事件。

  C 档（彻底对齐官方）：把循证后端重写成 ADK LlmAgent + A2A（DirectJsonFormat 直出 A2UI + InMemorySessionService 免费拿
  session）。最"正确"但是真重写——放弃现有手写 pipeline/mapper/SSE 那套，还得上 A2A 协议。除非你想长期靠 A2UI 生态，否则 over-kill。

  我的建议：先 A 档止血（今天就能修好你说的有状态需求），把 B 档作为下一步方向（它才是官方"无分诊器"那套的低成本版）。C 档除非要接 A2A
  生态，不做。
