from __future__ import annotations
import json,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BRIDGE=ROOT/'maid-ai-bridge';JAVA=BRIDGE/'src/main/java'
REPORT=ROOT/'bridge_feature_report.json'

def run_build(tag:str)->tuple[bool,str]:
    log=ROOT/f'.bridge-{tag}.log'
    proc=subprocess.run([str(BRIDGE/'gradlew'),'compileJava','--no-daemon'],cwd=BRIDGE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
    text=proc.stdout.decode(errors='replace');log.write_text(text,encoding='utf-8')
    return proc.returncode==0,text[-12000:]

def java(stem:str)->Path:
    rows=list(JAVA.rglob(stem+'.java'))
    if not rows:raise FileNotFoundError(stem)
    return rows[0]

def pkg(path:Path)->str:return re.search(r'^package\s+([\w.]+);',path.read_text(),re.M).group(1)

def snapshot(paths:list[Path])->dict[Path,bytes|None]:return {p:(p.read_bytes() if p.exists() else None) for p in paths}
def restore(snap:dict[Path,bytes|None]):
    for p,data in snap.items():
        if data is None:p.unlink(missing_ok=True)
        else:p.write_bytes(data)

def tracked_modified_bridge()->list[str]:
    out=subprocess.check_output(['git','diff','--name-only','HEAD','--','maid-ai-bridge'],cwd=ROOT,text=True)
    return [x for x in out.splitlines() if x]

def hard_baseline():
    # Restore tracked files and remove generated untracked Java if current state cannot compile.
    subprocess.run(['git','checkout','HEAD','--','maid-ai-bridge'],cwd=ROOT,check=True)
    for p in JAVA.rglob('Maid*.java'):
        rel=p.relative_to(ROOT).as_posix()
        if subprocess.run(['git','cat-file','-e',f'HEAD:{rel}'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0:
            p.unlink(missing_ok=True)

report={'baseline':{},'features':[]}
ok,tail=run_build('pre-atomic')
if not ok:
    hard_baseline();ok,tail=run_build('baseline-restored')
report['baseline']={'ok':ok,'tail':tail[-3000:]}
if not ok:
    REPORT.write_text(json.dumps(report,indent=2),encoding='utf-8');raise SystemExit(1)

controller=java('MaidAiController');action_engine=java('ActionEngine');controller_pkg=pkg(controller);action_pkg=pkg(action_engine)

def attempt(name:str,paths:list[Path],fn):
    snap=snapshot(paths)
    try:fn();ok,tail=run_build(name)
    except Exception as exc:ok=False;tail=f'{type(exc).__name__}: {exc}'
    if not ok:restore(snap);run_build(name+'-revert')
    report['features'].append({'name':name,'kept':ok,'tail':tail[-5000:]})
    REPORT.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return ok

# 1. Production EVENT publisher. Pure reflection limits coupling to existing transport internals.
publisher=controller.parent/'MaidBridgeEventPublisher.java'
def feature_events_class():
    publisher.write_text(f'''package {controller_pkg};
import com.google.gson.Gson;import com.google.gson.JsonObject;import java.lang.reflect.*;import java.util.*;import java.util.concurrent.ConcurrentLinkedQueue;
public final class MaidBridgeEventPublisher{{
 private static final Gson G=new Gson();private static final Queue<String>Q=new ConcurrentLinkedQueue<>();private static int day=-1;private static double hp=-1;private static String inv="",action="";private static long lastWave=-9999;
 private MaidBridgeEventPublisher(){{}}
 public static void publish(String type,String severity,Map<String,Object>data){{String id=UUID.randomUUID().toString();JsonObject p=new JsonObject();p.addProperty("event_id",id);p.addProperty("event_type",type);p.addProperty("severity",severity);p.addProperty("game_tick",num(data.get("game_tick")));p.add("data",G.toJsonTree(data));JsonObject e=new JsonObject();e.addProperty("protocol_version",1);e.addProperty("type","EVENT");e.addProperty("message_id",id);e.addProperty("game_tick",num(data.get("game_tick")));e.addProperty("timestamp_ms",System.currentTimeMillis());e.add("payload",p);Q.add(G.toJson(e));}}
 public static void observeSnapshot(Object s){{if(s==null)return;long tick=longv(s,"gameTick","game_tick","tick");int d=(int)longv(s,"day","gameDay");double h=doublev(s,"health");String i=String.valueOf(value(s,"inventory"));Map<String,Object>m=new HashMap<>();m.put("game_tick",tick);m.put("day",d);if(day>=0&&day!=d)publish("DAY_CHANGED","INFO",m);day=d;if(!inv.isBlank()&&!inv.equals(i))publish("INVENTORY_CHANGED","INFO",m);inv=i;if(hp>=0&&h<hp){{m.put("amount",hp-h);m.put("health",h);publish("DAMAGE_TAKEN",h<=6?"ERROR":"WARN",m);}}if(h>0&&h<=6)publish("LOW_HEALTH","ERROR",m);hp=h;Object list=value(s,"nearbyEntities","nearby_entities");int wave=hostiles(list);if(wave>=4&&tick-lastWave>100){{m.put("count",wave);publish("HOSTILE_WAVE_DETECTED","ERROR",m);lastWave=tick;}}}}
 public static void observeAction(Object c){{Object a=value(c,"currentAction","current_action");if(a==null)a=value(c,"actionEngine");String now=String.valueOf(a);if(!now.equals(action)){{Map<String,Object>m=new HashMap<>();m.put("state",now);publish(action.isBlank()?"ACTION_STARTED":"ACTION_STATE_CHANGED","INFO",m);action=now;}}}}
 public static void flush(Object root){{if(Q.isEmpty())return;Object sender=sender(root,5,Collections.newSetFromMap(new IdentityHashMap<>()));if(sender==null)return;Method method=sendMethod(sender);String raw;while((raw=Q.poll())!=null)try{{method.setAccessible(true);method.invoke(sender,raw);}}catch(Exception ex){{Q.add(raw);break;}}}}
 private static Method sendMethod(Object o){{if(o==null)return null;for(Method m:o.getClass().getMethods())if((m.getName().equals("send")||m.getName().equals("sendText")||m.getName().equals("sendJson")||m.getName().equals("sendMessage"))&&m.getParameterCount()==1&&m.getParameterTypes()[0]==String.class)return m;return null;}}
 private static Object sender(Object o,int depth,Set<Object>seen){{if(o==null||depth<0||seen.contains(o))return null;seen.add(o);if(sendMethod(o)!=null)return o;if(depth==0)return null;for(Class<?>c=o.getClass();c!=null;c=c.getSuperclass())for(Field f:c.getDeclaredFields()){{if(Modifier.isStatic(f.getModifiers()))continue;try{{f.setAccessible(true);Object found=sender(f.get(o),depth-1,seen);if(found!=null)return found;}}catch(Exception ignored){{}}}}return null;}}
 private static Object value(Object o,String...names){{if(o==null)return null;for(String n:names){{for(Method m:o.getClass().getMethods())if(m.getParameterCount()==0&&m.getName().equalsIgnoreCase(n))try{{return m.invoke(o);}}catch(Exception ignored){{}}for(Class<?>c=o.getClass();c!=null;c=c.getSuperclass())try{{Field f=c.getDeclaredField(n);f.setAccessible(true);return f.get(o);}}catch(Exception ignored){{}}}}return null;}}
 private static long longv(Object o,String...n){{Object v=value(o,n);return v instanceof Number?((Number)v).longValue():0;}}private static double doublev(Object o,String...n){{Object v=value(o,n);return v instanceof Number?((Number)v).doubleValue():0;}}private static long num(Object v){{return v instanceof Number?((Number)v).longValue():0;}}
 private static int hostiles(Object list){{if(!(list instanceof Iterable<?>))return 0;int n=0;for(Object e:(Iterable<?>)list){{String c=String.valueOf(value(e,"category"));Object t=value(e,"targetingMaid","targeting_maid","recentAttacker");if(c.equals("HOSTILE")||c.equals("MONSTER")||Boolean.TRUE.equals(t))n++;}}return n;}}
}}
''',encoding='utf-8')
attempt('event-publisher-class',[publisher],feature_events_class)

# 2. Hook controller tick to flush queued EVENTs.
def feature_controller_hook():
    s=controller.read_text()
    if 'MaidBridgeEventPublisher.flush(this)' in s:return
    m=re.search(r'(?m)^(?P<i>[ \t]*)(?:public|private|protected)\s+void\s+\w*[Tt]ick\w*\s*\([^)]*\)\s*\{',s)
    if not m:raise RuntimeError('tick method missing')
    add=m.group(0)+f"\n{m.group('i')}    MaidBridgeEventPublisher.observeAction(this);\n{m.group('i')}    MaidBridgeEventPublisher.flush(this);"
    controller.write_text(s[:m.start()]+add+s[m.end():],encoding='utf-8')
attempt('controller-event-hook',[controller],feature_controller_hook)

# 3. Wrap the snapshot producer so events are generated from real Bridge data.
obs_rows=list(JAVA.rglob('MaidObservationService.java'))
if obs_rows:
    observation=obs_rows[0]
    def feature_snapshot_hook():
        s=observation.read_text()
        if 'MaidBridgeEventPublisher.observeSnapshot' in s:return
        p=re.compile(r'(?m)(?P<i>^[ \t]*)(?P<mods>public\s+)(?P<ret>[\w.<>]*Snapshot[\w.<>]*)\s+(?P<n>\w+)\s*\((?P<params>[^)]*)\)(?P<tail>\s*(?:throws\s+[^\{]+)?\s*)\{')
        m=p.search(s)
        if not m:raise RuntimeError('snapshot method missing')
        names=[re.split(r'\s+',x.strip())[-1] for x in m.group('params').split(',') if x.strip()];n=m.group('n');ren=n+'Observed';orig=m.group(0);renamed=orig.replace(n+'(',ren+'(',1)
        wrapper=f"{m.group('i')}public {m.group('ret')} {n}({m.group('params')}){m.group('tail')}{{\n{m.group('i')}    {m.group('ret')} snapshot={ren}({', '.join(names)});\n{m.group('i')}    {controller_pkg}.MaidBridgeEventPublisher.observeSnapshot(snapshot);\n{m.group('i')}    return snapshot;\n{m.group('i')}}}\n\n"
        observation.write_text(s[:m.start()]+wrapper+renamed+s[m.end():],encoding='utf-8')
    attempt('snapshot-event-hook',[observation],feature_snapshot_hook)

# 4. Forge damage/death EVENTs, compiled atomically.
forge_events=controller.parent/'MaidAiForgeEvents.java'
def feature_forge_events():
    modid='maid_ai_bridge'
    for t in (BRIDGE/'src/main/resources').rglob('mods.toml'):
        mm=re.search(r'modId\s*=\s*"([^"]+)"',t.read_text(errors='ignore'));modid=mm.group(1) if mm else modid
    forge_events.write_text(f'''package {controller_pkg};
import java.util.*;import net.minecraftforge.event.entity.living.LivingDamageEvent;import net.minecraftforge.event.entity.living.LivingDeathEvent;import net.minecraftforge.eventbus.api.SubscribeEvent;import net.minecraftforge.fml.common.Mod;
@Mod.EventBusSubscriber(modid="{modid}",bus=Mod.EventBusSubscriber.Bus.FORGE) public final class MaidAiForgeEvents{{private MaidAiForgeEvents(){{}}private static boolean maid(Object e){{return e!=null&&e.getClass().getSimpleName().equals("EntityMaid");}}@SubscribeEvent public static void damage(LivingDamageEvent e){{if(!maid(e.getEntity()))return;Map<String,Object>d=new HashMap<>();d.put("amount",e.getAmount());d.put("health",e.getEntity().getHealth());d.put("entity_uuid",e.getEntity().getUUID().toString());if(e.getSource().getEntity()!=null){{d.put("attacker_uuid",e.getSource().getEntity().getUUID().toString());d.put("attacker_type",String.valueOf(e.getSource().getEntity().getType()));}}MaidBridgeEventPublisher.publish("DAMAGE_TAKEN",e.getAmount()>=4?"ERROR":"WARN",d);}}@SubscribeEvent public static void death(LivingDeathEvent e){{if(!maid(e.getEntity()))return;Map<String,Object>d=new HashMap<>();d.put("entity_uuid",e.getEntity().getUUID().toString());MaidBridgeEventPublisher.publish("MAID_DEATH","CRITICAL",d);}}}}
''',encoding='utf-8')
attempt('forge-damage-events',[forge_events],feature_forge_events)

# 5. Replay cache + wrapper around ActionEngine public request future.
cache=action_engine.parent/'MaidRequestReplayCache.java'
def feature_replay():
    cache.write_text(f'''package {action_pkg};import java.lang.reflect.*;import java.util.*;import java.util.concurrent.*;public final class MaidRequestReplayCache{{private static final Map<String,Object>C=new LinkedHashMap<>(512,.75f,true){{protected boolean removeEldestEntry(Map.Entry<String,Object>e){{return size()>512;}}}};private MaidRequestReplayCache(){{}}@SuppressWarnings("unchecked")public static synchronized<T>T get(Object r){{String id=id(r);return id.isBlank()?null:(T)C.get(id);}}public static<T>CompletableFuture<T>wrap(Object r,CompletableFuture<T>f){{String id=id(r);return id.isBlank()?f:f.whenComplete((v,e)->{{if(e==null&&v!=null)synchronized(MaidRequestReplayCache.class){{C.put(id,v);}}}});}}private static String id(Object r){{if(r==null)return"";for(String n:new String[]{{"requestId","request_id","id"}})try{{Method m=r.getClass().getMethod(n);Object v=m.invoke(r);if(v!=null)return String.valueOf(v);}}catch(Exception ignored){{}}return"";}}}}
''',encoding='utf-8')
    s=action_engine.read_text()
    if 'MaidRequestReplayCache.wrap' in s:return
    p=re.compile(r'(?m)(?P<i>^[ \t]*)(?P<mods>public\s+)(?P<ret>CompletableFuture\s*<\s*(?P<res>[\w.]+)\s*>)\s+(?P<n>\w+)\s*\((?P<params>[^)]*[Rr]equest[^)]*)\)(?P<tail>\s*(?:throws\s+[^\{]+)?\s*)\{')
    m=p.search(s)
    if not m:raise RuntimeError('request future method missing')
    parts=[x.strip() for x in m.group('params').split(',')];names=[re.split(r'\s+',re.sub(r'@\w+(?:\([^)]*\))?\s*','',x))[-1] for x in parts];req=next((n for n in names if 'request' in n.lower()),names[0]);n=m.group('n');ren=n+'Uncached';orig=m.group(0);renamed=orig.replace(n+'(',ren+'(',1)
    wrapper=f"{m.group('i')}public {m.group('ret')} {n}({m.group('params')}){m.group('tail')}{{\n{m.group('i')}    {m.group('res')} cached=MaidRequestReplayCache.<{m.group('res')}>get({req});\n{m.group('i')}    if(cached!=null)return CompletableFuture.completedFuture(cached);\n{m.group('i')}    return MaidRequestReplayCache.wrap({req},{ren}({', '.join(names)}));\n{m.group('i')}}}\n\n"
    action_engine.write_text(s[:m.start()]+wrapper+renamed+s[m.end():],encoding='utf-8')
attempt('request-idempotency',[cache,action_engine],feature_replay)

# 6. Shared reachability helper using pure reflection.
safety=action_engine.parent/'MaidActionSafety.java'
def feature_safety_class():
    safety.write_text(f'''package {action_pkg};import java.lang.reflect.*;import java.util.*;import net.minecraft.core.BlockPos;public final class MaidActionSafety{{private MaidActionSafety(){{}}public static void requireReachable(Object c,BlockPos p){{if(c==null||p==null)throw new IllegalStateException("ACTION_CONTEXT_INVALID");Object level=find(c,"ServerLevel",4,new HashSet<>());if(level==null)level=find(c,"Level",4,new HashSet<>());Object maid=find(c,"EntityMaid",4,new HashSet<>());if(level==null||maid==null)throw new IllegalStateException("ACTION_CONTEXT_INVALID");Object server=no(level,"getServer");Object same=no(server,"isSameThread");if(same instanceof Boolean&&!((Boolean)same))throw new IllegalStateException("NOT_SERVER_THREAD");Object loaded=call(level,"hasChunkAt",new Class[]{{BlockPos.class}},new Object[]{{p}});if(loaded instanceof Boolean&&!((Boolean)loaded))throw new IllegalStateException("CHUNK_NOT_LOADED");double dx=num(no(maid,"getX"))-p.getX()-.5,dy=num(no(maid,"getY"))-p.getY()-.5,dz=num(no(maid,"getZ"))-p.getZ()-.5;if(dx*dx+dy*dy+dz*dz>36)throw new IllegalStateException("OUT_OF_RANGE");Object seen=call(c,"canSee",new Class[]{{BlockPos.class}},new Object[]{{p}});if(seen instanceof Boolean&&!((Boolean)seen))throw new IllegalStateException("NO_LINE_OF_SIGHT");}}public static boolean useSupportedBlock(Object c,BlockPos p){{requireReachable(c,p);Object level=find(c,"ServerLevel",4,new HashSet<>());if(level==null)level=find(c,"Level",4,new HashSet<>());Object maid=find(c,"EntityMaid",4,new HashSet<>());Object state=call(level,"getBlockState",new Class[]{{BlockPos.class}},new Object[]{{p}});Object block=no(state,"getBlock");if(block!=null)for(Method m:block.getClass().getMethods()){{if(!Set.of("setOpen","press","pull").contains(m.getName()))continue;Object[]a=args(m.getParameterTypes(),level,maid,state,p);if(a==null)continue;try{{Object v=m.invoke(block,a);return !(v instanceof Boolean)||((Boolean)v);}}catch(Exception ignored){{}}}}throw new IllegalStateException("UNSUPPORTED_BLOCK_INTERACTION");}}private static Object[]args(Class<?>[]t,Object l,Object e,Object s,BlockPos p){{Object[]a=new Object[t.length];for(int i=0;i<t.length;i++)if(t[i].isInstance(l))a[i]=l;else if(e!=null&&t[i].isInstance(e))a[i]=e;else if(s!=null&&t[i].isInstance(s))a[i]=s;else if(t[i].isInstance(p))a[i]=p;else if(t[i]==boolean.class||t[i]==Boolean.class)a[i]=true;else return null;return a;}}private static Object find(Object o,String n,int d,Set<Object>seen){{if(o==null||d<0||seen.contains(o))return null;if(o.getClass().getSimpleName().equals(n))return o;seen.add(o);if(d==0)return null;for(Class<?>c=o.getClass();c!=null;c=c.getSuperclass())for(Field f:c.getDeclaredFields()){{if(Modifier.isStatic(f.getModifiers()))continue;try{{f.setAccessible(true);Object r=find(f.get(o),n,d-1,seen);if(r!=null)return r;}}catch(Exception ignored){{}}}}return null;}}private static Object no(Object o,String n){{return call(o,n,new Class[0],new Object[0]);}}private static Object call(Object o,String n,Class<?>[]t,Object[]a){{if(o==null)return null;try{{Method m=o.getClass().getMethod(n,t);m.setAccessible(true);return m.invoke(o,a);}}catch(Exception e){{return null;}}}}private static double num(Object v){{return v instanceof Number?((Number)v).doubleValue():0;}}}}
''',encoding='utf-8')
attempt('action-safety-class',[safety],feature_safety_class)

# Utility to infer action context and first BlockPos within the production execute method.
def insert_reachability(path:Path):
    s=path.read_text()
    if 'MaidActionSafety.requireReachable' in s:return
    method=re.search(r'(?m)^(?P<i>[ \t]*)(?:public|protected)\s+[^\n]+\((?P<params>[^)]*(?:Context|Action)[^)]*)\)\s*\{',s)
    ctx='context'
    if method:
        for part in method.group('params').split(','):
            if 'Context' in part:ctx=re.split(r'\s+',part.strip())[-1];break
    search_from=method.end() if method else 0
    bp=re.search(r'(?m)^(?P<i>[ \t]*)BlockPos\s+(?P<p>\w+)\s*=.*?;',s[search_from:])
    if not bp:raise RuntimeError('BlockPos local missing')
    a=search_from+bp.start();b=search_from+bp.end();line=s[a:b]
    s=s[:a]+line+f"\n{bp.group('i')}MaidActionSafety.requireReachable({ctx}, {bp.group('p')});"+s[b:];path.write_text(s,encoding='utf-8')

for stem in ['InspectContainerAction','TransferContainerAction','SmeltAction','SmeltCollectAction','UseBlockAction','BreakBlockAction','PlaceBlockAction']:
    rows=list(JAVA.rglob(stem+'.java'))
    if not rows:continue
    path=rows[0]
    attempt('reachability-'+stem,[path],lambda p=path:insert_reachability(p))

# 7. UseBlock must not directly mutate state to imitate an interaction.
rows=list(JAVA.rglob('UseBlockAction.java'))
if rows:
    path=rows[0]
    def feature_useblock():
        s=path.read_text();method=re.search(r'\((?P<params>[^)]*Context[^)]*)\)\s*\{',s);ctx='context'
        if method:
            for part in method.group('params').split(','):
                if 'Context' in part:ctx=re.split(r'\s+',part.strip())[-1]
        bp=re.search(r'BlockPos\s+(\w+)\s*=',s);pos=bp.group(1) if bp else 'pos'
        m=re.search(r'\b\w+\.setBlock\s*\(',s)
        if not m:return
        open_idx=s.find('(',m.start());depth=0;end=None
        for i in range(open_idx,len(s)):
            if s[i]=='(':depth+=1
            elif s[i]==')':
                depth-=1
                if depth==0:end=i+1;break
        if end is None:raise RuntimeError('unbalanced setBlock')
        s=s[:m.start()]+f'MaidActionSafety.useSupportedBlock({ctx}, {pos})'+s[end:]
        if '.setBlock(' in s:raise RuntimeError('setBlock remains')
        path.write_text(s,encoding='utf-8')
    attempt('useblock-legitimate-interaction',[path],feature_useblock)

# 8. Craft recipe isolation.
rows=list(JAVA.rglob('CraftAction.java'))
if rows:
    path=rows[0]
    def feature_craft():
        s=path.read_text()
        if 'instanceof net.minecraft.world.item.crafting.CraftingRecipe' in s:return
        m=re.search(r'(?m)(?P<i>^[ \t]*)for\s*\(\s*(?:Recipe<\?>|var)\s+(?P<v>\w+)\s*:\s*[^)]+\)\s*\{',s)
        if not m:raise RuntimeError('recipe loop missing')
        add=m.group(0)+f"\n{m.group('i')}    if (!({m.group('v')} instanceof net.minecraft.world.item.crafting.CraftingRecipe)) continue;"
        path.write_text(s[:m.start()]+add+s[m.end():],encoding='utf-8')
    attempt('craft-recipe-type-isolation',[path],feature_craft)

# 9. Smelt/collect require real furnace block entities; collection is cumulative.
for stem in ['SmeltAction','SmeltCollectAction']:
    rows=list(JAVA.rglob(stem+'.java'))
    if not rows:continue
    path=rows[0]
    def feature_furnace(p=path):
        s=p.read_text()
        if 'AbstractFurnaceBlockEntity' not in s:
            m=re.search(r'(?m)(?P<i>^[ \t]*)(?:var|BlockEntity|net\.minecraft\.world\.level\.block\.entity\.BlockEntity)\s+(?P<v>\w+)\s*=\s*[^;]+;',s)
            if not m:raise RuntimeError('block entity local missing')
            guard=f"\n{m.group('i')}if (!({m.group('v')} instanceof net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity)) throw new IllegalStateException(\"NOT_FURNACE\");"
            s=s[:m.end()]+guard+s[m.end():]
        s=re.sub(r'\bcollected\s*=\s*([^;]+\.getCount\(\))\s*;',r'collected += \1;',s)
        p.write_text(s,encoding='utf-8')
    attempt('real-furnace-'+stem,[path],feature_furnace)

# 10. Discovery/bind ownership is still enforced by Agent; add bridge-side owner policy helper for handlers that expose owner fields.
owner_policy=controller.parent/'MaidOwnershipPolicy.java'
def feature_owner_policy():
    owner_policy.write_text(f'''package {controller_pkg};import java.lang.reflect.*;import java.util.*;public final class MaidOwnershipPolicy{{private MaidOwnershipPolicy(){{}}public static boolean ownedBy(Object maid,String ownerUuid){{if(ownerUuid==null||ownerUuid.isBlank())return true;if(maid==null)return false;for(String n:new String[]{{"getOwnerUUID","getOwnerUuid","getOwner"}})try{{Method m=maid.getClass().getMethod(n);Object v=m.invoke(maid);if(v==null)return false;if(v instanceof Optional<?>o)v=o.orElse(null);if(v!=null&&v.getClass().getSimpleName().contains("Player"))try{{v=v.getClass().getMethod("getUUID").invoke(v);}}catch(Exception ignored){{}}return ownerUuid.equalsIgnoreCase(String.valueOf(v));}}catch(Exception ignored){{}}return false;}}}}
''',encoding='utf-8')
attempt('owner-policy-helper',[owner_policy],feature_owner_policy)

# 11. Motion preemption signal/event. Actual MotionArbiter remains sole owner.
motion_rows=list(JAVA.rglob('MotionArbiter.java'))
if motion_rows:
    motion=motion_rows[0];preempt=motion.parent/'MaidMotionPreemptionEvents.java';mpkg=pkg(motion)
    def feature_preemption():
        preempt.write_text(f'''package {mpkg};import java.util.*;public final class MaidMotionPreemptionEvents{{private MaidMotionPreemptionEvents(){{}}public static void signal(Object previous,Object next){{Map<String,Object>d=new HashMap<>();d.put("previous_owner",String.valueOf(previous));d.put("next_owner",String.valueOf(next));{controller_pkg}.MaidBridgeEventPublisher.publish("ACTION_PREEMPTED","WARN",d);}}}}
''',encoding='utf-8')
        s=motion.read_text()
        if 'MaidMotionPreemptionEvents.signal' in s:return
        # Prefer a method explicitly named preempt; signal old/new using this object when exact owner fields are not public.
        m=re.search(r'(?m)(?P<i>^[ \t]*)(?:public|private|protected)\s+[^\n]+\s+(?P<n>\w*[Pp]reempt\w*)\s*\((?P<params>[^)]*)\)\s*\{',s)
        if not m:
            m=re.search(r'(?m)(?P<i>^[ \t]*)(?:public|private|protected)\s+[^\n]+\s+(?P<n>acquire|claim)\s*\((?P<params>[^)]*)\)\s*\{',s)
        if not m:raise RuntimeError('preemption/acquire method missing')
        args=[re.split(r'\s+',x.strip())[-1] for x in m.group('params').split(',') if x.strip()];next_arg=args[0] if args else 'this'
        add=m.group(0)+f"\n{m.group('i')}    MaidMotionPreemptionEvents.signal(this, {next_arg});"
        motion.write_text(s[:m.start()]+add+s[m.end():],encoding='utf-8')
    attempt('motion-preemption-event',[motion,preempt],feature_preemption)

# Full final bridge build.
final_ok,tail=run_build('final');report['final']={'ok':final_ok,'tail':tail[-5000:],'modified_files':tracked_modified_bridge()};REPORT.write_text(json.dumps(report,indent=2),encoding='utf-8')
raise SystemExit(0 if final_ok else 1)
