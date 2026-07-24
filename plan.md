# 在线办公 + Skills 智能体 集成计划

把 ONLYOFFICE（在线看/改 doc/xls/ppt/pdf + 目录树）和 Excalidraw（思维导图/白板）
接进现有「每用户一个沙箱 + Skills 智能体」的架构。

## 0. 现状（集成的地基，别重造）

| 部分 | 现状 | 复用点 |
|------|------|--------|
| 后端 | FastAPI + Google ADK，端口 8585，SSE 流式对话 | 直接加路由 |
| 沙箱 | 每用户独占 OpenSandbox，`_sbkey = user_id`（`backend/app/sandbox.py`） | 文件都存这，天然多租户隔离 |
| 沙箱文件 API | `files.list_directory` / `get_file_info` / `write_file(s)` 已有 | **缺一个 read/download，需补** |
| Skills | ADK `SkillToolset`，Agent 把产物写进沙箱 | 产物自动出现在文件树，能被编辑器打开 |
| 上传/下载 | 落本地 `uploads/<user_id>/`（`server.py` 已标 TODO 要转沙箱） | 统一改成走沙箱 |
| 前端 | React 19 + Vite + Tailwind + lucide，单页聊天 | 加「文件树 + 编辑器」面板 |

结论：**沙箱就是每个用户的"网盘"根目录**。办公套件和白板都只是这个目录上的两种查看器，不引入第二套存储。

## 1. 目标架构

```
┌── 前端 (Vite:3585) ─────────────────────────────┐
│  聊天面板  │  文件树  │  ONLYOFFICE iframe        │
│           │         │  / Excalidraw 组件         │
└──────┬─────────┬───────────────┬─────────────────┘
       │ SSE     │ REST 文件API   │ 编辑器 config/回写
       ▼         ▼                ▼
┌── 后端 FastAPI (8585) ──────────────────────────┐
│  /chat/stream  /files/*   /office/*(网关+JWT)   │
└──────┬──────────────────────┬────────────────────┘
       │ sandbox.files.*       │ HTTP 拉取/回写
       ▼                       ▼
   每用户 OpenSandbox      ONLYOFFICE DocumentServer
   (文件真身在这)          (独立 Docker 容器)
```

- **Excalidraw**：纯前端组件，读写沙箱里的 `.excalidraw` JSON，不碰 DocumentServer。
- **ONLYOFFICE**：DocumentServer 是独立容器，只认 HTTP URL。文件在远程沙箱里，
  所以**后端必须当文件网关**：给 DocServer 一个下载 URL，收它的保存回调再写回沙箱。

## 2. ONLYOFFICE 集成（doc/xls/ppt/pdf）

用社区版镜像 `onlyoffice/documentserver`（免费，~20 并发连接，够用）。

### 2.1 部署
docker-compose 加一个服务：
```yaml
  documentserver:
    image: onlyoffice/documentserver
    environment:
      - JWT_ENABLED=true
      - JWT_SECRET=${OFFICE_JWT_SECRET}   # 与后端共享，安全边界，必须开
    ports: ["8080:80"]
```
DocServer 与后端要能互相通过内网 URL 访问（compose 同网络即可）。

### 2.2 编辑器嵌入（前端）
1. 加载 `http://<docserver>/web-apps/apps/api/documents/api.js`
2. `new DocsAPI.DocEditor("ph", config)`，config 由**后端签发**（前端不持有 JWT 密钥）。
3. 后缀 → documentType 映射：`.docx→word` `.xlsx→cell` `.pptx→slide` `.pdf→pdf`
   （pdf 编辑需 DocServer ≥ 7.x）。

### 2.3 后端三个网关端点（新增，都带 JWT）
| 端点 | 作用 |
|------|------|
| `GET /office/config?path=` | 读沙箱文件信息 → 返回**签名后**的 DocEditor config（含 document.url、document.key、callbackUrl、token） |
| `GET /office/download?token=` | DocServer 来拉原文件：校验 JWT → `sandbox.files` 读字节流回去 |
| `POST /office/callback?token=` | DocServer 保存回调：`status==2` 时下载编辑后文件 → `sandbox.write_file` 写回沙箱 |

关键点（踩坑预防）：
- `document.key`：**同一文件同一版本必须稳定、改动后必须变**，否则编辑器显示缓存旧内容。
  用 `md5(path + mtime)` 生成。
- config 的 `token` 用 `OFFICE_JWT_SECRET` 签；callback 也带 token，**必须验签**再落盘。
- `download`/`callback` 的 URL 里的 host 必须是 DocServer 容器能访问到的后端地址（内网名，不是 localhost）。

## 3. Excalidraw 集成（思维导图/白板）

纯前端，最省事的一块。

1. `npm i @excalidraw/excalidraw`（含思维导图/白板全部能力）。
2. `<Excalidraw initialData={scene} onChange={debounced(save)} />`。
3. 打开：`GET /files/raw?path=x.excalidraw` → `JSON.parse` → initialData。
4. 保存：序列化 `{elements, appState, files}` → `PUT /files/raw`（写回沙箱）。
5. 思维导图/流程图：`@excalidraw/mermaid-to-excalidraw` 把 mermaid 转成 excalidraw 元素，
   Skill 可以直接产出 mermaid，前端转成可编辑白板。

## 4. 文件树 / 目录（两个编辑器共用）

沙箱就是根目录，直接包 `sandbox.files.list_directory`。

新增文件 API（`backend/app/`，比如 `files_api.py`）：
| 端点 | 实现 |
|------|------|
| `GET  /files/tree?path=` | `sandbox.files.list_directory(depth=1)`，前端懒加载展开 |
| `GET  /files/raw?path=`  | 读字节流（Excalidraw/预览/office-download 共用同一读函数） |
| `PUT  /files/raw?path=`  | `sandbox.write_file`（Excalidraw 保存、上传落盘） |
| `DELETE /files?path=`    | 删除 |
| `POST /files/upload`     | 改现有 `/upload`：不落本地，直接写沙箱 |

**前置补丁**：`sandbox.py` 目前只有 `write_file`，没有读。加一个
`read_file(key, path) -> bytes`（`sandbox.files` 的下载/读接口），office 网关和文件 API 都依赖它。

## 5. 与 Skills 智能体的结合点

不用改 Skills 机制，只是打通"产物 ↔ 编辑器"：

- Agent 生成的 `.pptx / .docx / .xlsx / .excalidraw` 写进沙箱 → 文件树里出现 → 点开即用对应编辑器改。
  现有 `dashi-ppt` / `ppt-deck` 产出的 pptx 立刻能在 ONLYOFFICE 里二次编辑。
- 反向：编辑器里改完存回沙箱 → 用户可以让 Agent「基于我刚改的 X.pptx 继续…」，
  Agent 用 `terminal`/`run_skill_script` 读同一份沙箱文件。
- 可选新增 Skill：`docx-gen`（python-docx）、`xlsx-gen`（openpyxl）、`mindmap`（产 mermaid→excalidraw），
  让 Agent 能直接生成这四类可编辑文档。—— 按需再加，先别写。

## 6. 前端改动

- App.tsx 布局改三栏：聊天 | 文件树 | 编辑区（编辑区按后缀路由到 ONLYOFFICE iframe 或 Excalidraw）。
- 新组件：`FileTree.tsx`、`OfficeEditor.tsx`（iframe + DocsAPI）、`WhiteboardEditor.tsx`（Excalidraw）。
- 依赖只加 `@excalidraw/excalidraw`（+ 可选 mermaid-to-excalidraw）；ONLYOFFICE 走 CDN/自托管 script，不进 npm。

## 7. 安全（不能偷懒的地方）

- ONLYOFFICE JWT 全程开启，config 后端签、callback 后端验；密钥只在后端。
- 所有 `/files/*`、`/office/*` 端点按 `user_id` 定位沙箱，**路径做规范化**防 `../` 越权（现有 `/download` 已有 `resolve()+startswith` 校验，复用同一套）。
- DocServer download/callback URL 用不可猜的短期 token（JWT，绑 path + 过期），别用明文路径直连。

## 8. 分阶段落地

1. **P0 文件底座** ✅：`/files/tree|raw`(GET/PUT) + `/files`(DELETE)，前端 `Workspace.tsx` 文件树+预览。
   —— 决策修正：文档空间用**本地 uploads/<user_id>/**（产物/下载已在这），沙箱仍是代码执行环境，没接 `sandbox.read_file`。
2. **P1 Excalidraw** ✅：装 `@excalidraw/excalidraw@0.18`，`WhiteboardEditor.tsx` 读写 `.excalidraw`（走 /files/raw，防抖保存）。
   思维导图/流程图用 Excalidraw 自带的 mermaid→excalidraw，无需额外依赖。
3. **P2 ONLYOFFICE** ✅（代码就绪，待起容器端到端验证）：
   - docker-compose 加 `documentserver`（端口 8081，8080 被 OpenSandbox 占）+ JWT。
   - 后端网关 `/office/config`(签发)、`/office/download`(DocServer 拉)、`/office/callback`(写回)，PyJWT 签验。
   - 前端 `OfficeEditor.tsx` 加载 api.js 嵌 DocEditor；未配置时回退下载。pdf 也走 ONLYOFFICE。
   - env：`OFFICE_JWT_SECRET/OFFICE_PORT/OFFICE_DOCSERVER_URL/OFFICE_BACKEND_URL`。
   - 端到端验证：`docker compose up -d documentserver` 后，工作台打开 .docx/.pptx 编辑保存回写。
4. **P3 Skills 联动**：验证 Agent 产物在编辑器里可编辑、可回喂；按需加 docx/xlsx/mindmap skill。
