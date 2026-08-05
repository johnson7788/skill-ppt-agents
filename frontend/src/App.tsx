import { useState, useRef, useEffect, useCallback, useMemo, type ReactNode, type ChangeEvent, type DragEvent, type KeyboardEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowUp,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Download,
  Eye,
  FileText,
  FolderOpen,
  Home,
  ImageIcon,
  HelpCircle,
  Loader2,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Palette,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Upload,
  User,
  Wand2,
  Wrench,
  XCircle,
} from 'lucide-react';
import { streamChat, answerChat, uploadFile, listDecks, deleteDeck, type Deck, type SSEEvent } from './api';

// ─── 类型定义 ────────────────────────────────────────────────────────────────

interface ToolCall {
  id: string;
  tool_name: string;
  display_name: string;
  args_summary: string;
  status: 'running' | 'done' | 'error';
  result_summary: string | null;
}

interface ToolStep {
  step_id: string;
  summary: string;
  call_count: number;
  calls: ToolCall[];
}

interface ThoughtItem {
  raw: string;
  narrated: string | null;
}

interface HistoryMessage {
  id: string;
  role: 'user' | 'assistant';
  text?: string;
  steps?: ToolStep[];
  thoughts?: ThoughtItem[];
  timeline?: TimelineItem[];
}

interface UploadedFile {
  name: string;
  size: number;
  path: string;
}

// ─── 时序类型 ────────────────────────────────────────────────────────────────

type TimelineItem =
  | { kind: 'thought'; data: ThoughtItem }
  | { kind: 'text'; text: string }
  | { kind: 'tool'; step: ToolStep };

// ─── 澄清提问（人在回路）────────────────────────────────────────────────────

interface ClarifyState {
  session_id: string;
  call_id: string;
  question: string;
  choices: string[];
}

// ─── 常量 ────────────────────────────────────────────────────────────────────

const TYPING_CHARS = 3;
const TYPING_INTERVAL = 12;

// ─── 工具图标映射 ────────────────────────────────────────────────────────────

function getToolIcon(toolName: string): ReactNode {
  const map: Record<string, React.ReactNode> = {
    arxiv_search: <Search className="w-4 h-4" />,
    write_file: <FileText className="w-4 h-4" />,
    read_file: <FileText className="w-4 h-4" />,
    execute_command: <Terminal className="w-4 h-4" />,
    load_skill: <Wrench className="w-4 h-4" />,
    web_search: <Search className="w-4 h-4" />,
  };
  return map[toolName] || <Wrench className="w-4 h-4" />;
}

// ─── 预设模版风格（与后端 _STYLE_PRESETS 对应）────────────────────────────────
const PPT_STYLES = [
  '科研答辩风', '麦肯锡风格', '清爽专业风', '数据仪表盘风',
  '党政红风格', '教学课件风', '温暖手工风', '手绘白板风',
  '手绘技术解释风', '电子墨水杂志风', '创意杂志风', '复古扁平插画风',
] as const;

// ─── 示例问题 ─────────────────────────────────────────────────────────────────
type ExampleQuestion = {
  question: string;
  demoFile?: string;  // 内置 demo 文件路径（public/ 下），点击时自动上传
};

const EXAMPLE_QUESTIONS: ExampleQuestion[] = [
  // 可编辑模式：产出可再编辑的 .pptx（dashi-ppt），支持在线预览
  { question: '用可编辑模式，把大语言模型的发展脉络做成一套 12 页 PPT' },
  { question: '用可编辑模式生成一份「Mixture-of-Experts 架构」的教学幻灯片' },
  // 图片模式：整页图片型 PPT（视觉统一，不可逐字编辑）
  { question: '用图片模式，为「2024 年 AI 行业趋势」做一套演示，麦肯锡风格' },
  { question: '用图片模式帮我做一份公司季度业绩汇报 PPT，数据仪表盘风' },
  // 上传资料 → 据内容生成可编辑 PPT
  {
    question: '根据这份讲义，用可编辑模式生成一套演示文稿',
    demoFile: '/demo/LongContextLLM.pptx',
  },
  // 上传风格参考图 → 图片模式模仿风格生成
  {
    question: '参照这张图的视觉风格，用图片模式做一套「RAG 技术」介绍 PPT',
    demoFile: '/demo/benchmark_results.png',
  },
];

// ─── 子组件 ──────────────────────────────────────────────────────────────────

function Header({
  userId,
  onUserIdChange,
  onOpenFiles,
}: {
  userId: string;
  onUserIdChange: (id: string) => void;
  onOpenFiles: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(userId);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  return (
    <header className="h-[52px] md:h-[76px] flex items-center gap-1.5 md:gap-2 px-3 md:px-8 border-b border-slate-200/80 bg-white/75 backdrop-blur-xl text-[13px] md:text-sm font-medium text-slate-700 shrink-0">
      <button
        onClick={onOpenFiles}
        title="打开文件栏"
        className="md:hidden mr-1 p-1.5 text-blue-600 hover:bg-blue-50 rounded-xl transition-colors"
      >
        <Menu className="w-5 h-5" />
      </button>
      <Home className="hidden sm:block w-5 h-5 text-blue-600" />
      <span className="font-bold text-slate-900">PPT</span>
      <ChevronRight className="w-4 h-4 text-slate-500" />
      <span className="text-slate-700 font-semibold truncate">PPT 生成智能体</span>
      <div className="ml-auto flex items-center gap-2">
        {editing ? (
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={() => {
              if (value.trim()) onUserIdChange(value.trim());
              setEditing(false);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                if (value.trim()) onUserIdChange(value.trim());
                setEditing(false);
              }
              if (e.key === 'Escape') {
                setValue(userId);
                setEditing(false);
              }
            }}
            className="bg-[#f1f5f9] border border-slate-200 rounded px-2 py-1 text-[13px] text-slate-800 outline-none w-36"
          />
        ) : (
          <button
            onClick={() => {
              setValue(userId);
              setEditing(true);
            }}
            className="flex items-center gap-1.5 text-[11px] md:text-[12px] text-slate-500 hover:text-slate-700 transition-colors bg-slate-100/40 hover:bg-slate-100 px-2 md:px-2.5 py-1 rounded-lg border border-slate-200/50"
          >
            <User className="w-3.5 h-3.5" />
            {userId}
          </button>
        )}
      </div>
    </header>
  );
}

const HERO_FEATURES = [
  {
    title: '多样化模板与风格',
    body: '提供多种专业模板与设计风格，满足不同场景需求',
    icon: <Wand2 className="w-5 h-5 md:w-8 md:h-8" />,
    tone: 'blue',
  },
  {
    title: '智能内容生成',
    body: '基于你的需求，自动生成逻辑清晰、内容专业的 PPT',
    icon: <Sparkles className="w-5 h-5 md:w-8 md:h-8" />,
    tone: 'violet',
  },
  {
    title: '专业视觉呈现',
    body: '智能匹配配色与排版，打造高颜值演示文稿',
    icon: <ImageIcon className="w-5 h-5 md:w-8 md:h-8" />,
    tone: 'emerald',
  },
  {
    title: '数据安全可靠',
    body: '支持企业私有化部署，保障数据安全与隐私',
    icon: <ShieldCheck className="w-5 h-5 md:w-8 md:h-8" />,
    tone: 'orange',
  },
] as const;

function FeatureCard({
  feature,
}: {
  feature: (typeof HERO_FEATURES)[number];
}) {
  const toneClass = {
    blue: {
      card: 'border-blue-100/80 bg-white/80',
      icon: 'bg-blue-100 text-blue-600',
      line: 'bg-blue-500',
    },
    violet: {
      card: 'border-violet-100/80 bg-white/80',
      icon: 'bg-violet-100 text-violet-600',
      line: 'bg-violet-500',
    },
    emerald: {
      card: 'border-emerald-100/80 bg-white/80',
      icon: 'bg-emerald-100 text-emerald-500',
      line: 'bg-emerald-400',
    },
    orange: {
      card: 'border-orange-100/80 bg-white/80',
      icon: 'bg-orange-100 text-orange-500',
      line: 'bg-orange-400',
    },
  }[feature.tone];

  return (
    <div
      className={`w-[132px] shrink-0 rounded-2xl md:w-auto md:shrink md:rounded-[22px] border shadow-[0_12px_28px_rgba(49,82,166,0.1)] md:shadow-[0_18px_44px_rgba(49,82,166,0.13)] backdrop-blur-md px-3 py-3.5 md:px-7 md:py-6 text-center ${toneClass.card}`}
    >
      <div className={`mx-auto w-9 h-9 md:w-[58px] md:h-[58px] rounded-full flex items-center justify-center mb-2.5 md:mb-4 ${toneClass.icon}`}>
        {feature.icon}
      </div>
      <h3 className="text-[13px] md:text-[22px] font-black text-[#08256f] leading-snug">
        {feature.title}
      </h3>
      <p className="mt-1.5 md:mt-3 text-[10px] md:text-[16px] leading-relaxed text-[#526996]">
        {feature.body}
      </p>
      <div className={`mx-auto mt-2.5 md:mt-4 h-1 w-10 md:w-16 rounded-full ${toneClass.line}`} />
    </div>
  );
}

function WelcomeHero({
  examples,
  onExampleClick,
  disabled,
}: {
  examples: ExampleQuestion[];
  onExampleClick: (ex: ExampleQuestion) => void;
  disabled: boolean;
}) {
  return (
    <section className="relative min-h-full overflow-hidden px-4 pt-4 pb-3 md:px-8 md:pt-7 md:pb-5">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute inset-x-0 top-0 h-[42%] bg-[linear-gradient(152deg,rgba(219,234,254,0.95),rgba(255,255,255,0.72)_54%,rgba(233,231,255,0.95))]" />
        <div className="absolute -top-10 left-[-6%] h-48 md:h-72 w-[62%] rounded-[50%] bg-blue-100/60 blur-2xl" />
        <div className="absolute top-20 md:top-24 right-[-8%] h-48 md:h-72 w-[46%] rounded-[50%] bg-violet-100/70 blur-2xl" />
        <div className="absolute top-[12%] left-0 right-0 h-px bg-white/80 rotate-[-12deg]" />
      </div>

      <div className="relative mx-auto flex min-h-full max-w-[1280px] flex-col items-center text-center">
        <div className="hidden md:flex relative mb-2 h-[112px] w-[150px] items-center justify-center">
          <div className="absolute bottom-2 h-8 w-32 rounded-full bg-blue-500/10 blur-md" />
          <div className="relative h-[92px] w-[86px] rounded-[18px] bg-[linear-gradient(135deg,#4f83ff,#7c3df2)] shadow-[0_18px_42px_rgba(78,111,255,0.28)] flex items-center justify-center">
            <div className="absolute right-0 top-0 h-7 w-7 rounded-bl-xl bg-white/20" />
            <span className="text-5xl font-black text-white">P</span>
          </div>
        </div>

        <h1 className="mt-1 text-[32px] leading-none md:text-[68px] font-black text-[#08256f]">
          PPT 生成智能体
        </h1>
        <p className="mt-3 md:mt-4 text-[13px] md:text-[24px] font-bold text-[#5c6f9b]">
          选风格 · 定模板 · 一句话生成专业PPT
          <span className="hidden md:inline"> ｜ 图文并茂 · 逻辑清晰 · 设计精美</span>
        </p>

        {/* 移动端：特色卡片水平轮播 */}
        <div className="mt-5 -mx-4 w-[calc(100%+2rem)] overflow-hidden md:hidden">
          <div className="feature-marquee-track flex w-max gap-3 px-4 pb-2">
            {[...HERO_FEATURES, ...HERO_FEATURES].map((feature, index) => (
              <FeatureCard key={`${feature.title}-${index}`} feature={feature} />
            ))}
          </div>
        </div>

        {/* PC 端：特色卡片网格 */}
        <div className="hidden md:mt-10 md:grid md:w-full md:grid-cols-4 md:gap-6">
          {HERO_FEATURES.map((feature) => (
            <FeatureCard key={feature.title} feature={feature} />
          ))}
        </div>

        {/* 移动端：标语 */}
        <div className="md:hidden mt-4 flex items-center justify-center gap-2 text-[#3456d9]">
          <Sparkles className="w-3.5 h-3.5 shrink-0" />
          <span className="text-[12px] font-black">让专业的 PPT 制作更简单 · 更高效 · 更出色</span>
        </div>

        {/* PC 端：标语 */}
        <div className="mt-6 hidden w-full items-center justify-center gap-8 text-[#3456d9] md:flex">
          <div className="h-px w-56 bg-blue-200" />
          <div className="flex items-center gap-3 text-[22px] font-black">
            <Sparkles className="w-6 h-6" />
            让专业的 PPT 制作更简单 · 更高效 · 更出色
          </div>
          <div className="h-px w-56 bg-blue-200" />
        </div>

        {/* 移动端：示例问题向上轮播 */}
        <div className="md:hidden mt-4 w-full overflow-hidden rounded-2xl" style={{ height: 84 }}>
          <div className="example-scroll-track flex flex-col">
            {examples.map((ex, i) => (
              <button
                key={i}
                className="w-full flex items-center gap-2 px-4 py-3 text-left text-[12px] font-semibold leading-snug text-slate-600 border-b border-slate-100 last:border-b-0 active:bg-blue-50 transition-colors"
                onClick={() => onExampleClick(ex)}
                disabled={disabled}
              >
                {ex.demoFile ? (
                  <FileText className="w-3.5 h-3.5 shrink-0 text-amber-500" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5 shrink-0 text-blue-500" />
                )}
                <span className="line-clamp-1">{ex.question}</span>
              </button>
            ))}
            {examples.map((ex, i) => (
              <button
                key={`dup-${i}`}
                className="w-full flex items-center gap-2 px-4 py-3 text-left text-[12px] font-semibold leading-snug text-slate-600 border-b border-slate-100 last:border-b-0 active:bg-blue-50 transition-colors"
                onClick={() => onExampleClick(ex)}
                disabled={disabled}
              >
                {ex.demoFile ? (
                  <FileText className="w-3.5 h-3.5 shrink-0 text-amber-500" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5 shrink-0 text-blue-500" />
                )}
                <span className="line-clamp-1">{ex.question}</span>
              </button>
            ))}
          </div>
        </div>

        {/* PC 端：示例问题按钮 */}
        <div className="mt-5 hidden max-w-5xl flex-wrap justify-center gap-2 md:flex">
          {examples.slice(0, 4).map((ex, i) => (
            <button
              key={i}
              className="group inline-flex max-w-[420px] items-center gap-2 rounded-full border border-blue-100 bg-white/70 px-4 py-2 text-left text-[13px] font-semibold leading-snug text-slate-600 shadow-sm hover:border-blue-200 hover:bg-white hover:text-blue-700 transition-colors"
              onClick={() => onExampleClick(ex)}
              disabled={disabled}
            >
              {ex.demoFile ? (
                <FileText className="w-3.5 h-3.5 shrink-0 text-amber-500" />
              ) : (
                <Sparkles className="w-3.5 h-3.5 shrink-0 text-blue-500" />
              )}
              <span className="truncate">{ex.question}</span>
            </button>
          ))}
        </div>

      </div>
    </section>
  );
}

function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end mb-5 w-full max-w-[1180px] mx-auto px-4 md:px-8">
      <div className="bg-[#2563eb] text-white px-5 py-4 rounded-2xl rounded-tr-sm max-w-[min(820px,92%)] leading-relaxed text-[15px] shadow-sm whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

function SubCallRow({ call }: { call: ToolCall }) {
  const [showResult, setShowResult] = useState(false);
  const hasArgs = call.args_summary && call.args_summary.length > 0;
  const hasResult = call.result_summary && call.result_summary.length > 0;

  // todo 工具：尝试解析 todos 数据
  const isTodo = call.tool_name === 'todo';
  const todos: TodoItem[] | null = useMemo(() => {
    if (!isTodo || !call.result_summary) return null;
    try {
      const parsed = JSON.parse(call.result_summary);
      return Array.isArray(parsed) ? parsed : null;
    } catch {
      return null;
    }
  }, [isTodo, call.result_summary]);

  return (
    <div className="py-1.5">
      <div className="flex items-center gap-2 text-[13px]">
        {call.status === 'running' ? (
          <Loader2 className="w-3.5 h-3.5 text-amber-600 animate-spin flex-shrink-0" />
        ) : call.status === 'error' ? (
          <XCircle className="w-3.5 h-3.5 text-red-600 flex-shrink-0" />
        ) : (
          <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 flex-shrink-0" />
        )}
        <span className="text-slate-600">{getToolIcon(call.tool_name)}</span>
        <span className="text-slate-700 font-medium">{call.display_name}</span>
        <span
          className={`px-1.5 py-0.5 rounded text-[11px] font-medium ${
            call.status === 'running'
              ? 'bg-amber-500/10 text-amber-600'
              : call.status === 'error'
                ? 'bg-red-500/10 text-red-600'
                : 'bg-emerald-500/10 text-emerald-600'
          }`}
        >
          {call.status === 'running' ? '执行中...' : call.status === 'error' ? '错误' : '完成'}
        </span>
        {!isTodo && hasResult && (
          <button
            className="ml-auto text-[11px] text-blue-600 hover:text-blue-700 transition-colors flex items-center gap-0.5"
            onClick={() => setShowResult(!showResult)}
          >
            {showResult ? (
              <>
                <ChevronDown className="w-3 h-3" />
                收起结果
              </>
            ) : (
              <>
                <ChevronRight className="w-3 h-3" />
                查看结果
              </>
            )}
          </button>
        )}
      </div>

      {/* 推理说明（始终显示） */}
      {hasArgs && (
        <div className="mt-1.5 ml-5.5 text-[13px] text-slate-600 leading-relaxed">
          {call.args_summary}
        </div>
      )}

      {/* todo 工具：渲染待办列表 */}
      {isTodo && call.status !== 'running' && todos && (
        <div className="mt-2 ml-5.5">
          <TodoCard todos={todos} />
        </div>
      )}

      {/* 返回结果（可展开） */}
      {!isTodo && showResult && hasResult && (
        <div className="mt-2 ml-5.5">
          <pre className={`text-[12px] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap break-all font-mono leading-relaxed max-h-80 overflow-y-auto ${
            call.status === 'error'
              ? 'bg-red-50/30 text-red-500'
              : 'bg-white/50 text-slate-600'
          }`}>
            {call.result_summary}
          </pre>
        </div>
      )}
    </div>
  );
}

// ─── 待办列表卡片 ──────────────────────────────────────────────────────────

interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
}

function TodoIcon({ status }: { status: TodoItem['status'] }) {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />;
    case 'in_progress':
      return <Loader2 className="w-4 h-4 text-blue-600 animate-spin shrink-0" />;
    case 'cancelled':
      return <XCircle className="w-4 h-4 text-slate-500 shrink-0" />;
    default:
      return <Circle className="w-4 h-4 text-slate-500 shrink-0" />;
  }
}

function TodoCard({ todos }: { todos: TodoItem[] }) {
  return (
    <div className="space-y-1.5">
      {todos.map((t) => (
        <div
          key={t.id}
          className={`flex items-start gap-2.5 px-3 py-2 rounded-lg text-[13px] ${
            t.status === 'completed'
              ? 'text-slate-600 line-through'
              : t.status === 'cancelled'
                ? 'text-slate-500 line-through'
                : t.status === 'in_progress'
                  ? 'text-slate-800 bg-blue-500/5 border border-blue-500/10'
                  : 'text-slate-700'
          }`}
        >
          <TodoIcon status={t.status} />
          <span className="leading-snug">{t.content}</span>
        </div>
      ))}
    </div>
  );
}

function ToolStepCard({ step }: { step: ToolStep }) {
  const [open, setOpen] = useState(true);
  const hasRunning = step.calls.some((c) => c.status === 'running');

  // 从子调用中归纳卡片标题
  const toolNames = [...new Set(step.calls.map((c) => c.display_name))];
  const title = toolNames.length <= 2
    ? toolNames.join(' + ')
    : `${toolNames[0]} + 等 ${toolNames.length - 1} 个工具`;

  return (
    <div className="bg-[#ffffff] border border-slate-200/80 rounded-xl overflow-hidden">
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-slate-100/30 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center gap-3 text-[14px] text-slate-700">
          {open ? (
            <ChevronDown className="w-4 h-4 text-slate-500" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-500" />
          )}
          {hasRunning ? (
            <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
          ) : (
            getToolIcon(step.calls[0]?.tool_name || '')
          )}
          <span className="font-medium">{title}</span>
        </div>
        <div className="text-[13px] text-slate-500">{step.call_count} 次调用</div>
      </div>
      {open && step.calls.length > 0 && (
        <div className="px-5 pb-3 pt-1">
          <div className="border-l-2 border-slate-200/80 pl-4 py-1 space-y-1">
            {step.calls.map((call) => (
              <SubCallRow key={call.id} call={call} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ThoughtCard({ thought, thinking = false }: { thought: ThoughtItem; thinking?: boolean }) {
  const [open, setOpen] = useState(thinking);
  // 思考时自动展开，思考结束自动收缩
  useEffect(() => {
    setOpen(thinking);
  }, [thinking]);
  return (
    <div className="inline-flex flex-col max-w-full">
      <div
        className="inline-flex items-center gap-2 px-4 py-2 bg-[#eef2ff]/40 border border-indigo-300/30 rounded-lg text-indigo-600 text-[14px] font-medium cursor-pointer hover:bg-[#eef2ff]/60 transition-colors w-max"
        onClick={() => setOpen(!open)}
      >
        {open ? (
          <ChevronDown className="w-4 h-4" />
        ) : (
          <ChevronRight className="w-4 h-4" />
        )}
        {thinking ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Brain className="w-4 h-4" />
        )}
        <span>{thinking ? '思考中...' : '思考过程'}</span>
        {thought.narrated && (
          <span className="text-[12px] text-indigo-600/70 truncate max-w-xs">
            {thought.narrated}
          </span>
        )}
      </div>
      {open && (
        <div className="mt-1 px-4 py-3 bg-[#eef2ff]/20 border border-indigo-300/20 rounded-lg text-[13px] text-slate-600 leading-relaxed whitespace-pre-wrap max-w-2xl">
          {thought.raw.replace(/\s+/g, ' ').trim()}
        </div>
      )}
    </div>
  );
}

function AssistantMessage({ msg }: { msg: HistoryMessage }) {
  const hasTools = msg.timeline?.some((t) => t.kind === 'tool');
  let textIndex = 0;
  const totalTexts = msg.timeline?.filter((t) => t.kind === 'text').length ?? 0;

  return (
    <div className="w-full max-w-[1180px] mx-auto px-4 md:px-8 mb-5">
      <div className="flex flex-col gap-2 min-w-0">
        {/* 优先按时序渲染 */}
        {msg.timeline && msg.timeline.length > 0 ? (
          msg.timeline.map((item, i) => {
            if (item.kind === 'thought') {
              return <ThoughtCard key={`t-${i}`} thought={item.data} />;
            }
            if (item.kind === 'text') {
              textIndex++;
              const isFinal = hasTools && textIndex === totalTexts;
              return (
                <div key={`tx-${i}`}>
                  {isFinal && (
                    <div className="flex items-center gap-2 mb-1.5 px-1">
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                      <span className="text-[13px] font-medium text-emerald-600">最终结果</span>
                    </div>
                  )}
                  <div className="bg-[#ffffff] border border-slate-200/80 rounded-xl p-6 text-[15px] text-slate-800 prose-invert">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        table: ({ children }) => (
                          <div className="overflow-x-auto">
                            <table className="w-full text-left border-collapse">{children}</table>
                          </div>
                        ),
                        thead: ({ children }) => (
                          <thead className="border-b border-slate-200/60 text-slate-600 text-[14px]">
                            {children}
                          </thead>
                        ),
                        th: ({ children }) => (
                          <th className="py-3 px-4 font-medium">{children}</th>
                        ),
                        td: ({ children }) => (
                          <td className="py-3 px-4 text-slate-700 border-b border-slate-200/40">
                            {children}
                          </td>
                        ),
                        code: ({ children, className }) => {
                          const isBlock = className?.includes('language-');
                          if (isBlock) {
                            return (
                              <pre className="bg-[#f1f5f9] border border-slate-200 rounded-lg p-4 overflow-x-auto text-[13px]">
                                <code>{children}</code>
                              </pre>
                            );
                          }
                          return (
                            <code className="bg-[#eff6ff] text-blue-700 px-2 py-0.5 rounded text-[13px] border border-slate-200/50">
                              {children}
                            </code>
                          );
                        },
                        a: ({ children, href }) => (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:text-blue-700 underline"
                          >
                            {children}
                          </a>
                        ),
                        p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                        ul: ({ children }) => (
                          <ul className="list-disc pl-5 space-y-1 mb-3">{children}</ul>
                        ),
                        ol: ({ children }) => (
                          <ol className="list-decimal pl-5 space-y-1 mb-3">{children}</ol>
                        ),
                        strong: ({ children }) => (
                          <strong className="text-slate-900 font-bold">{children}</strong>
                        ),
                      }}
                    >
                      {item.text}
                    </ReactMarkdown>
                  </div>
                </div>
              );
            }
            // item.kind === 'tool'
            return <ToolStepCard key={`s-${item.step.step_id}`} step={item.step} />;
          })
        ) : (
          /* 旧消息兼容：没有 timeline 时按旧顺序渲染 */
          <>
            {msg.steps?.map((step) => (
              <ToolStepCard key={step.step_id} step={step} />
            ))}
            {msg.thoughts?.map((t, i) => (
              <ThoughtCard key={i} thought={t} />
            ))}
            {msg.text && (
              <div className="bg-[#ffffff] border border-slate-200/80 rounded-xl p-6 text-[15px] text-slate-800 prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function LiveAgentRow({
  timeline,
  displayedText,
  isStreaming,
}: {
  timeline: TimelineItem[];
  displayedText: string;
  isStreaming: boolean;
}) {
  const hasRunningTool = timeline.some(
    (t) => t.kind === 'tool' && t.step.calls.some((c) => c.status === 'running'),
  );
  const lastItem = timeline[timeline.length - 1];
  const lastWasThought = lastItem?.kind === 'thought';

  // 根据当前阶段显示更精确的状态
  const getStatusText = () => {
    if (hasRunningTool) return '工具执行中...';
    if (lastWasThought) return '思考中...';
    if (timeline.length === 0) return '思考中...';
    // 工具执行完毕后，等待下一阶段（生成回答或继续思考）
    return '分析结果中...';
  };

  return (
    <div className="w-full max-w-[1180px] mx-auto px-4 md:px-8 mb-5">
      <div className="flex flex-col gap-2 min-w-0">
        {/* 时序渲染已刷入的内容 */}
        {timeline.map((item, i) => {
          if (item.kind === 'thought') {
            // 最后一项 thought 且仍在流式输出、尚无正式回答 → 正在思考
            const isThinking =
              isStreaming && i === timeline.length - 1 && !displayedText;
            return (
              <ThoughtCard key={`t-${i}`} thought={item.data} thinking={isThinking} />
            );
          }
          if (item.kind === 'text') {
            return (
              <div
                key={`tx-${i}`}
                className="bg-[#ffffff] border border-slate-200/80 rounded-xl p-6 text-[15px] text-slate-800"
              >
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
              </div>
            );
          }
          return <ToolStepCard key={`s-${item.step.step_id}`} step={item.step} />;
        })}

        {/* 状态指示：流式中且还没有正在显示的文本 */}
        {isStreaming && !displayedText && (
          <div className="flex items-center gap-2 px-4 py-3 bg-[#ffffff] border border-slate-200/80 rounded-xl text-[14px] text-slate-600">
            <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
            {getStatusText()}
          </div>
        )}

        {/* 正在流式输出的文字（还未刷入 timeline） */}
        {displayedText && (
          <div className="bg-[#ffffff] border border-slate-200/80 rounded-xl p-6 text-[15px] text-slate-800">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayedText}</ReactMarkdown>
            {isStreaming && (
              <span className="inline-block w-0.5 h-4 bg-blue-500 ml-0.5 animate-pulse align-text-bottom" />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ClarifyCard({
  clarify,
  onAnswer,
  disabled,
}: {
  clarify: ClarifyState;
  onAnswer: (answer: string) => void;
  disabled: boolean;
}) {
  const [custom, setCustom] = useState('');

  return (
    <div className="w-full max-w-[1180px] mx-auto px-4 md:px-8 mb-5">
      <div className="min-w-0">
        <div className="bg-[#fffbeb] border border-amber-400/30 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-3 text-[13px] font-medium text-amber-600">
            <HelpCircle className="w-4 h-4" />
            需要你确认
          </div>
          <div className="text-[15px] text-slate-800 mb-4 whitespace-pre-wrap">
            {clarify.question}
          </div>
          {clarify.choices.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {clarify.choices.map((choice, i) => (
                <button
                  key={i}
                  disabled={disabled}
                  onClick={() => onAnswer(choice)}
                  className="px-4 py-2 text-[14px] rounded-lg bg-amber-500/10 border border-amber-400/30 text-amber-700 hover:bg-amber-500/20 hover:border-amber-400/50 transition-colors disabled:opacity-40"
                >
                  {choice}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <textarea
              rows={1}
              value={custom}
              disabled={disabled}
              onChange={(e) => setCustom(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (custom.trim()) onAnswer(custom.trim());
                }
              }}
              placeholder="或输入你的回答..."
              className="flex-1 bg-[#f1f5f9] border border-slate-200/60 focus:border-amber-400/50 rounded-lg text-[14px] text-slate-800 placeholder:text-slate-500 resize-none outline-none py-2.5 px-3 max-h-32 min-h-[42px]"
            />
            <button
              disabled={disabled || !custom.trim()}
              onClick={() => onAnswer(custom.trim())}
              className="p-2.5 bg-amber-600 hover:bg-amber-500 text-white rounded-lg transition-colors shrink-0 disabled:opacity-40"
            >
              <ArrowUp className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── 主组件 ──────────────────────────────────────────────────────────────────

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// ─── 侧边栏：已生成的 PPT deck 列表（预览 / 下载 / 删除） ──────────────────────

function DeckSidebar({
  open,
  onToggle,
  decks,
  loading,
  userId,
  onRefresh,
  onPreview,
  onDelete,
}: {
  open: boolean;
  onToggle: () => void;
  decks: Deck[];
  loading: boolean;
  userId: string;
  onRefresh: () => void;
  onPreview: (d: Deck) => void;
  onDelete: (d: Deck) => void;
}) {
  const downloadHref = (name: string) =>
    `/download?user_id=${encodeURIComponent(userId)}&file=${encodeURIComponent(name)}`;
  if (!open) {
    return (
      <div className="hidden md:flex w-12 shrink-0 border-r border-slate-200/80 bg-[#f3f7ff] flex-col items-center py-4">
        <button
          onClick={onToggle}
          title="展开文件栏"
          className="p-2 text-slate-600 hover:text-blue-700 hover:bg-blue-50 rounded-xl transition-colors"
        >
          <PanelLeftOpen className="w-5 h-5" />
        </button>
      </div>
    );
  }
  return (
    <>
      {/* 手机上展开时用遮罩层浮层覆盖聊天区（桌面 md+ 恢复为并排静态栏） */}
      <div onClick={onToggle} className="md:hidden fixed inset-0 bg-slate-950/35 backdrop-blur-[2px] z-30" />
      <div className="fixed inset-y-0 left-0 z-40 w-[82vw] max-w-[320px] shadow-2xl md:static md:z-auto md:w-[328px] md:max-w-none md:shadow-none shrink-0 border-r border-slate-200/80 bg-[#f3f7ff] flex flex-col">
      <div className="flex items-center gap-2 px-4 py-4 md:py-5 border-b border-slate-200/80">
        <FolderOpen className="w-4 h-4 text-blue-600" />
        <span className="text-[15px] md:text-lg font-black text-slate-900">我的文件</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={onRefresh}
            title="刷新"
            className="p-2 text-slate-500 hover:text-blue-700 hover:bg-blue-50 rounded-xl transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={onToggle}
            title="收起"
            className="p-2 text-slate-500 hover:text-slate-700 hover:bg-slate-100/70 rounded-xl transition-colors"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {decks.length === 0 && !loading && (
          <div className="text-[13px] text-slate-500 text-center px-4 py-10 leading-relaxed">
            还没有文件。
            <br />
            上传文件或让 AI 生成 PPT 都会出现在这里。
          </div>
        )}
        {decks.map((d) => {
          const canPreview = !!d.preview_path;
          return (
            <div
              key={d.name}
              className="group rounded-2xl border border-slate-200/80 bg-white/90 shadow-[0_10px_26px_rgba(49,82,166,0.07)] hover:border-blue-200 hover:bg-white transition-colors"
            >
              <button
                onClick={() => canPreview && onPreview(d)}
                disabled={!canPreview}
                className={`w-full text-left px-4 pt-4 pb-2 ${canPreview ? '' : 'cursor-default'}`}
                title={canPreview ? `预览 ${d.name}` : d.name}
              >
                <div className="flex items-start gap-2">
                  <FileText className="w-4 h-4 text-blue-600/80 shrink-0 mt-0.5" />
                  <span className="text-[14px] md:text-[15px] font-bold text-[#07164f] leading-snug break-all line-clamp-2">
                    {d.name}
                  </span>
                </div>
                <div className="text-[12px] text-[#526996] mt-2 ml-6">{formatSize(d.size)}</div>
              </button>
              <div className="flex items-center gap-2 px-4 pb-4 ml-5">
                {canPreview && (
                  <button
                    onClick={() => onPreview(d)}
                    className="flex items-center gap-1 text-[12px] font-semibold text-slate-600 hover:text-blue-700 px-1.5 py-1 rounded-lg hover:bg-blue-50"
                    title="在线预览"
                  >
                    <Eye className="w-3.5 h-3.5" /> 预览
                  </button>
                )}
                <a
                  href={downloadHref(d.name)}
                  download
                  className="flex items-center gap-1 text-[12px] font-semibold text-slate-600 hover:text-emerald-700 px-1.5 py-1 rounded-lg hover:bg-emerald-50"
                  title="下载"
                >
                  <Download className="w-3.5 h-3.5" /> 下载
                </a>
                <button
                  onClick={() => onDelete(d)}
                  className="flex items-center gap-1 text-[12px] text-slate-500 hover:text-red-600 px-1.5 py-1 rounded-lg hover:bg-red-50 ml-auto"
                  title="删除"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
    </>
  );
}

export default function App() {
  const [messages, setMessages] = useState<HistoryMessage[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [userId, setUserId] = useState('default_user');
  const [style, setStyle] = useState<string>('');  // 选中的预设模版风格，空=不指定

  // 流式中间状态
  const [liveSteps, setLiveSteps] = useState<ToolStep[]>([]);
  const [liveThoughts, setLiveThoughts] = useState<ThoughtItem[]>([]);
  const [liveTimeline, setLiveTimeline] = useState<TimelineItem[]>([]);
  const [displayedText, setDisplayedText] = useState('');
  const targetTextRef = useRef('');
  const textBufferRef = useRef('');
  const timelineRef = useRef<TimelineItem[]>([]);
  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 澄清提问（人在回路）：收集状态用 ref 以便回答后续接同一轮
  const [clarify, setClarify] = useState<ClarifyState | null>(null);
  const clarifyPendingRef = useRef(false);
  const collectedStepsRef = useRef<ToolStep[]>([]);
  const collectedThoughtsRef = useRef<ThoughtItem[]>([]);

  // 文件上传
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // PPT deck 侧边栏
  const [decks, setDecks] = useState<Deck[]>([]);
  const [decksLoading, setDecksLoading] = useState(false);
  // 手机默认收起文件栏，让聊天区占满窄屏；桌面(≥768px)默认展开
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 768 : true,
  );

  // 视口缩到 md(768px) 以下时强制收起文件栏（如从桌面浏览器缩到手机宽度、旋转屏幕）
  useEffect(() => {
    const sync = () => {
      if (window.innerWidth < 768) setSidebarOpen(false);
    };
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, []);
  const openPreview = useCallback((d: Deck) => {
    if (d.preview_path)
      window.open(`/preview?path=${encodeURIComponent(d.preview_path)}`, '_blank', 'noopener');
  }, []);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 聊天上下文 chips 只反映本次会话上传的文件，刷新不自动带入历史文件（历史文件在左侧边栏）

  // 加载文件列表（挂载时 + 每次流式结束后，捕获新上传/生成的文件）
  const loadDecks = useCallback(() => {
    setDecksLoading(true);
    listDecks(userId)
      .then((data) => setDecks(data.decks || []))
      .catch(() => {})
      .finally(() => setDecksLoading(false));
  }, [userId]);

  useEffect(() => {
    if (!isStreaming) loadDecks();
  }, [isStreaming, loadDecks]);

  const handleDeleteDeck = useCallback(
    async (d: Deck) => {
      if (!window.confirm(`确定删除「${d.name}」？此操作不可撤销。`)) return;
      try {
        await deleteDeck(d.name, userId);
        setDecks((prev) => prev.filter((x) => x.name !== d.name));
        setUploadedFiles((prev) => prev.filter((x) => x.name !== d.name));
      } catch (err) {
        console.error('Delete file failed:', err);
      }
    },
    [userId],
  );

  // 自动滚底
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, displayedText, liveSteps, liveTimeline, clarify]);

  // 打字机效果
  const startTyping = useCallback(() => {
    if (typingTimerRef.current) return;
    typingTimerRef.current = setInterval(() => {
      setDisplayedText((prev) => {
        const target = targetTextRef.current;
        if (prev.length >= target.length) return prev;
        return target.slice(0, prev.length + TYPING_CHARS);
      });
    }, TYPING_INTERVAL);
  }, []);

  const stopTyping = useCallback(() => {
    if (typingTimerRef.current) {
      clearInterval(typingTimerRef.current);
      typingTimerRef.current = null;
    }
    setDisplayedText(targetTextRef.current);
  }, []);

  // ─── 文件上传 ──────────────────────────────────────────────────────────

  const doUpload = useCallback(async (file: File, uid?: string) => {
    setUploading(true);
    try {
      const result = await uploadFile(file, uid || userId);
      if (result.success) {
        setUploadedFiles((prev) => [
          ...prev,
          { name: result.filename, size: result.size, path: result.path || '' },
        ]);
        loadDecks();
      }
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
    }
  }, [userId, loadDecks]);

  const handleFileSelect = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) doUpload(file);
      e.target.value = '';
    },
    [doUpload],
  );

  // ─── 示例问题点击 ──────────────────────────────────────────────────────

  const handleClickExample = useCallback(
    async (ex: ExampleQuestion) => {
      if (ex.demoFile && !isStreaming) {
        // 自动上传内置 demo 文件
        setUploading(true);
        try {
          const resp = await fetch(ex.demoFile);
          const blob = await resp.blob();
          const fileName = ex.demoFile.split('/').pop()!;
          const file = new File([blob], fileName, { type: blob.type });
          await doUpload(file, userId);
        } catch (err) {
          console.error('Demo file upload failed:', err);
        } finally {
          setUploading(false);
        }
      }
      setInput(ex.question);
    },
    [doUpload, isStreaming],
  );

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) doUpload(file);
    },
    [doUpload],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragging(false);
  }, []);

  // 只把这些文件从「本轮 prompt 上下文」里移除，不删后端文件（侧边栏仍保留）
  const handleClearFiles = useCallback(() => {
    setUploadedFiles([]);
  }, []);

  // 只把该文件移出「本轮 prompt 上下文」，不删后端文件（侧边栏仍保留，删除走侧边栏）
  const handleRemoveFile = useCallback((fileName: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.name !== fileName));
  }, []);

  // ─── 发送消息 ──────────────────────────────────────────────────────────

  // 将累积的文字刷入 timeline
  const flushText = useCallback(() => {
    if (textBufferRef.current) {
      timelineRef.current.push({ kind: 'text', text: textBufferRef.current });
      setLiveTimeline([...timelineRef.current]);
      textBufferRef.current = '';
    }
    // 重置打字机状态 — 已刷入的文字由 timeline 渲染，避免重复显示
    targetTextRef.current = '';
    setDisplayedText('');
  }, []);

  // 把当前实时 timeline 收尾为一条 assistant 历史消息，并重置实时状态
  const finalizeAssistant = useCallback(() => {
    const finalText = targetTextRef.current;
    flushText();
    const finalTimeline = [...timelineRef.current];
    const steps = collectedStepsRef.current;
    const thoughts = collectedThoughtsRef.current;
    if (finalText || steps.length > 0 || thoughts.length > 0 || finalTimeline.length > 0) {
      const assistantMsg: HistoryMessage = {
        id: `assistant_${Date.now()}`,
        role: 'assistant',
        text: finalText || undefined,
        steps: steps.length > 0 ? [...steps] : undefined,
        thoughts: thoughts.length > 0 ? [...thoughts] : undefined,
        timeline: finalTimeline.length > 0 ? finalTimeline : undefined,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    }
    targetTextRef.current = '';
    textBufferRef.current = '';
    timelineRef.current = [];
    collectedStepsRef.current = [];
    collectedThoughtsRef.current = [];
    setDisplayedText('');
    setLiveSteps([]);
    setLiveThoughts([]);
    setLiveTimeline([]);
  }, [flushText]);

  // 消费一个 SSE 事件流（首轮 streamChat 与 clarify 续接 answerChat 共用）
  const processStream = useCallback(
    async (gen: AsyncGenerator<SSEEvent>) => {
      for await (const evt of gen) {
        switch (evt.type) {
          case 'text': {
            const chunk = evt.text as string;
            targetTextRef.current += chunk;
            textBufferRef.current += chunk;
            startTyping();
            break;
          }

          case 'thought': {
            flushText();
            const rawChunk = evt.raw as string;
            const narrated = (evt.narrated as string) || null;
            const lastTlItem = timelineRef.current[timelineRef.current.length - 1];
            const shouldMerge = lastTlItem?.kind === 'thought';
            if (shouldMerge) {
              const last = collectedThoughtsRef.current[collectedThoughtsRef.current.length - 1];
              last.raw += rawChunk;
              if (narrated) last.narrated = narrated;
              setLiveThoughts([...collectedThoughtsRef.current]);
              timelineRef.current[timelineRef.current.length - 1] = { kind: 'thought', data: { ...last } };
              setLiveTimeline([...timelineRef.current]);
            } else {
              const t: ThoughtItem = { raw: rawChunk, narrated };
              collectedThoughtsRef.current.push(t);
              setLiveThoughts([...collectedThoughtsRef.current]);
              timelineRef.current.push({ kind: 'thought', data: t });
              setLiveTimeline([...timelineRef.current]);
            }
            break;
          }

          case 'tool_step': {
            flushText();
            const step: ToolStep = {
              step_id: evt.step_id as string,
              summary: evt.summary as string,
              call_count: evt.call_count as number,
              calls: (evt.calls as ToolCall[]) || [],
            };
            collectedStepsRef.current.push(step);
            setLiveSteps((prev) => [...prev, step]);
            timelineRef.current.push({ kind: 'tool', step });
            setLiveTimeline([...timelineRef.current]);
            break;
          }

          case 'tool_call': {
            const step_id = evt.step_id as string;
            const call_id = evt.call_id as string;
            const status = evt.status as 'done' | 'error';
            const result_summary = evt.result_summary as string;
            const updateCall = (steps: ToolStep[]) =>
              steps.map((s) =>
                s.step_id !== step_id
                  ? s
                  : {
                      ...s,
                      calls: s.calls.map((c) =>
                        c.id === call_id ? { ...c, status, result_summary } : c,
                      ),
                    },
              );
            setLiveSteps((prev) => updateCall(prev));
            const idx = collectedStepsRef.current.findIndex((s) => s.step_id === step_id);
            if (idx >= 0) collectedStepsRef.current[idx] = updateCall([collectedStepsRef.current[idx]])[0];
            const tl = timelineRef.current.map((item) =>
              item.kind === 'tool' && item.step.step_id === step_id
                ? ({ ...item, step: updateCall([item.step])[0] } as TimelineItem)
                : item,
            );
            timelineRef.current = tl;
            setLiveTimeline(tl);
            break;
          }

          case 'clarify': {
            flushText();
            clarifyPendingRef.current = true;
            setClarify({
              session_id: evt.session_id as string,
              call_id: evt.call_id as string,
              question: (evt.question as string) || '',
              choices: (evt.choices as string[]) || [],
            });
            break;
          }

          case 'done': {
            stopTyping();
            flushText();
            // 触发了澄清提问：保留实时 timeline，停止 spinner，等待用户回答后续接
            if (clarifyPendingRef.current) {
              setIsStreaming(false);
              return;
            }
            finalizeAssistant();
            setIsStreaming(false);
            break;
          }
        }
      }
    },
    [startTyping, stopTyping, flushText, finalizeAssistant],
  );

  // 流式异常统一处理（中止 / 网络错误）
  const handleStreamError = useCallback(
    (err: unknown) => {
      stopTyping();
      clarifyPendingRef.current = false;
      setClarify(null);
      if (err instanceof DOMException && err.name === 'AbortError') {
        finalizeAssistant();
        return;
      }
      console.error('SSE error:', err);
      const errMsg = err instanceof Error ? err.message : '未知错误';
      finalizeAssistant();
      setMessages((prev) => [
        ...prev,
        { id: `error_${Date.now()}`, role: 'assistant', text: `请求失败: ${errMsg}` },
      ]);
    },
    [stopTyping, finalizeAssistant],
  );

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput('');
    setIsStreaming(true);
    setClarify(null);
    clarifyPendingRef.current = false;

    // 文件提示
    let fileHint = '';
    if (uploadedFiles.length > 0) {
      fileHint = '\n\n[已上传文件: ' + uploadedFiles.map((f) => f.name).join(', ') + ']';
    }
    // 模版风格提示：让 agent 用选定的预设风格生成 PPT
    const styleHint = style ? `\n\n[PPT 模版风格: ${style}]` : '';
    setMessages((prev) => [
      ...prev,
      { id: `user_${Date.now()}`, role: 'user', text: text + fileHint + styleHint },
    ]);

    // 重置流式状态
    targetTextRef.current = '';
    textBufferRef.current = '';
    timelineRef.current = [];
    collectedStepsRef.current = [];
    collectedThoughtsRef.current = [];
    setDisplayedText('');
    setLiveSteps([]);
    setLiveThoughts([]);
    setLiveTimeline([]);

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      // 精确匹配到内置示例问题时标记 example，让后端用稳定 key 缓存/回放整段过程
      const isExample = EXAMPLE_QUESTIONS.some((e) => e.question === text);
      await processStream(streamChat(text + styleHint, userId, ac.signal, isExample));
      // 流结束但未收到 'done' 事件时，手动收尾
      if (!clarifyPendingRef.current) {
        finalizeAssistant();
      }
    } catch (err: unknown) {
      handleStreamError(err);
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [input, isStreaming, uploadedFiles, processStream, handleStreamError, userId, style]);

  // 回答 clarify 澄清提问，续接同一 session
  const submitAnswer = useCallback(
    async (answer: string) => {
      if (!clarify || isStreaming || !answer) return;
      const c = clarify;
      setClarify(null);
      clarifyPendingRef.current = false;
      setIsStreaming(true);

      const ac = new AbortController();
      abortRef.current = ac;
      try {
        await processStream(answerChat(c.session_id, c.call_id, answer, userId, ac.signal));
      } catch (err: unknown) {
        handleStreamError(err);
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [clarify, isStreaming, processStream, handleStreamError, userId],
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.nativeEvent.isComposing) return;
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    },
    [sendMessage],
  );

  // 自动调整 textarea 高度
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 128) + 'px';
    }
  }, [input]);

  const isInitialScreen = messages.length === 0 && !isStreaming && !clarify;

  return (
    <div className="h-dvh flex bg-[#f7faff] font-sans selection:bg-blue-500/30">
      <DeckSidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        decks={decks}
        loading={decksLoading}
        userId={userId}
        onRefresh={loadDecks}
        onPreview={openPreview}
        onDelete={handleDeleteDeck}
      />
      <div className="flex-1 flex flex-col min-w-0">
      <Header
        userId={userId}
        onUserIdChange={setUserId}
        onOpenFiles={() => setSidebarOpen(true)}
      />

      <main className={`flex-1 overflow-y-auto ${isInitialScreen ? 'py-0' : 'py-6'}`}>
        {/* 欢迎消息 */}
        {isInitialScreen && (
          <WelcomeHero
            examples={EXAMPLE_QUESTIONS}
            onExampleClick={handleClickExample}
            disabled={isStreaming || uploading}
          />
        )}

        {/* 历史消息 */}
        {messages.map((msg) =>
          msg.role === 'user' ? (
            <UserMessage key={msg.id} text={msg.text || ''} />
          ) : (
            <AssistantMessage key={msg.id} msg={msg} />
          ),
        )}

        {/* 流式实时渲染（澄清等待期间也保持可见） */}
        {(isStreaming || clarify) && (
          <LiveAgentRow
            timeline={liveTimeline}
            displayedText={displayedText}
            isStreaming={isStreaming}
          />
        )}

        {/* 澄清提问卡片 */}
        {clarify && (
          <ClarifyCard clarify={clarify} onAnswer={submitAnswer} disabled={isStreaming} />
        )}

        <div ref={messagesEndRef} />
      </main>

      {/* 已上传文件栏 */}
      {uploadedFiles.length > 0 && (
        <div className="w-full max-w-[1286px] mx-auto px-4 md:px-8 pb-2">
          <div className="flex flex-wrap items-center gap-1.5 py-2">
            {uploadedFiles.map((f, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 bg-white/80 text-slate-700 px-2.5 py-1.5 rounded-xl text-[12px] border border-slate-200/70 shadow-sm"
                title={f.path}
              >
                <Paperclip className="w-3 h-3 text-slate-500" />
                {f.name} ({formatSize(f.size)})
                <button
                  className="text-slate-500 hover:text-red-600 ml-0.5"
                  onClick={() => handleRemoveFile(f.name)}
                >
                  <XCircle className="w-3 h-3" />
                </button>
              </span>
            ))}
            <button
              className="text-slate-500 hover:text-red-600 text-[12px] flex items-center gap-1 px-1.5"
              onClick={handleClearFiles}
            >
              <Trash2 className="w-3 h-3" />
              清除全部
            </button>
          </div>
        </div>
      )}

      {/* 文件拖拽区域 */}
      <div
        className={`w-full max-w-[1286px] mx-auto px-4 md:px-8 transition-opacity ${
          isDragging ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <div className="border-2 border-dashed border-blue-500/50 rounded-xl py-3 text-center text-[13px] text-blue-600 bg-blue-500/5 mb-2">
          {uploading ? '上传中...' : '松开鼠标上传文件'}
        </div>
      </div>

      {/* 输入区域 */}
      <div className="w-full max-w-[1286px] mx-auto px-4 md:px-8 pb-3 md:pb-6 pt-1.5 md:pt-2 shrink-0">
        {/* 桌面端：单行布局 */}
        <div
          className={`hidden md:flex bg-white/95 border rounded-[22px] items-end gap-2 p-3 transition-colors shadow-[0_18px_48px_rgba(49,82,166,0.15)] backdrop-blur-xl ${
            isDragging ? 'border-blue-500/60' : 'border-slate-200/80 focus-within:border-blue-200'
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <button
            className="h-14 w-14 flex items-center justify-center text-violet-600 bg-violet-50 hover:bg-violet-100 rounded-2xl transition-colors shrink-0"
            onClick={() => fileInputRef.current?.click()}
            title="上传文件"
          >
            {uploading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Upload className="w-5 h-5" />
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            onChange={handleFileSelect}
            className="hidden"
          />
          <label className="h-14 inline-flex items-center gap-2 shrink-0 rounded-2xl bg-[#f0f3ff] border border-slate-200/70 px-4 text-[#08256f]">
            <Palette className="w-4 h-4 text-violet-600" />
            <select
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              title="PPT 模版风格"
              className="bg-transparent text-[15px] font-bold outline-none max-w-[12rem]"
            >
              <option value="">模板：不指定</option>
              {PPT_STYLES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </label>
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming || !!clarify}
            placeholder={
              clarify
                ? '请先回答上方的确认问题...'
                : isStreaming
                  ? 'AI 正在思考...'
                  : '输入你的研究问题或PPT需求...'
            }
            className="flex-1 min-w-0 bg-transparent text-[18px] text-slate-800 placeholder:text-slate-400 resize-none outline-none py-3 px-4 max-h-32 min-h-[48px] disabled:opacity-50"
          />
          {isStreaming ? (
            <button
              className="h-14 px-5 bg-red-600 hover:bg-red-500 text-white rounded-2xl transition-colors shrink-0 flex items-center gap-2 font-bold"
              onClick={handleStop}
              title="停止"
            >
              <Square className="w-5 h-5" />
              <span className="hidden sm:inline">停止</span>
            </button>
          ) : (
            <button
              className="h-14 px-7 bg-[linear-gradient(135deg,#4f83ff,#7c3df2)] hover:brightness-105 text-white rounded-2xl transition shrink-0 disabled:opacity-40 flex items-center gap-2 font-black shadow-[0_10px_24px_rgba(79,131,255,0.28)]"
              onClick={sendMessage}
              disabled={!input.trim() || !!clarify}
            >
              <Send className="w-5 h-5" />
              <span className="hidden sm:inline">生成 PPT</span>
            </button>
          )}
        </div>

        {/* 移动端：两行布局 — 上行 textarea，下行 上传+模板(左) + 发送(右) */}
        <div
          className={`md:hidden bg-white/95 border rounded-[20px] transition-colors shadow-[0_14px_36px_rgba(49,82,166,0.13)] backdrop-blur-xl ${
            isDragging ? 'border-blue-500/60' : 'border-slate-200/80 focus-within:border-blue-200'
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming || !!clarify}
            placeholder={
              clarify
                ? '请先回答上方的确认问题...'
                : isStreaming
                  ? 'AI 正在思考...'
                  : '输入你的研究问题或PPT需求...'
            }
            className="w-full bg-transparent text-[14px] text-slate-800 placeholder:text-slate-400 resize-none outline-none py-3 px-4 max-h-28 min-h-[44px] disabled:opacity-50"
          />
          <div className="flex items-center gap-2 p-2 pt-0">
            <button
              className="h-10 w-10 flex items-center justify-center text-violet-600 bg-violet-50 hover:bg-violet-100 rounded-2xl transition-colors shrink-0"
              onClick={() => fileInputRef.current?.click()}
              title="上传文件"
            >
              {uploading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Upload className="w-4 h-4" />
              )}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              onChange={handleFileSelect}
              className="hidden"
            />
            <label className="h-10 inline-flex items-center gap-1.5 shrink-0 rounded-2xl bg-[#f0f3ff] border border-slate-200/70 px-3 text-[#08256f]">
              <Palette className="w-3.5 h-3.5 text-violet-600" />
              <select
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                title="PPT 模版风格"
                className="bg-transparent text-[12px] font-bold outline-none max-w-[7.4rem]"
              >
                <option value="">模板：不指定</option>
                {PPT_STYLES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
            {isStreaming ? (
              <button
                className="ml-auto h-10 px-3 bg-red-600 hover:bg-red-500 text-white rounded-2xl transition-colors shrink-0 flex items-center gap-2 font-bold"
                onClick={handleStop}
                title="停止"
              >
                <Square className="w-4 h-4" />
                停止
              </button>
            ) : (
              <button
                className="ml-auto h-10 px-3.5 bg-[linear-gradient(135deg,#4f83ff,#7c3df2)] hover:brightness-105 text-white rounded-2xl transition shrink-0 disabled:opacity-40 flex items-center gap-2 font-black shadow-[0_8px_20px_rgba(79,131,255,0.24)]"
                onClick={sendMessage}
                disabled={!input.trim() || !!clarify}
              >
                <Send className="w-4 h-4" />
                生成 PPT
              </button>
            )}
          </div>
        </div>
        <div className="text-center mt-2 md:mt-3 text-[11px] md:text-[12px] text-slate-500">
          内容由 AI 生成，请仔细甄别。
        </div>
      </div>
      </div>
    </div>
  );
}
