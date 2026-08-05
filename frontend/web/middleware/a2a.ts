// Vite dev-server 中间件：浏览器 POST /a2a → 官方 A2A 协议转发到循证 A2UI 智能体(:8700)。
//
// 请求体固定 JSON：{contextId, text?} 或 {contextId, action?}。
// contextId 由前端每次会话固定生成 → 作为 A2A message.contextId → 后端按它复用
// InMemorySession = 真·多轮状态。text→文本 Part；action→A2UI DataPart（卡片回传）。
// 上游 SSE 的 status-update/message parts 原样 `data: <Part[]>` 透传给浏览器。
import {IncomingMessage, ServerResponse} from 'http';
import {randomUUID} from 'crypto';
import {Plugin, ViteDevServer} from 'vite';
import {A2AClient} from '@a2a-js/sdk/client';
import type {MessageSendParams, Part} from '@a2a-js/sdk';

const A2UI_MIME_TYPE = 'application/a2ui+json';
const CARD_URL = 'http://localhost:8700/.well-known/agent-card.json';

const fetchWithExt: typeof fetch = (url, init) => {
  const headers = new Headers(init?.headers);
  headers.set('X-A2A-Extensions', 'https://a2ui.org/a2a-extension/a2ui/v0.9');
  return fetch(url, {...init, headers});
};

let client: A2AClient | null = null;
const getClient = async () => {
  if (!client) client = await A2AClient.fromCardUrl(CARD_URL, {fetchImpl: fetchWithExt});
  return client;
};

export const plugin = (): Plugin => ({
  name: 'a2a-handler',
  configureServer(server: ViteDevServer) {
    server.middlewares.use('/a2a', (req: IncomingMessage, res: ServerResponse, next) => {
      if (req.method !== 'POST') return next();
      let body = '';
      const MAX = 1024 * 1024;
      req.on('data', c => {
        body += c;
        if (body.length > MAX) {
          res.statusCode = 413;
          res.end('{"error":"payload too large"}');
          req.destroy();
        }
      });
      req.on('end', async () => {
        if (res.writableEnded) return;
        let parts: Part[];
        let contextId: string | undefined;
        try {
          const b = JSON.parse(body) as {contextId?: string; text?: string; action?: unknown};
          contextId = b.contextId;
          parts = b.action
            ? [{kind: 'data', data: {version: 'v0.9', action: b.action}, mimeType: A2UI_MIME_TYPE} as Part]
            : [{kind: 'text', text: String(b.text ?? '')}];
        } catch {
          res.statusCode = 400;
          res.end('{"error":"invalid body"}');
          return;
        }
        const sendParams: MessageSendParams = {
          message: {messageId: randomUUID(), role: 'user', kind: 'message', parts, contextId},
        };
        try {
          const stream = await (await getClient()).sendMessageStream(sendParams);
          res.statusCode = 200;
          res.setHeader('Content-Type', 'text/event-stream');
          res.setHeader('Cache-Control', 'no-cache');
          res.flushHeaders(); // 立刻冲刷响应头，避免首帧在中间件被攒着
          for await (const chunk of stream) {
            if (res.destroyed) break;
            if (chunk.kind === 'status-update' && chunk.status.message?.parts) {
              res.write(`data: ${JSON.stringify(chunk.status.message.parts)}\n\n`);
            } else if (chunk.kind === 'message' && chunk.parts) {
              res.write(`data: ${JSON.stringify(chunk.parts)}\n\n`);
            }
          }
          res.end();
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          if (!res.headersSent) {
            res.statusCode = 500;
            res.end(JSON.stringify({error: msg}));
          } else {
            res.write(`data: ${JSON.stringify([{kind: 'error', text: msg}])}\n\n`);
            res.end();
          }
        }
      });
    });
  },
});
