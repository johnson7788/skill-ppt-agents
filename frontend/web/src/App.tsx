import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {A2uiSurface, MarkdownContext, ReactComponentImplementation} from '@a2ui/react/v0_9';
import {MessageProcessor, SurfaceModel, A2uiMessage} from '@a2ui/web_core/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';
import {evidenceCatalog} from './catalog';
import './index.css';

const DEFAULT_Q = '一直打喷嚏，可以吃氯雷他定吗？';

interface Part {
  kind: 'data' | 'text' | 'error' | 'status' | 'thinking' | 'done';
  data?: A2uiMessage;
  text?: string;
  delta?: string;
}

function ThinkingBubble({thinking, steps, done}: {thinking: string; steps: string[]; done: boolean}) {
  const [open, setOpen] = useState(false);
  if (!thinking && steps.length === 0) return null;
  const collapsed = done && !open;
  return (
    <div className={`thinking${collapsed ? ' collapsed' : ''}`}>
      <div className="thinking-head" onClick={() => done && setOpen(o => !o)}>
        {done ? <>已完成思考 {open ? '▾' : '▸'}</> : <><span className="thinking-dot" />思考中…</>}
      </div>
      {!collapsed && (
        <>
          <div className="thinking-steps">
            {steps.map((s, i) => (
              <div className="thinking-step" key={i}>
                {done || i < steps.length - 1 ? '✓' : '•'} {s}
              </div>
            ))}
          </div>
          {thinking && <div className="thinking-body">{thinking}</div>}
        </>
      )}
    </div>
  );
}

export function App() {
  const processor = useMemo(
    () => new MessageProcessor([evidenceCatalog], action => console.log('action:', action)),
    [],
  );
  const [surfaces, setSurfaces] = useState<SurfaceModel<ReactComponentImplementation>[]>([]);
  const [question, setQuestion] = useState(DEFAULT_Q);
  const [history, setHistory] = useState<string[]>([]);
  const [intro, setIntro] = useState('');
  const [loading, setLoading] = useState(false);
  const [thinking, setThinking] = useState('');
  const [steps, setSteps] = useState<string[]>([]);
  const [thinkingDone, setThinkingDone] = useState(false);
  // 每次加载生成新会话 id；后端据此区分首轮(建卡)与追问(增量更新同一张卡)
  const sessionId = useMemo(() => Math.random().toString(36).slice(2), []);

  useEffect(() => {
    const s1 = processor.onSurfaceCreated(s => setSurfaces(prev => [...prev, s]));
    const s2 = processor.onSurfaceDeleted(id => setSurfaces(prev => prev.filter(s => s.id !== id)));
    return () => {
      s1.unsubscribe();
      s2.unsubscribe();
    };
  }, [processor]);

  const ask = useCallback(
    async (q: string) => {
      setLoading(true);
      setIntro('');
      setThinking('');
      setSteps([]);
      setThinkingDone(false);
      setHistory(prev => [...prev, q]);
      // 不删 surface：首轮由后端 createSurface 建卡，追问只发差异 updateComponents
      // → MessageProcessor 按组件 id 原地合并，卡片增量更新、不整块重画。
      // 读 SSE 流：fetch + ReadableStream reader（POST 不能用原生 EventSource），
      // 按 \n\n 切事件、data: 前缀去掉后 JSON.parse，按 kind 分发。
      try {
        const res = await fetch('/a2a', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: q, session_id: sessionId}),
        });
        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        for (;;) {
          const {done, value} = await reader.read();
          if (done) break;
          buf += decoder.decode(value, {stream: true});
          let idx: number;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const raw = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (!raw.startsWith('data:')) continue;
            const evt = JSON.parse(raw.slice(5).trim()) as Part;
            if (evt.kind === 'status' && evt.text) setSteps(prev => [...prev, evt.text!]);
            else if (evt.kind === 'thinking' && evt.delta) setThinking(prev => prev + evt.delta);
            else if (evt.kind === 'text' && evt.text) setIntro(await renderMarkdown(evt.text));
            else if (evt.kind === 'data' && evt.data) {
              setThinkingDone(true); // 卡片首条 data 到达 → 折叠思考气泡
              processor.processMessages([evt.data]);
            } else if (evt.kind === 'error' && evt.text) setIntro(evt.text);
          }
        }
      } catch (e) {
        setIntro(`出错了：${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setLoading(false);
      }
    },
    [processor, sessionId],
  );

  // StrictMode 会双跑 effect；ref 保证初始加载只发一次，避免 createSurface 撞 id
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    ask(DEFAULT_Q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <MarkdownContext.Provider value={renderMarkdown}>
      <div className="app">
        <header className="topbar">
          <span className="back">‹</span>
          <span className="avatar" />
          <div>
            <div className="title">小团健康管家</div>
            <div className="tags">
              <span className="tag tag-doc">AI 医生</span>
              <span className="tag tag-ev">循证支持</span>
            </div>
          </div>
          <span className="more">⋯</span>
        </header>

        <main className="chat">
          {history.map((q, i) => (
            <div className="bubble user" key={i}>
              {q}
            </div>
          ))}
          {(thinking || steps.length > 0) && (
            <ThinkingBubble thinking={thinking} steps={steps} done={thinkingDone} />
          )}
          {intro && <div className="bubble ai" dangerouslySetInnerHTML={{__html: intro}} />}
          {surfaces.map(s => (
            <div className="card-wrap" key={s.id}>
              <A2uiSurface surface={s} />
            </div>
          ))}
        </main>

        <form
          className="composer"
          onSubmit={e => {
            e.preventDefault();
            if (question.trim()) ask(question.trim());
          }}
        >
          <input value={question} onChange={e => setQuestion(e.target.value)} placeholder="输入你的健康问题…" />
          <button type="submit" disabled={loading}>
            发送
          </button>
        </form>
      </div>
    </MarkdownContext.Provider>
  );
}
