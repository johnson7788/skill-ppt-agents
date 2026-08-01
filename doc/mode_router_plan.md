# 多模式路由 + 知识图谱模式 实施思路（先设计，暂不写代码）

## 0. 背景与问题
现状 `pipeline.classify()` 把「意图判断 + PICO 抽取 + 闲聊回复」塞进一次 LLM 调用，
`stream_answer` 里用 `_is_chat()` 二分：闲聊 → 纯文字，其余 → 循证卡。

要加「知识图谱模式」（展示药—病—症—禁忌等关系图）时，这个二分 + mega-prompt 会变脏。
本文给出可扩展的重构思路。

---

## 1. 核心洞察：路由 ≠ 渲染（两个正交轴）
- **路由轴**（选哪个模式）= 后端一次分类决策。
- **渲染轴**（画什么 UI）= A2UI catalog + 每模式一个 mapper。

**关键**：A2UI 让前端天然「模式无关」。前端只把收到的组件按 catalog 渲染——
闲聊来 text、循证来 EvidenceHeader/Badge/CautionBox、图谱来 KnowledgeGraph 组件，
**前端不为任何模式写分支**。所以「加图谱模式」≈ 后端 handler + 一个新 catalog 组件，
聊天外壳 `App.tsx` 不动。这正是现有 `evidenceCatalog`（3 个 Smart Wrapper）已验证的路子。

---

## 2. 路由重构：模式注册表 + 判别联合（discriminated union）

### 目标形态
```
Mode = Literal["chat", "evidence", "graph"]

# 路由：只出 mode（+chat 的一句回复），不再塞 PICO
router LLM → {"mode": Mode, "reply"?: str}     # Pydantic 判别联合校验，非法 → 兜底 evidence/chat

DISPATCH: dict[Mode, Handler] = {
    "chat":     chat_handler,       # 0 次额外 LLM，直接 yield text(reply)
    "evidence": evidence_handler,   # pico→search→build_answer→evidence_components（现有流程搬进来）
    "graph":    graph_handler,      # extract_entities→graph_components（新）
}
```
每个 handler 是**同一套事件词汇**（`status`/`thinking`/`chat`/`answer`）的 async generator。
`stream_answer` 只做：`route → DISPATCH[mode](question, history)` 转发事件。

### 加一个模式 = 三步
1. router 枚举加一个值；
2. 写一个 handler（自己的 prompt + mapper）；
3. 若有新 UI，再加一个 catalog 组件（前端一次性注册）。

### 路由的取舍
- evidence 会变成「router + pico + answer」= 3 次 LLM（现在融合写法是 2 次）。省回合两条路：
  - **① router 用更小更快的模型**（配 `ROUTER_MODEL`，路由本就简单）——推荐，干净且路由可独立提速。
  - ② 保留「router 顺带出 PICO」的融合，只是形式化成判别联合的 evidence 分支载荷。
- **不选**：语义路由（embedding 最近邻，模式太少+医疗意图要细判，不划算）；纯关键词（边界脆）。

### 兜底
- 判别联合校验失败 / 未知 mode → 默认 `evidence`（或 `chat`，取保守）。
- handler 内部异常 → 沿用现有边界兜底：发 `error` + `done`，不留死流。

---

## 3. 知识图谱在 A2UI 里怎么画（按推荐度）

A2UI basic catalog **无图组件**，三条路：

### 方案 A（强烈推荐）：自定义 catalog 组件 `KnowledgeGraph`
与现有 EvidenceHeader 同构：
- **前端**：`catalog.tsx` 注册一个 React 组件，props 收 `{nodes, edges}`，内部用图库渲染。
- **后端**：mapper 确定性产一条 `{id, component:"KnowledgeGraph", nodes:[...], edges:[...]}`。
  LLM 只产实体/关系（结构化），**不碰渲染**；Python 决定性拼图数据。
- **图库选型**：交互式选 React Flow (`@xyflow/react`) 或 cytoscape；小图可手撸 SVG 零依赖。
- **数据源**：复用 medical-pico-search 检索结果抽实体关系 / 专门一次 LLM 抽取 / 接真 KG 库。
- 与现有架构完全同构，前端聊天壳不动。

### 方案 B（最省，POC 用）：服务端出图 → basic `Image`
graphviz/mermaid 渲成 SVG/PNG，用 Image 组件贴。缺点：不可交互、丢了 A2UI 声明式增量的好处。

### 方案 C 参照：`googlemaps/a2ui` 把地图做成自定义 catalog
图谱是同类问题，官方已给背书（领域交互组件走自定义 catalog）。

### GitHub 现成参照
- `a2ui-project/a2ui`（官方标准仓）
- `googlemaps/a2ui`（Maps 自定义 catalog，交互组件先例）
- `CopilotKit/generative-ui`（AG-UI/A2UI 生成式 UI 例子）
- dev.to「A2UI + RizzCharts 数据看板」（图表类自定义组件已被反复验证 → 图谱可行）

---

## 4. 涉及文件清单（真要做时）
- 后端
  - `backend/app/evidence/pipeline.py`：`classify` 瘦身成纯路由（出 mode）；新增 `stream_answer`
    的 DISPATCH 分发；evidence 逻辑抽成 `evidence_handler`；新增 `graph_handler`。
  - `backend/app/evidence/mapper.py`：新增 `graph_components()`（产 KnowledgeGraph 组件）。
  - `backend/app/evidence/schema.py`：新增 `GraphNode/GraphEdge/GraphAnswer` 数据契约。
  - （可选）`ROUTER_MODEL` 环境变量 + `_router_model_and_key()`。
- 前端（`frontend/web/src/`）
  - `catalog.tsx`：注册 `KnowledgeGraph` 组件（+ 选定图库依赖）。
  - `App.tsx`：**不改**（渲染任意到达的 surface）。
- 依赖：仅方案 A 交互式图库需新增（React Flow / cytoscape）；SVG 手撸则零新增。

---

## 5. 事件流兼容性（复用 stream_plan.md 的 SSE 协议）
图谱模式仍走同一条 SSE：`status*`（如「抽取实体与关系…」）→ `text`(引导语，可选) →
`data`(createSurface + updateComponents，含 KnowledgeGraph 组件) → `done`。
前端 SSE 读取与分发逻辑完全复用，无需改动。

---

## 7. 何时显示知识图谱比较合适？

知识图谱的价值在于**表达关系**，不是堆信息。判断准则：答案本身是「网状/关系型」而非「线性结论」时才上图谱。

### 适合（关系型问题）
- **相互作用**：药物—药物相互作用（"氯雷他定和XX一起吃可以吗"），一个药牵出一串禁忌/协同。
- **机制/通路**：药 → 靶点 → 通路 → 效应（"为什么XX能降压"），因果链天然是图。
- **鉴别诊断**：症状 → 多个可能疾病 → 区分要点，一对多发散。
- **药—病—症网络**：一种病的多种药、一种药的多个适应症，多对多。
- **共病/合并用药**：多病共存时的用药冲突网络。
- 用户措辞含**"关系/区别/相互作用/机制/为什么/牵连/影响到"**这类关系信号词。

### 不适合（用循证卡或纯文字更好）
- 简单能不能吃 / 剂量问题（"2 岁能吃吗""吃多少"）→ 循证卡的结论+要点已足够。
- 单一事实查询、是非题 → 纯文字或卡片。
- 闲聊/致谢 → chat。
- 节点 < 3 或关系单一：画成图反而比一句话啰嗦。**图谱要有 ≥3 节点、≥2 类边才值得画。**

### 路由信号
router 可据「问题是否涉及多实体+它们之间的关系」判 `graph`；handler 抽完实体/关系后，
若节点/边太少，**降级回循证卡或文字**（同 `_is_chat` 空 PICO 降级的思路），避免尬图。

---

## 8. 其它可玩 / 可用的交互模式（候选池）

先分一个关键轴：**展示型（display-only）vs 交互型（round-trip）**。
A2UI 支持组件带 `action:{event:{name,context}}`，点击回传给 agent → agent 再发新 `updateComponents`，
形成**双向交互**。交互型模式是 A2UI 相对「LLM 吐 markdown」的最大差异化，优先探索。

| 模式 | 场景/触发 | A2UI 实现 | 类型 | 优先级 |
|------|-----------|-----------|------|--------|
| **剂量计算器** | "儿童吃多少""按体重算" | basic 原生 TextField/Slider + Button，回传体重/年龄 → agent 算剂量回填 | 交互 | ⭐ 高（native，几乎白嫖） |
| **自测问卷/量表** | PHQ-9 抑郁、Wells 血栓、焦虑自评 | basic CheckBox/ChoicePicker + 提交 → 评分+解读 | 交互 | ⭐ 高（native + 临床实用） |
| **对比表** | "A 和 B 哪个好""区别" | 自定义 ComparisonTable（药×维度：起效/副作用/年龄/价格）或 basic 拼 Row/Column | 展示 | 高（高频医疗问法） |
| **交互式分诊/决策树** | "我该怎么办"分步引导 | Button 分支，每点一步 agent 发下一层 | 交互 | 中（体验惊艳、设计成本高） |
| **知识图谱** | 关系/机制/相互作用（见 §7） | 自定义 KnowledgeGraph（React Flow/cytoscape） | 展示 | 中 |
| **时间线** | 病程/疗程/服药 X 周后 | 自定义 Timeline | 展示 | 中 |
| **风险评分仪表盘** | CHA2DS2-VASc 等评分 → 分值+档位 | 自定义 Gauge/Meter；输入走问卷式表单 | 交互+展示 | 中 |
| **趋势图/统计图** | 流行病学数据、疗效随时间 | 自定义 Chart（RizzCharts 已验证）或 SVG | 展示 | 中 |
| **证据强度可视化** | GRADE 分级、森林图式 | 强化现有循证卡：等级条/森林图小组件 | 展示 | 中（增强而非新模式） |
| **引用溯源抽屉** | 点结论里的 [1] → 弹出该文献摘要 | 现有 Button action + Modal（basic 有 Modal） | 交互 | 中（低成本增强循证卡） |
| **用药提醒卡** | "提醒我吃药" | 卡片 + "加入提醒" Button action | 交互 | 低（需外部集成） |

### 具体问题示例（先做的两个 native 交互，中/西医）

两者共性：用户问题里带**「按…算 / 我该吃多少 / 帮我评估 / 测一测」**这类需要「用户先给参数、系统再出结果」的信号 → 路由到交互模式，先发一张表单卡（TextField/Slider/CheckBox/Button），用户填完点提交 → agent 回传计算/评分结果卡。

#### A. 剂量计算器（round-trip：表单 → 回填结果）
触发信号：**体重/年龄/体表面积**未知但剂量依赖它；用户说「几岁/多重 吃多少」。

西医：
- 「宝宝 3 岁，发烧了，**对乙酰氨基酚**要吃多少？」→ 表单收「体重 kg」→ 按 10–15 mg/kg/次算单次量 + 每日上限 + 最短间隔。
- 「**布洛芬**混悬液，孩子 15 kg 一次喂几毫升？」→ 收「体重 + 制剂浓度(如 100mg/5ml)」→ 出 mL。
- 「按体重算**阿莫西林**儿童剂量」→ 收「体重 + 适应症(普通/中重)」→ mg/kg/日 分次。
- 「**头孢克洛**每天几次每次多少」→ 收「体重」→ 分次剂量。
- （进阶）「**肌酐清除率**多少要减量」→ 收「年龄/体重/血肌酐/性别」→ Cockcroft-Gault 算 CrCl → 给减量档。

中医：
- 「这个方子按**成人常用量**，小孩要减多少？」→ 收「年龄」→ 按小儿用量折算系数（如 <1 岁 1/4、1–3 岁 1/3、3–7 岁 1/2 成人量）给每味药折算。
- 「**汤剂**一天几次、一次多少毫升？」→ 收「一剂煎出总量 mL + 分服次数」→ 每次 mL。
- 「**颗粒剂/免煎饮片**冲服，我该冲几袋？」→ 收「规格(每袋=g生药) + 医嘱日剂量」→ 袋数。

#### B. 自测问卷 / 量表（round-trip：勾选 → 评分+解读）
触发信号：**「测一测 / 我是不是 / 帮我评估 / 严重吗」** + 主诉是可量表化的症状群。

西医（成熟量表，评分规则确定、可离线算）：
- 「最近总失眠没精神，**我是不是抑郁了**？」→ PHQ-9（9 题 0–3）→ 总分 → 轻/中/重度 + 建议就医档。
- 「老是紧张担心，**焦虑严重吗**？」→ GAD-7。
- 「腿肿会不会是**血栓**？」→ Wells DVT 评分（勾选危险因素）→ 低/中/高概率。
- 「心慌，**中风风险**高不高？」（房颤患者）→ CHA₂DS₂-VASc → 分值 + 抗凝建议档。
- 「睡觉打呼很响，**要不要查睡眠呼吸暂停**」→ STOP-BANG。
- 「**痛经/疼痛**有多严重」→ NRS/VAS 0–10 滑块 → 分级。

中医（辨证/体质问卷，勾选症状 → 倾向性提示，不下诊断）：
- 「**我是什么体质**？」→ 中医体质辨识（王琦九分法，60 题精简版）→ 平和/气虚/阳虚/阴虚/痰湿… 倾向。
- 「总怕冷手脚凉，**是不是阳虚**？」→ 阳虚证症状清单勾选 → 倾向提示 + 建议就诊辨证。
- 「爱上火口干，**是阴虚还是实热**？」→ 症状二分清单 → 倾向 + 提示需四诊合参。

**共同边界（写进 handler 降级/免责）**：
- 量表只给**倾向/分档 + 就医建议**，绝不下临床诊断；结果卡固定附「本结果仅供参考，不能替代医生面诊」。
- 剂量计算只给**说明书/指南范围内**的常规折算，特殊人群（肝肾功能不全、孕哺、过敏史）一律提示就医；超范围/危险信号（如超每日上限）要红字警示。
- 参数缺失或非法（体重填 0、年龄超范围）→ 表单侧校验，不硬算。

### 落地建议（ponytail：先摘低垂果实）
1. **先做 A2UI native 的交互型**：剂量计算器、自测问卷——basic catalog 的 TextField/Slider/
   CheckBox/Button 直接够用，**零新组件、零新依赖**，却拿到「真交互」的最大差异化。
2. 再做**对比表**：高频医疗问法，basic Row/Column 能拼，或轻量自定义组件。
3. 知识图谱、时间线、图表等**展示型自定义组件**按需上，每个 = 一个 catalog 组件 + 一个 mapper。
4. 引用溯源抽屉是**增强现有循证卡**（不是新模式），Modal + action 低成本，体验提升明显。

这些全部复用 §2 的「模式注册表 + 判别联合」路由和 §5 的 SSE 协议——加一个 = 枚举值 + handler
（+ 展示型再加 catalog 组件）。前端聊天壳始终不动。

---

## 9. ponytail 备注（有意的边界）
- 前端保持模式无关：模式差异全在后端 + catalog 组件，聊天壳零分支。
- 路由能用小模型就别上大模型；能融合就别硬拆——先跑通再谈提速。
- 图谱先做方案 A 的最小实体/关系（药—病—症三类节点、"适用/禁忌/一线"三类边），
  别一上来做全本体；SVG 够用就先不引图库。
- 判别联合只校验 mode 合法性，不过度设计每模式的深层 schema 校验——在 handler 内按需。
