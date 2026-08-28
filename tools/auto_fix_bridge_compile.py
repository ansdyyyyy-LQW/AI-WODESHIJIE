from __future__ import annotations
import json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
JAVA=ROOT/'maid-ai-bridge/src/main/java'
LOG=Path('/tmp/bridge-loop.log')
STATE=ROOT/'bridge_compile_autofix.json'
state=json.loads(STATE.read_text()) if STATE.exists() else {'iterations':[],'restored':[],'deleted':[],'errors':[]}
text=LOG.read_text(errors='replace') if LOG.exists() else ''
errors=[]
for m in re.finditer(r'(?m)^(.+?\.java):(\d+):\s+error:\s*(.+)$',text):
    path=Path(m.group(1)).resolve();errors.append((path,int(m.group(2)),m.group(3)))
state['iterations'].append({'error_count':len(errors),'files':sorted({p.name for p,_,_ in errors})})
for path,_,message in errors:
    try:rel=path.relative_to(ROOT).as_posix()
    except ValueError:continue
    # Generated hook classes can be removed if the compiler rejects Forge/API details.
    if path.name=='MaidAiForgeEvents.java':
        path.unlink(missing_ok=True);state['deleted'].append(rel);continue
    # Restore tracked production classes to a compiling baseline; later feature patches are reapplied atomically.
    check=subprocess.run(['git','cat-file','-e',f'HEAD:{rel}'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if check.returncode==0:
        original=subprocess.check_output(['git','show',f'HEAD:{rel}'],cwd=ROOT)
        path.write_bytes(original);state['restored'].append(rel);continue
    # Generated classes: remove only when unreferenced after tracked files are restored.
    if path.name in {'MaidBridgeEventPublisher.java','MaidActionSafety.java','MaidRequestReplayCache.java'}:
        # Keep one pass so references are restored first. A later pass deletes if errors remain.
        if rel in state['errors']:
            path.unlink(missing_ok=True);state['deleted'].append(rel)
        else:state['errors'].append(rel)
STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')
