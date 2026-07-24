import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Sparkles, PanelRightClose, PanelRightOpen, User } from 'lucide-react';
import App from './App.tsx';
import Workspace from './Workspace.tsx';
import './index.css';

function Shell() {
  const [userId, setUserId] = useState('default_user');
  const [showAssistant, setShowAssistant] = useState(true);
  const [openFile, setOpenFile] = useState<{ path: string; name: string } | null>(null);
  // 助手覆盖保存白板后 +1，触发工作台重挂编辑器拉最新内容
  const [reloadNonce, setReloadNonce] = useState(0);

  return (
    <div className="flex flex-col h-screen bg-[#0a0e1a]">
      {/* 统一顶栏：logo + 产品名 · 助手开关 + 用户 */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800 bg-[#0d1220] flex-shrink-0">
        <Sparkles size={18} className="text-blue-400" />
        <span className="text-[15px] font-semibold text-slate-100">文档生成智能体</span>
        <div className="ml-auto flex items-center gap-3">
          <button
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[13px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            onClick={() => setShowAssistant((s) => !s)}
            title={showAssistant ? '隐藏助手' : '显示助手'}
          >
            {showAssistant ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
            助手
          </button>
          <div className="flex items-center gap-1.5 text-[12px] text-slate-500">
            <User size={14} />
            <input
              className="bg-slate-800/60 border border-slate-700 rounded px-2 py-0.5 text-slate-300 w-28 outline-none focus:border-blue-500"
              value={userId}
              onChange={(e) => setUserId(e.target.value || 'default_user')}
            />
          </div>
        </div>
      </div>

      {/* 三栏：文件树+编辑器(Workspace) | 助手对话侧栏(App) */}
      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0">
          <Workspace userId={userId} onOpenFile={setOpenFile} reloadNonce={reloadNonce} />
        </div>
        {showAssistant && (
          <aside className="w-[420px] flex-shrink-0 border-l border-slate-800 min-h-0">
            <App
              userId={userId}
              hideHeader
              openFile={openFile}
              onDocChanged={() => setReloadNonce((n) => n + 1)}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Shell />
  </StrictMode>,
);
