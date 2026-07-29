import { execFileSync } from 'node:child_process';
import path from 'node:path';

/**
 * Generate a 2-page Chinese-named deck before the suite runs.
 *
 * This exercises the real dashi pipeline (scaffold -> render -> pptx) and,
 * crucially, the `_slugify_segment` fix: the Chinese title "量子计算前沿"
 * must land on the deterministic ASCII dir `output/deck-82f50188/`, so the
 * preview_url / download_url the agent hands the user are copy-safe ASCII.
 * If the slug logic ever regresses (Chinese leaks into the path), the deck
 * lands elsewhere and every serving assertion below 404s.
 */
export default function globalSetup() {
  // playwright runs from the frontend dir; backend is its sibling
  const backend = path.resolve(process.cwd(), '../backend');
  const py = `
import asyncio, sys
sys.path.insert(0, '.')
from app.dashi_tools import dashi_scaffold, dashi_render
async def main():
    s = await dashi_scaffold(
        title='量子计算前沿', goal='介绍量子计算核心概念',
        theme='theme03', pages=2,
        layouts=['theme03_page002', 'theme03_page009'],
    )
    assert not s.get('error'), s.get('error')
    assert s['scaffold_path'] == 'output/deck-82f50188/goal.json', s['scaffold_path']
    r = await dashi_render(goal_path=s['scaffold_path'], export_pptx=True)
    assert not r.get('error'), r.get('error')
    print(r['preview_url'])
    print(r['download_url'])
    print(r['workspace_path'])
asyncio.run(main())
`;
  const out = execFileSync('python3', ['-c', py], {
    cwd: backend,
    encoding: 'utf-8',
    timeout: 300_000,
  });
  console.log('[global-setup] deck generated:\n' + out.trim());
}
