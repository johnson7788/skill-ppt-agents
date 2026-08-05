const API_BASE = '';

/**
 * SSE 事件类型
 */
export interface SSEEvent {
  type: 'text' | 'thought' | 'tool_step' | 'tool_call' | 'narrator_card' | 'clarify' | 'done';
  [key: string]: unknown;
}

/**
 * 解析 fetch 响应体（SSE 流），yield 每条 data 的 JSON 事件
 */
async function* parseSSE(response: Response): AsyncGenerator<SSEEvent> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE 消息以双换行分隔
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';

    for (const part of parts) {
      const lines = part.split('\n');
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const jsonStr = line.slice(6);
          try {
            yield JSON.parse(jsonStr) as SSEEvent;
          } catch {
            // 跳过格式错误的 JSON
          }
        }
      }
    }
  }
}

/**
 * 调用 SSE 流式端点，yield 解析后的 JSON 事件
 */
export async function* streamChat(
  message: string,
  userId: string = 'default_user',
  signal?: AbortSignal,
  example = false,
): AsyncGenerator<SSEEvent> {
  // example=1 时后端用「原始问题文本」做缓存 key（不含累积的上传/生成文件），保证示例点击稳定命中缓存
  const url = `${API_BASE}/chat/stream?message=${encodeURIComponent(message)}&user_id=${encodeURIComponent(userId)}${example ? '&example=1' : ''}`;
  const response = await fetch(url, {
    headers: { Accept: 'text/event-stream' },
    signal,
  });
  yield* parseSSE(response);
}

/**
 * 回答 clarify 澄清提问，续接同一 session 的流式输出
 */
export async function* answerChat(
  sessionId: string,
  callId: string,
  answer: string,
  userId: string = 'default_user',
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ session_id: sessionId, call_id: callId, answer, user_id: userId }),
    signal,
  });
  yield* parseSSE(response);
}

/**
 * 调用非流式 POST /chat 端点
 */
export async function postChat(message: string, userId: string = 'default_user') {
  const url = `${API_BASE}/chat`;
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, user_id: userId }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }

  return response.json();
}

/**
 * 上传文件到后端
 */
export async function uploadFile(file: File, userId: string = 'default_user') {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('user_id', userId);

  const response = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }

  return response.json();
}

/**
 * 列出用户已上传的文件
 */
export async function listUploads(userId: string = 'default_user') {
  const response = await fetch(`${API_BASE}/uploads?user_id=${encodeURIComponent(userId)}`);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * 用户文件（uploads/<user_id> 里的一个文件）。preview_path 非空时可在线预览。
 */
export interface Deck {
  name: string;
  preview_path: string | null;
  size: number;
  modified: number;
}

/**
 * 列出用户所有文件（uploads/<user_id>），含可选预览路径
 */
export async function listDecks(userId: string = 'default_user'): Promise<{ decks: Deck[] }> {
  const response = await fetch(`${API_BASE}/decks?user_id=${encodeURIComponent(userId)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/**
 * 删除一个用户文件（连同其匹配的生成 deck 目录）
 */
export async function deleteDeck(name: string, userId: string = 'default_user') {
  const response = await fetch(
    `${API_BASE}/decks?name=${encodeURIComponent(name)}&user_id=${encodeURIComponent(userId)}`,
    { method: 'DELETE' },
  );
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

/**
 * 删除用户文件：传 fileName 删单个，否则清空全部
 */
export async function clearUploads(userId: string = 'default_user', fileName?: string) {
  const url = fileName
    ? `${API_BASE}/uploads?user_id=${encodeURIComponent(userId)}&file=${encodeURIComponent(fileName)}`
    : `${API_BASE}/uploads?user_id=${encodeURIComponent(userId)}`;
  const response = await fetch(url, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}
