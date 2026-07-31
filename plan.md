# 循证问答 × A2UI 实施计划（evidence-a2ui）

> 目标：把一段"循证医学回答"（见 `meituan.png`）从纯文本升级成 **A2UI 声明式卡片**——由 Google ADK Agent 调用一个"循证问答 skill"，产出结构化循证证据，映射成 A2UI JSON，前端用 A2UI Renderer 渲染成那张"循证决策支持"卡。

---

## 1. 目标与范围

**要做的（MVP）**：
- 用户问诊式提问（"一直打喷嚏，可以吃氯雷他定吗？"）
- ADK Agent 调用 `evidence-qa` skill → 检索/组织循证证据 → 输出**结构化证据对象**
- 后端把结构化证据**确定性映射**成 A2UI JSON（不让 LLM 手搓 JSON，见 §5 决策）
- 前端 A2UI Renderer 渲染出 `meituan.png` 那张卡：证据等级徽章、循证结论、剂量、注意事项、参考文献

**先不做（YAGNI，后续里程碑再说）**：
- 多轮追问式增量更新卡片（A2UI 支持，M3 再接）
- 真实医学数据库联网检索（M0 先用 mock / 固定知识源，跑通链路优先）
- 多端渲染（先 Web React，其他端靠 A2UI 生态现成 renderer 后补）

**边界铁律**：不做诊断、不替代医生。所有回答带证据等级 + 参考文献 + "建议就医"兜底（合规先行）。

---

## 2. 整体架构

```
用户提问
  │
  ▼
ADK Agent (Gemini / 兼容 LLM)
  │  调用 skill
  ▼
evidence-qa skill ──► 循证检索/组织 ──► 结构化证据对象 (EvidenceAnswer, 见 §4)
  │
  ▼
A2UI Mapper (确定性模板, 纯函数) ──► A2UI JSON (声明式组件树)
  │  经 A2A / AG-UI transport
  ▼
前端 A2UI Renderer (官方 renderer + 自定义 Smart Wrapper)
  │
  ▼
渲染出"循证决策支持"卡
```

**关键解耦**：Agent 只产*证据数据*，不碰 UI；Mapper 把证据翻译成 UI 意图；Renderer 负责落地成像素。三层各管一段，符合 A2UI "生成与执行分离" 的核心哲学。

---

## 3. 技术选型（能复用就不自己造）

| 层 | 选型 | 理由 |
|---|---|---|
| Agent 框架 | **Google ADK**（已在用） | 复用现有 skill 机制（`SkillToolset`，skill 目录名 == SKILL.md `name`） |
| LLM | Gemini（或兼容中转） | A2UI 官方即用 Gemini 生成，配套成熟 |
| UI 协议 | **A2UI v0.9.1**（关注 v1.0 rc） | 已是事实标准，声明式 JSON + 可信组件目录，安全 |
| 前端 Renderer | **官方 `a2ui-project/a2ui` Web renderer** | 不自己写渲染引擎，只做自定义组件 Smart Wrapper |
| Transport | **AG-UI 事件流**（或直接 A2A） | A2UI 官方推荐传输层，天然支持流式增量 |
| 后端 | FastAPI（复用现有栈） | SSE / WebSocket 推 A2UI 事件 |

> 决策：**不 fork A2UI，只依赖它**。渲染引擎、组件目录、schema 校验全用官方；我们只贡献「循证证据 → A2UI」的 mapper 和几个循证专属的自定义组件。

---

## 4. 数据契约（skill 的唯一产物）

skill 输出**类型化的证据对象**，这是全系统的真相源。A2UI JSON 由它确定性推导，绝不反过来。

```jsonc
// EvidenceAnswer
{
  "question": "一直打喷嚏，可以吃氯雷他定吗？",
  "intro": "关于氯雷他定的使用，以下是根据循证医学引擎的分析：",
  "evidenceLevel": "A",                    // A/B/C/D，映射徽章颜色
  "basis": "基于 WHO 过敏指南 · 多项 RCT",
  "conclusion": {
    "subject": "氯雷他定（开瑞坦）为第二代抗组胺药",
    "citations": [1],                      // 关联到 references 的 id
    "points": [
      { "label": "适用年龄", "text": "2 岁以上儿童可用" },
      { "text": "儿童剂量：体重≤30kg，5mg/日；>30kg，10mg/日" },
      { "text": "起效快（1-2h），无嗜睡副作用" }
    ]
  },
  "cautions": [
    { "highlight": "建议先明确诊断", "text": "如为过敏性鼻炎，可联合鼻用激素。如症状持续超过 2 周，建议就医。" }
  ],
  "references": [
    { "id": 1, "title": "WHO Guidelines on Allergic Rhinitis Management",
      "source": "WHO", "year": 2024, "isbn": "978-92-4-008XXXX", "url": "..." },
    { "id": 2, "title": "Comparative Efficacy of Second-Generation Antihistamines",
      "source": "Allergy", "year": 2024, "volume": "79(3): 456-470", "pmid": "..." }
  ]
}
```

> 这份 schema 落成 JSON Schema / Pydantic，既约束 skill 输出，又给 mapper 做输入校验。

---

## 5. A2UI Mapper：确定性模板（核心决策）

**决策：不让 LLM 直接生成 A2UI JSON，而是 skill 出结构化证据 + 纯函数模板映射成 A2UI。**

理由（这是本项目最重要的取舍）：
- A2UI 官方鼓励 LLM 直接吐 JSON（flat + ID 引用，便于增量），但**医疗场景容不得畸形 JSON / 幻觉组件**。
- 确定性模板 = 永远合法的 A2UI、永远含参考文献和免责声明、可单测。安全 > 灵活。
- 灵活性仍在：**证据内容**由 LLM 生成，**UI 结构**由模板固定。变的是数据，不变的是版式。

Mapper 是纯函数 `evidenceToA2UI(answer: EvidenceAnswer): A2UIDocument`，把上面的字段拼成 A2UI 组件树（Card → Header/Badge → ConclusionList → CautionBox → ReferenceList）。带一个 `demo()` 自检：喂 `meituan.png` 对应的 fixture，断言产出的 A2UI JSON 通过官方 schema 校验且含 2 条 reference。

> ponytail: 纯函数 + fixture 自检足够；LLM 直生 A2UI 的自由版留到确有需求再加，别提前上。

---

## 6. A2UI 组件目录（catalog）设计

优先用 A2UI **通用原子组件**（Card / Text / Badge / Divider / List / Link）拼出这张卡。只有下面几个循证专属视觉，用 A2UI 的 **Smart Wrapper** 注册成自定义组件：

| 自定义组件 | 作用 | 能否用通用组件替代 |
|---|---|---|
| `EvidenceHeader` | 渐变绿标题条 "循证决策支持 Powered by Evidence Engine" | 可用 Card+Text+样式，先试通用 |
| `EvidenceBadge` | A/B/C 级证据彩色徽章 | 通用 Badge + 颜色映射即可 |
| `ReferenceCard` | 参考文献条目（序号 + 标题 + 来源 + 查看原文链接） | 通用 Card+Text+Link 可拼 |

> 结论：**M1 尽量零自定义组件**，全用通用原子 + 样式属性。只有当通用组件表达不了（如渐变头、角标）再登记 Smart Wrapper。少一个自定义组件少一份维护。

---

## 7. Skill 设计（evidence-qa）

沿用现有 ADK skill 约定：目录名 == SKILL.md front matter `name`。

```
skills/evidence-qa/
  SKILL.md          # name: evidence-qa；描述触发词（能不能吃/怎么用/剂量/循证）
  tools.py          # retrieve_evidence(question) -> EvidenceAnswer
  instruction.md    # 强约束：必带证据等级+参考文献+免责声明；只填 EvidenceAnswer schema
  references/       # M0 mock 知识源 / 后续接真实检索
```

- **M0**：`retrieve_evidence` 返回 mock（就用 `meituan.png` 那条氯雷他定数据），先跑通端到端。
- **M2**：接真实证据源（指南库 / PubMed / 内部循证引擎），LLM 负责组织成 schema，检索负责给依据。
- Agent instruction 里明确：**输出必须是合法 EvidenceAnswer，不得自由发挥 UI，不得省略参考文献与就医提示**。

---

## 8. 后端

- FastAPI，一个 `/chat/stream`（SSE）端点，复用现有栈经验。
- Agent 回合结束 → 拿到 EvidenceAnswer → `evidenceToA2UI` → 经 **AG-UI 事件**推给前端。
- 流式：先推 intro 文本，再推 A2UI 卡（A2UI 扁平结构支持组件逐个 append，M3 做真增量，M1 先整卡一次推）。

---

## 9. 前端

- React + 官方 A2UI Web Renderer。
- 注册组件目录（通用原子 + 少量 Smart Wrapper）。
- 聊天界面：用户气泡 + AI intro 文本 + A2UI 渲染区（那张卡）。
- 样式对齐 `meituan.png`：绿色循证主题、徽章、参考文献列表。

---

## 10. 里程碑

| 里程碑 | 内容 | 完成标准 |
|---|---|---|
| **M0 打通链路** | mock skill → EvidenceAnswer → mapper → A2UI JSON → 官方 renderer 渲染 | 浏览器里出现氯雷他定卡，参考文献可点 |
| **M1 版式还原** | 对齐 `meituan.png`：徽章/结论/注意/引用 | 视觉基本一致，全用通用组件或最少 Smart Wrapper |
| **M2 真检索** | 接真实证据源，LLM 组织 schema | 换个药/症状提问也能出正确循证卡 |
| **M3 增量更新** | 追问时 A2UI 局部更新卡片 | "那成人剂量呢" → 卡片就地改，不重绘整卡 |
| **M4 多端 & 开源** | 补 Vue/移动端 renderer（复用 A2UI 生态）+ 发 GitHub | README + demo + 一键跑 |

---

## 11. 新仓库结构（建议）

```
evidence-a2ui/
  backend/
    app/
      agent.py              # ADK Agent + SkillToolset
      skills/evidence-qa/   # 见 §7
      mapper/
        evidence_to_a2ui.py # 纯函数 + demo() 自检
        schema.py           # EvidenceAnswer (Pydantic)
    server.py               # FastAPI + AG-UI 事件流
  frontend/
    src/
      renderer/             # 挂官方 A2UI renderer + 注册 catalog
      components/           # Smart Wrapper 自定义组件（尽量少）
      App.tsx               # 聊天 UI
  docs/
    plan.md                 # 本文件
    schema.md               # EvidenceAnswer 契约
  fixtures/
    loratadine.json         # meituan.png 对应的 EvidenceAnswer，跑自检用
```

---

## 12. 风险与未决

- **A2UI v0.9 → v1.0 rc 会有 breaking change**：mapper 产出的 JSON 结构可能要跟规范升级。缓解：mapper 集中一处、schema 校验兜底，升级只改一个文件。
- **通用组件能否还原渐变头/徽章**：M1 先试，试不动再上 Smart Wrapper，别预先造。
- **合规**：医疗建议红线。所有卡强制带证据等级 + 参考文献 + 免责/就医提示，由 mapper 保证（不依赖 LLM 自觉）。
- **transport 选 AG-UI 还是裸 A2A**：M0 可先不上事件流，后端直接返回整份 A2UI JSON，M3 做增量时再引 AG-UI。

---

## 13. 下一步（起项目就干）

1. 建空仓 `evidence-a2ui`，落本 `plan.md` + `EvidenceAnswer` schema + `fixtures/loratadine.json`。
2. 写 `evidence_to_a2ui.py`（纯函数 + demo 自检），先对着 fixture 产合法 A2UI JSON。
3. 起官方 A2UI Web renderer，把这份 JSON 渲染出来 —— **这一步绿了，M0 就成了**，剩下都是往里填。
