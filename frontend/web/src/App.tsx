import {Fragment, useCallback, useEffect, useMemo, useRef, useState} from 'react';
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

// 一轮对话：用户问 + 该轮的思考/引导语/循证卡（各轮独立，卡片用唯一 surfaceId）
interface Turn {
  q: string;
  intro: string;
  thinking: string;
  steps: string[];
  done: boolean;
  surfaceId: string;
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
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  // 用 ref 读最新 turns（ask 闭包里避免拿到旧值），供组装追问上下文 history
  const turnsRef = useRef<Turn[]>([]);
  turnsRef.current = turns;

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
      // 每轮独立卡片：唯一 surfaceId，后端每轮 createSurface + 全量组件（不再共用一张卡增量 diff）
      const surfaceId = `card-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
      const history = turnsRef.current.map(t => t.q); // 此前的问题，供 LLM 连续上下文
      setTurns(prev => [...prev, {q, intro: '', thinking: '', steps: [], done: false, surfaceId}]);
      // 只改「最后一轮」（即刚追加的这轮）
      const patchLast = (upd: (t: Turn) => Partial<Turn>) =>
        setTurns(prev => {
          const c = [...prev];
          const last = c[c.length - 1];
          c[c.length - 1] = {...last, ...upd(last)};
          return c;
        });
      try {
        const res = await fetch('/a2a', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: q, surface_id: surfaceId, history}),
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
            if (evt.kind === 'status' && evt.text) patchLast(t => ({steps: [...t.steps, evt.text!]}));
            else if (evt.kind === 'thinking' && evt.delta)
              patchLast(t => ({thinking: t.thinking + evt.delta}));
            else if (evt.kind === 'text' && evt.text) {
              const html = await renderMarkdown(evt.text);
              patchLast(() => ({intro: html}));
            } else if (evt.kind === 'data' && evt.data) {
              patchLast(() => ({done: true})); // 卡片首条 data 到达 → 折叠思考气泡
              processor.processMessages([evt.data]);
            } else if (evt.kind === 'error' && evt.text) patchLast(() => ({intro: evt.text}));
          }
        }
      } catch (e) {
        patchLast(() => ({intro: `出错了：${e instanceof Error ? e.message : String(e)}`}));
      } finally {
        setLoading(false);
      }
    },
    [processor],
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
          {turns.map((t, i) => (
            <Fragment key={i}>
              <div className="bubble user">{t.q}</div>
              {(t.thinking || t.steps.length > 0) && (
                <ThinkingBubble thinking={t.thinking} steps={t.steps} done={t.done} />
              )}
              {t.intro && <div className="bubble ai" dangerouslySetInnerHTML={{__html: t.intro}} />}
              {surfaces
                .filter(s => s.id === t.surfaceId)
                .map(s => (
                  <div className="card-wrap" key={s.id}>
                    <A2uiSurface surface={s} />
                  </div>
                ))}
            </Fragment>
          ))}
        </main>

        <form
          className="composer"
          onSubmit={e => {
            e.preventDefault();
            if (question.trim() && !loading) {
              ask(question.trim());
              setQuestion('');
            }
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
