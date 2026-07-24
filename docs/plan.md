# AI 自然语言编辑文档 —— 统一模式（P6）

> 已完成的 P0–P5 集成计划见 `完成.md`。本文只讲**下一步核心改版**：让用户用自然语言
> 稳定地编辑 pptx / docx / xlsx / 白板。

## 0. 为什么要改（现有模式的病根）

现在"AI 编辑文档"的回写走**后端整文件重写**：

- **office**：agent 在沙箱用 python-pptx/python-docx/openpyxl 改原始 XML → `sync_sandbox_to_workspace` 覆盖整个文件。
- **白板**：agent 用 excalidraw-diagram 技能**整图重画** → `save_to_workspace` 覆盖。

这条路有三宗罪（"改背景没生效"就是它们叠加的结果）：

| 病 | 说明 |
|----|------|
| **脆** | 直接写原始 XML 绕过 ONLYOFFICE 渲染引擎。改 slide 级 `p:bg`，被模板首页铺满整页的封面图/形状遮住 → 文件改了但肉眼看不到。 |
| **无粒度** | 只能整文件重写，不能"只改这一段/这张图/这个单元格"。 |
| **抢写 + 不刷新** | 用户正开着 ONLYOFFICE（DocServer 缓存着自己那份）。后端覆盖磁盘后：① 编辑器不重挂就看不到；② 用户一动/autosave，DocServer 把旧副本回写覆盖掉 agent 的改动。 |

**结论**：编辑不该由"后端改二进制"来做，应由"编辑器自己的 API"来做。

## 1. 两个正交维度（这是设计的骨架）

编辑一次文档 = **A. 上下文注入**（prompt 带什么）× **B. 回写通道**（编辑落哪）。

- 用户提的 3 个场景 = **维度 A**，✅ 正确且完整（已实现于 `App.tsx` 的 docHint）。
- 之前的 bug 出在 **维度 B**，之前没系统设计过。两个维度必须一起定。

## 2. 维度 A —— 上下文注入的 3 个场景（确认 + 补全）

| # | 触发 | prompt 注入 | 对应编辑范围 |
|---|------|-------------|--------------|
| 1 | 未打开任何文档 | 无文档上下文 | 通用生成（新建文件） |
| 2 | 打开文档、**未选中** | 文档路径 + 文件名 + 类型 + 用户要求 | **全文级**编辑 |
| 3 | 打开文档、**选中元素** | 路径/名 + **选区位置 locator + 选区内容** + 要求 | **选区级**编辑 |

补充：场景 3 的"选区信息怎么拿"因编辑器而异——
office 用插件 `GetSelectedText`/`GetSelectedContent`；白板用 `appState.selectedElementIds` + `getSceneElements()`；图片用框选坐标。

## 3. 维度 B —— 更好的回写模式（核心改动）

> **总原则：编辑器是唯一真相源；LLM 只产"补丁"；由编辑器自己的 API 把补丁落到用户正看着的那份文档。后端不再碰 office 二进制。**

| 文档类型 | 场景 2（全文级） | 场景 3（选区级） | 落盘 |
|----------|------------------|------------------|------|
| **office** docx/xlsx/pptx | 插件 `callCommand`（Builder API：`Api.GetPresentation()...SetBackground` 等）在 live 编辑器内做全文操作 | 插件 `GetSelectedText` → LLM 改写 → `PasteText`/`callCommand` 替换选区 | ONLYOFFICE 自身 callback（沿用 P2） |
| **白板** excalidraw | `updateScene` 整图替换（或整图重画，够用时保留） | `getSceneElements()`+选区 → LLM 产新 elements → `updateScene` 局部替换 | 现有防抖 `PUT /files/raw` |
| **图片** png/jpg | 整图重绘 | 框选区域重绘 | 新增"写 PNG"工具 |

**为什么 office 走插件 `callCommand` 而不是后端 python-pptx**：插件跑在**用户正在编辑的那个 live 会话**里，通过 ONLYOFFICE 自己的引擎改文档 →
① 所见即所得、无需重挂刷新；② 不与 DocServer 抢写、不会被回写覆盖；③ Builder API 是高层语义（"设置第 1 页背景色"），不碰易被遮罩的原始 XML。
这同时解决了三宗罪，且**选区级和全文级用同一套插件机制**。

## 4. 分期落地

- **P6.0 止血（已做）**：office 覆盖后 `onDocChanged` 触发编辑器重挂重读（`App.tsx` 不再只对白板触发）；instruction.md 加"下载链接铁律"防臆造 host。—— 治标，不改回写通道。
- **P6.1 office 选区编辑走插件**：完成 `完成.md §10.4` 的插件 POC（P-a 写回验证 → P-b 接 LLM），覆盖**场景 3 / office**。这是最优先，因为它验证"插件 `callCommand`/`PasteText` 在社区版 docker 上真能写回"这个唯一没把握的点。
- **P6.2 office 全文编辑走插件**：给插件加"全文指令"通道，用 `callCommand` + Builder API 覆盖**场景 2 / office**（改背景、批量替换等）。**弃用后端 python-pptx 覆盖整文件那条路。**
- **P6.3 上下文注入统一**：前端按 3 场景规范化 docHint / 选区打包；office 与白板共用右侧同一个助手侧栏（跨 iframe `postMessage` 桥），不再各用一套面板。
- **P6.4 图片重绘**（依赖 image skill，最后做）。

## 5. 风险 / 待定

- **LLM 产 Builder API JS 的可靠性未知**（P6.2）→ 先用**受限结构化指令集**（`set_background`/`replace_text`/`insert_text`/`format`，LLM 出 JSON intent，插件翻译成 `callCommand`），覆盖不足再放开生成任意 JS。先窄后宽。
- 插件 `callCommand`/`PasteText` 写回**必须先在本项目的社区版 documentserver docker 上 POC 实测**（`完成.md §10.4` 待确认 4 项）。
- pdf 只读，不在编辑范围。
- 过渡期：P6.1/6.2 未落地前，office 全文编辑仍走 P6.0 的后端重写 + 重挂（脆但能用），完成后切换并删除该路径。
