import { StrictMode, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MessagesSquare, FolderKanban } from 'lucide-react';
import App from './App.tsx';
import Workspace from './Workspace.tsx';
import './index.css';

function Shell() {
  const [view, setView] = useState<'chat' | 'workspace'>('chat');
  const [userId, setUserId] = useState('default_user');

  return (
    <div className="flex flex-col h-screen bg-[#0a0e1a]">
      <div className="flex items-center gap-1 px-3 py-2 border-b border-slate-800 bg-[#0d1220] flex-shrink-0">
        <button
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] ${
            view === 'chat' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-800'
          }`}
          onClick={() => setView('chat')}
        >
          <MessagesSquare size={15} /> 对话
        </button>
        <button
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] ${
            view === 'workspace' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-800'
          }`}
          onClick={() => setView('workspace')}
        >
          <FolderKanban size={15} /> 工作台
        </button>
        <div className="ml-auto flex items-center gap-2 text-[12px] text-slate-500">
          <span>用户</span>
          <input
            className="bg-slate-800/60 border border-slate-700 rounded px-2 py-0.5 text-slate-300 w-32 outline-none focus:border-blue-500"
            value={userId}
            onChange={(e) => setUserId(e.target.value || 'default_user')}
          />
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {view === 'chat' ? <App userId={userId} /> : <Workspace userId={userId} />}
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Shell />
  </StrictMode>,
);
