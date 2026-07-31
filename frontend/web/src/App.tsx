import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {A2uiSurface, MarkdownContext, ReactComponentImplementation} from '@a2ui/react/v0_9';
import {MessageProcessor, SurfaceModel, A2uiMessage} from '@a2ui/web_core/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';
import {evidenceCatalog} from './catalog';
import './index.css';

const DEFAULT_Q = '一直打喷嚏，可以吃氯雷他定吗？';

interface Part {
  kind: 'data' | 'text' | 'error';
  data?: A2uiMessage;
  text?: string;
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
      setHistory(prev => [...prev, q]);
      // 不删 surface：首轮由后端 createSurface 建卡，追问只发差异 updateComponents
      // → MessageProcessor 按组件 id 原地合并，卡片增量更新、不整块重画。
      try {
        const res = await fetch('/a2a', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({question: q, session_id: sessionId}),
        });
        const parts = (await res.json()) as Part[];
        const dataMsgs: A2uiMessage[] = [];
        for (const p of parts) {
          if (p.kind === 'text' && p.text) setIntro(p.text);
          if (p.kind === 'data' && p.data) dataMsgs.push(p.data);
        }
        processor.processMessages(dataMsgs);
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
          <span className="avatar" />
          <div>
            <div className="title">小团健康管家</div>
            <div className="tags">
              <span className="tag tag-doc">AI 医生</span>
              <span className="tag tag-ev">循证支持</span>
            </div>
          </div>
        </header>

        <main className="chat">
          {history.map((q, i) => (
            <div className="bubble user" key={i}>
              {q}
            </div>
          ))}
          {intro && <div className="bubble ai">{intro}</div>}
          {loading && <div className="bubble ai">循证医学引擎分析中…</div>}
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
