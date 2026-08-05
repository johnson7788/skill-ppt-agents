import {Fragment, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {A2uiSurface, MarkdownContext, ReactComponentImplementation} from '@a2ui/react/v0_9';
import {MessageProcessor, SurfaceModel, A2uiMessage} from '@a2ui/web_core/v0_9';
import {renderMarkdown} from '@a2ui/markdown-it';
import {evidenceCatalog} from './catalog';
import './index.css';

// 空态示例问题（点击即发送）：覆盖循证/自测问卷/追问等模式，方便用户快速上手。
const EXAMPLES = [
  '一直打喷嚏，可以吃氯雷他定吗？',
  '孕妇能吃氯雷他定吗？',
  '测测我最近是不是抑郁了',
  '布洛芬和对乙酰氨基酚有什么区别？',
];

// 官方 A2A 传输：SSE 每帧 = A2A Part[]（{kind:'text',text} | {kind:'data',data:<A2UI消息>}）。
// text part 带 metadata.thinking=true 时是模型的思考链，渲染到独立的「思考」气泡。
interface Part {
  kind: 'data' | 'text' | 'error';
  data?: A2uiMessage;
  text?: string;
  metadata?: {thinking?: boolean};
}

// 一轮对话：用户问 + 思考链 + AI 引导语 + 该轮渲染出的卡片（surfaceId 由后端生成，前端记录后按它渲染）。
interface Turn {
  q: string;
  thinking: string;
  intro: string;
  surfaceIds: string[];
}

export function App() {
  // 会话内固定 contextId → A2A message.contextId → 后端 InMemorySession 复用 = 真·多轮状态。
  const contextId = useMemo(() => `ctx-${crypto.randomUUID()}`, []);
  // action 回传用：MessageProcessor 回调里拿最新 send（避免闭包旧值）。
  const sendRef = useRef<((body: object) => Promise<void>) | null>(null);
  const processor = useMemo(
    () =>
      new MessageProcessor([evidenceCatalog], action => {
        sendRef.current?.({contextId, action});
      }),
    [contextId],
  );
  const [surfaces, setSurfaces] = useState<SurfaceModel<ReactComponentImplementation>[]>([]);
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const s1 = processor.onSurfaceCreated(s => setSurfaces(prev => [...prev, s]));
    const s2 = processor.onSurfaceDeleted(id => setSurfaces(prev => prev.filter(s => s.id !== id)));
    return () => {
      s1.unsubscribe();
      s2.unsubscribe();
    };
  }, [processor]);

  // 底层发送：body = {contextId, text} 或 {contextId, action}。q 为空表示卡片 action（不新建用户气泡）。
  const send = useCallback(
    async (body: object, q?: string) => {
      setLoading(true);
      setTurns(prev => [...prev, {q: q ?? '', thinking: '', intro: '', surfaceIds: []}]);
      const patchLast = (upd: (t: Turn) => Partial<Turn>) =>
        setTurns(prev => {
          const c = [...prev];
          c[c.length - 1] = {...c[c.length - 1], ...upd(c[c.length - 1])};
          return c;
        });
      // 后端按 token 增量逐帧发 text part → 本轮原文累加后整体渲染 markdown（单气泡，不碎片）。
      // 思考链走 metadata.thinking 通道，单独累进到 thinking（不污染引导语气泡）。
      let introText = '';
      let thinkingText = '';
      try {
        const res = await fetch('/a2a', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
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
            const parts = JSON.parse(raw.slice(5).trim()) as Part[];
            for (const p of parts) {
              if (p.kind === 'error' && p.text) {
                patchLast(() => ({intro: `出错了：${p.text}`}));
              } else if (p.kind === 'text' && p.text) {
                if (p.metadata?.thinking) {
                  thinkingText += p.text;
                  patchLast(() => ({thinking: thinkingText}));
                } else {
                  introText += p.text;
                  const html = await renderMarkdown(introText);
                  patchLast(() => ({intro: html}));
                }
              } else if (p.kind === 'data' && p.data) {
                const sid = (p.data as {createSurface?: {surfaceId: string}}).createSurface?.surfaceId;
                if (sid) patchLast(t => ({surfaceIds: [...t.surfaceIds, sid]}));
                processor.processMessages([p.data]);
              }
            }
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
  sendRef.current = (body: object) => send(body);

  const ask = useCallback((q: string) => send({contextId, text: q}, q), [send, contextId]);

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
          {turns.length === 0 && (
            <div className="empty">
              <div className="empty-title">你好，我是小团健康管家</div>
              <div className="empty-sub">循证支持 · 点下面的问题快速开始</div>
              <div className="empty-examples">
                {EXAMPLES.map(q => (
                  <button
                    className="example-chip"
                    key={q}
                    disabled={loading}
                    onClick={() => ask(q)}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <Fragment key={i}>
              {t.q && <div className="bubble user">{t.q}</div>}
              {t.thinking && (
                <details className="thinking" open={loading}>
                  <summary className="thinking-head">
                    <span className="thinking-dot" />
                    <span>思考过程</span>
                  </summary>
                  <div className="thinking-body">{t.thinking}</div>
                </details>
              )}
              {t.intro && <div className="bubble ai" dangerouslySetInnerHTML={{__html: t.intro}} />}
              {surfaces
                .filter(s => t.surfaceIds.includes(s.id))
                .map(s => (
                  <div className="card-wrap" key={s.id}>
                    <A2uiSurface surface={s} />
                  </div>
                ))}
            </Fragment>
          ))}
          {(() => {
            // 首帧未到前显示「思考中…」；一旦有流式思考/文本/卡片到达就撤掉，避免与内容并存
            const t = turns[turns.length - 1];
            const waitingFirstFrame =
              loading && (!t || (!t.thinking && !t.intro && t.surfaceIds.length === 0));
            return waitingFirstFrame ? <div className="bubble ai loading-dots">思考中…</div> : null;
          })()}
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
          <input value={question} onChange={e => setQuestion(e.target.value)} placeholder="" />
          <button type="submit" disabled={loading}>
            发送
          </button>
        </form>
      </div>
    </MarkdownContext.Provider>
  );
}
