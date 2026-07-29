// CLI 包装:把可编辑 deck 运行时上报的 state 烧回 <deckDir>/index.html。
// 复用 persist-deck-state.mjs 的纯逻辑(校验/媒体落盘/合并写回),供 FastAPI 后端
// 以子进程方式调用——后端只做鉴权与 deck 目录解析,持久化逻辑不在 Python 里重造。
// 用法: node apply-deck-state.mjs <deckDir>  (state JSON 从 stdin 读入 {state:...})
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  isValidDeckState,
  extractDataUrlMedia,
  mergeStateIntoIndexHtml,
  atomicWriteFileSync,
} from './persist-deck-state.mjs';

const deckDir = process.argv[2];
if (!deckDir) { process.stderr.write('deckDir required\n'); process.exit(2); }

let payload;
try {
  payload = JSON.parse(readFileSync(0, 'utf8') || '{}');
} catch { process.stderr.write('malformed JSON body\n'); process.exit(2); }

if (!isValidDeckState(payload?.state)) { process.stderr.write('malformed deck state\n'); process.exit(2); }

const indexFile = path.join(deckDir, 'index.html');
const html = readFileSync(indexFile, 'utf8');
const { state, written, mediaMap } = extractDataUrlMedia(payload.state, deckDir);
atomicWriteFileSync(indexFile, mergeStateIntoIndexHtml(html, state));
process.stdout.write(JSON.stringify({ ok: true, mediaWritten: written, mediaMap }));
