# 项目文件结构与说明

> **Skill PPT Agents** — 基于 Google ADK + DeepSeek 的 AI 智能体，支持可编辑 PPT 生成（dashi-ppt）、图片型 PPT（ppt-deck）、arXiv 论文检索、Bing 搜索，通过旁路解说（Narrator）架构将 Agent 的工具调用与思考过程翻译为可视化卡片。

## 目录结构总览

```
skill-ppt-agents/
├── 📄 README.md                    # 项目总文档
├── 📄 .env                         # 环境变量（DEEPSEEK_API_KEY、DASHSCOPE_API_KEY 等）
├── 📄 .gitignore                   # Git 忽略规则
├── 📄 .dockerignore                # Docker 构建排除规则
├── 📄 Dockerfile                   # Docker 镜像构建（Python 3.12 + Node.js + Gunicorn）
├── 📄 docker-compose.yml           # Docker Compose 编排
├── 📄 start.sh                     # 本地开发一键启动（后端 + 前端，Ctrl+C 统一关闭）
├── 📄 start_sandbox.sh             # 启动 OpenSandbox 服务端（:8080，幂等）
├── 📄 prepare.sh                   # 一次性环境准备（拉 execd 镜像、构建沙箱镜像）
├── 📄 deploy.sh                    # 生产部署（git pull → docker compose up → 健康检查）
│
├── 🔧 backend/                     # 后端 — Python FastAPI + Google ADK
│   ├── 📄 pyproject.toml           # Python 项目配置（google-adk、fastapi、litellm 等）
│   ├── 📄 env_example              # 环境变量示例
│   ├── 📄 server.py                # FastAPI 主服务（SSE 流式聊天、文件上传/下载、预览）
│   ├── 📄 client.py                # 命令行客户端（消费 SSE 流，分类渲染输出）
│   ├── 📄 README.md                # 后端说明
│   │
│   ├── 📂 cache/                   # SSE 响应缓存（运行时自动生成）
│   │
│   ├── 📂 tests/                   # 后端测试
│   │   ├── 📄 test_sandbox.py
│   │   ├── 📄 test_sandbox_real.py
│   │   └── 📄 test_sandbox_skills_real.py
│   │
│   └── 📂 app/                     # 后端核心应用包
│       ├── 📄 __init__.py
│       ├── 📄 agent.py             # Agent 定义（ADK Agent + 技能注册 + 旁路回调）
│       ├── 📄 tools.py             # 自定义工具（save_to_workspace、vision_analyze 等）
│       ├── 📄 dashi_tools.py       # dashi-ppt 工具集（scaffold/fill/render/stage_media）
│       ├── 📄 narrator.py          # 旁路解说者（拦截工具调用/思考 → 翻译为中文卡片）
│       ├── 📄 narrator_rules.py    # 解说规则配置
│       ├── 📄 create_model.py      # 模型创建辅助
│       ├── 📄 file_reader.py       # 文件读取（PDF/PPTX/PPT/文本，带页码标记）
│       ├── 📄 sandbox.py           # 沙箱管理（每租户隔离、预热池）
│       ├── 📄 instruction.md       # Agent 系统指令
│       │
│       └── 📂 skills/              # Agent 技能目录（ADK Skill 规范）
│           ├── 📂 arxiv-paper-search/   # arXiv 学术论文搜索
│           │   ├── 📄 SKILL.md
│           │   ├── 📄 _meta.json
│           │   └── 📂 scripts/arxiv_search.py
│           │
│           ├── 📂 bingsearch/           # Bing 互联网搜索
│           │   ├── 📄 SKILL.md
│           │   ├── 📄 _meta.json
│           │   └── 📂 scripts/bing_search.py
│           │
│           ├── 📂 dashi-ppt/            # 可编辑 PPT 生成（dashi-ppt 引擎）
│           │   ├── 📄 SKILL.md          # 技能说明（工具化工作流 v0.4.5）
│           │   ├── 📄 _meta.json
│           │   ├── 📄 README.md
│           │   ├── 📂 project/          # PptxGenJS 项目（npm，生成 OOXML .pptx）
│           │   ├── 📂 references/       # 示例 JSON、schema、布局文档
│           │   ├── 📂 scripts/          # 渲染/检查脚本
│           │   └── 📂 assets/           # 图标、模板资源
│           │
│           └── 📂 ppt-deck/             # 图片型 PPT 生成（qwen-image 逐页出图）
│               ├── 📄 SKILL.md
│               └── 📄 _meta.json
│
├── 🎨 frontend/                    # 前端 — React 19 + TypeScript + Vite + Tailwind CSS
│   ├── 📄 index.html               # HTML 入口
│   ├── 📄 package.json             # 前端依赖
│   ├── 📄 vite.config.ts           # Vite 配置（端口 3787，代理 → :8787）
│   ├── 📄 tsconfig.json            # TypeScript 编译配置
│   ├── 📄 metadata.json            # AI Studio 元数据
│   ├── 📄 README.md                # 前端说明
│   │
│   ├── 📂 public/demo/             # 演示资源
│   └── 📂 src/                     # 前端源码
│       ├── 📄 main.tsx             # React 入口（createRoot → App）
│       ├── 📄 App.tsx              # 主应用组件（聊天、SSE 流式渲染、文件上传、Markdown）
│       ├── 📄 api.ts               # API 客户端（SSE 流解析、上传、下载）
│       └── 📄 index.css            # 全局样式（Tailwind、滚动条、Markdown 排版）
│
├── 📂 sandbox-image/               # 沙箱 Docker 镜像构建
│   ├── 📄 Dockerfile
│   ├── 📄 requirements.txt
│   └── 📂 skills/                  # 镜像内置技能副本
│
├── 📂 test/                        # 集成测试
│   ├── 📄 conftest.py
│   ├── 📄 pytest.ini
│   ├── 📄 test_ppt_qa.py
│   ├── 📄 test_optimize_stream.py
│   ├── 📄 test_sandbox_isolation.py
│   └── 📄 test_specific_question.py
│
└── 📂 docs/                        # 文档
    ├── 📄 project-introduction.md  # 项目介绍
    ├── 📄 file_tree.md             # 本文件
    ├── 📄 OpenSandBox本地部署.md    # OpenSandbox 部署指南
    └── 🖼️ 图片资源                 # 截图/GIF
```

## API 端点

```
聊天
  POST   /chat              非流式聊天
  GET    /chat/stream        SSE 流式聊天（6 种事件：text/thought/tool_step/tool_call/narrator_card/done）
  POST   /chat/answer        人在回路回答（澄清问题回灌）

文件
  POST   /upload             上传文件
  GET    /uploads            列出用户上传文件
  GET    /download?path=     下载文件（支持 dashi 产物和 uploads）
  DELETE /uploads            清除上传文件

预览与缓存
  GET    /preview?path=      预览 dashi-ppt 生成的 HTML
  GET    /preview-static/    静态资源
  GET    /cache/info         缓存信息
  DELETE /cache              清除缓存
```

## 技能体系

| 技能 | 目录 | 用途 |
|------|------|------|
| dashi-ppt | `backend/app/skills/dashi-ppt/` | 可编辑 PPT 生成（PptxGenJS 引擎，产出真 OOXML .pptx） |
| ppt-deck | `backend/app/skills/ppt-deck/` | 图片型 PPT 生成（qwen-image 逐页出图，12 种风格） |
| arxiv-paper-search | `backend/app/skills/arxiv-paper-search/` | arXiv 学术论文检索（相关性/最新并行、字段限定） |
| bingsearch | `backend/app/skills/bingsearch/` | Bing 互联网通用搜索 |

## 技术栈

| 层 | 技术 |
|:---|:-----|
| Agent 框架 | Google ADK ≥ 1.0.0 |
| 大模型 | DeepSeek（via LiteLLM） |
| 可编辑 PPT | dashi-ppt（PptxGenJS + Node.js） |
| 图片型 PPT | 阿里 DashScope qwen-image |
| 图片分析 | qwen-vl-max |
| 后端 | Python 3.12 + FastAPI + Uvicorn |
| 前端 | React 19 + TypeScript + Vite 6 + Tailwind CSS 4 |
| 沙箱 | OpenSandbox（预热池 + 每租户隔离） |
