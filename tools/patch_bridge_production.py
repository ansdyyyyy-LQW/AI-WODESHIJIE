from __future__ import annotations

import json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"maid-ai-bridge"/"src"/"main"/"java"
report={"created":[],"modified":[],"warnings":[]}

def find(stem:str)->Path:
    rows=list(ROOT.rglob(stem+".java"))
    if not rows:raise FileNotFoundError(stem)
    return rows[0]

def package(path:Path)->str:
    m=re.search(r"^package\s+([\w.]+);",path.read_text(),re.M)
    if not m:raise RuntimeError(path)
    return m.group(1)

def write(path:Path,text:str):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8");report["created"].append(str(path.relative_to(ROOT)))

def modify(path:Path,text:str):
    path.write_text(text,encoding="utf-8");report["modified"].append(str(path.relative_to(ROOT)))

# Find core packages dynamically.
controller=find("MaidAiController");controller_pkg=package(controller);controller_dir=controller.parent
action_engine=find("ActionEngine");action_pkg=package(action_engine);action_dir=action_engine.parent
try:observation=find("MaidObservationService")
except FileNotFoundError:
    observation=next((p for p in ROOT.rglob("*.java") if "StateSnapshot" in p.read_text(errors="ignore") and "snapshot" in p.stem.lower()),None)

# ---------- replay cache ----------
write(action_dir/"MaidRequestReplayCache.java",f'''package {action_pkg};

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/** Bounded server-side request replay cache. A repeated request_id returns the original result. */
public final class MaidRequestReplayCache {{
    private static final int LIMIT = 512;
    private static final Map<String,Object> CACHE = new LinkedHashMap<>(LIMIT,0.75f,true) {{
        @Override protected boolean removeEldestEntry(Map.Entry<String,Object> eldest) {{ return size() > LIMIT; }}
    }};
    private MaidRequestReplayCache() {{}}
    @SuppressWarnings("unchecked") public static synchronized <T> T get(Object request) {{
        String id=requestId(request); return id.isBlank()?null:(T)CACHE.get(id);
    }}
    public static <T> CompletableFuture<T> wrap(Object request,CompletableFuture<T> future) {{
        String id=requestId(request);
        if(id.isBlank()) return future;
        return future.whenComplete((result,error)->{{ if(error==null && result!=null) synchronized(MaidRequestReplayCache.class){{ CACHE.put(id,result); }} }});
    }}
    public static synchronized void clear() {{ CACHE.clear(); }}
    private static String requestId(Object request) {{
        if(request==null)return "";
        for(String name:new String[]{{"requestId","request_id","id"}}){{
            try{{ Method m=request.getClass().getMethod(name); Object v=m.invoke(request); if(v!=null)return String.valueOf(v); }}catch(Exception ignored){{}}
        }}
        return "";
    }}
}}
''')

# Wrap ActionEngine's production future method without editing its internals.
s=action_engine.read_text()
if "MaidRequestReplayCache.wrap" not in s:
    pattern=re.compile(r"(?P<indent>^[ \t]*)(?P<mods>public\s+)(?P<ret>CompletableFuture\s*<\s*(?P<result>[\w.]+)\s*>)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?P<tail>\s*(?:throws\s+[^\{{]+)?\s*)\{",re.M)
    candidates=list(pattern.finditer(s));match=next((m for m in candidates if "request" in m.group("params").lower()),candidates[0] if candidates else None)
    if match:
        params=match.group("params");names=[]
        for part in params.split(','):
            part=re.sub(r"@\w+(?:\([^)]*\))?\s*","",part.strip())
            names.append(re.split(r"\s+",part)[-1])
        request_name=next((n for n in names if "request" in n.lower()),names[0]);original=match.group("name");renamed=original+"Uncached"
        original_signature=match.group(0);renamed_signature=original_signature.replace(original+"(",renamed+"(",1)
        wrapper=(f"{match.group('indent')}public {match.group('ret')} {original}({params}){match.group('tail')}{{\n"
                 f"{match.group('indent')}    {match.group('result')} cached = MaidRequestReplayCache.<{match.group('result')}>get({request_name});\n"
                 f"{match.group('indent')}    if (cached != null) return CompletableFuture.completedFuture(cached);\n"
                 f"{match.group('indent')}    return MaidRequestReplayCache.wrap({request_name}, {renamed}({', '.join(names)}));\n"
                 f"{match.group('indent')}}}\n\n")
        s=s[:match.start()]+wrapper+renamed_signature+s[match.end():];modify(action_engine,s)
    else:report["warnings"].append("ActionEngine future method not found")

# ---------- strict reachability / legitimate block interaction ----------
write(action_dir/"MaidActionSafety.java",f'''package {action_pkg};

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/** Concrete survival checks shared by all block/container actions. */
public final class MaidActionSafety {{
    private MaidActionSafety() {{}}
    public static void requireReachable(Object context,BlockPos pos) {{
        Level level=find(context,Level.class,4); Entity maid=find(context,Entity.class,4);
        if(level==null||maid==null)throw new IllegalStateException("ACTION_CONTEXT_INVALID");
        if(level.isClientSide)throw new IllegalStateException("CLIENT_WORLD_MUTATION_FORBIDDEN");
        if(level.getServer()!=null&&!level.getServer().isSameThread())throw new IllegalStateException("NOT_SERVER_THREAD");
        if(!level.hasChunkAt(pos))throw new IllegalStateException("CHUNK_NOT_LOADED");
        if(maid.distanceToSqr(Vec3.atCenterOf(pos))>36.0D)throw new IllegalStateException("OUT_OF_RANGE");
        Vec3 eye=maid.getEyePosition(); Vec3 target=Vec3.atCenterOf(pos);
        HitResult hit=level.clip(new ClipContext(eye,target,ClipContext.Block.COLLIDER,ClipContext.Fluid.NONE,maid));
        if(hit.getType()==HitResult.Type.BLOCK && hit.getLocation().distanceToSqr(target)>2.5D)throw new IllegalStateException("NO_LINE_OF_SIGHT");
    }}
    /** Uses the block's own interaction method by reflection. It never calls Level#setBlock. */
    public static boolean useSupportedBlock(Object context,BlockPos pos) {{
        requireReachable(context,pos); Level level=find(context,Level.class,4); Entity maid=find(context,Entity.class,4);
        BlockState state=level.getBlockState(pos); Object block=state.getBlock();
        for(Method method:block.getClass().getMethods()){{
            if(!method.getName().equals("setOpen")&&!method.getName().equals("press")&&!method.getName().equals("pull"))continue;
            Object[] args=arguments(method.getParameterTypes(),level,maid,state,pos);
            if(args==null)continue;
            try{{method.setAccessible(true);Object value=method.invoke(block,args);return !(value instanceof Boolean)||((Boolean)value);}}catch(ReflectiveOperationException ignored){{}}
        }}
        throw new IllegalStateException("UNSUPPORTED_BLOCK_INTERACTION");
    }}
    private static Object[] arguments(Class<?>[] types,Level level,Entity maid,BlockState state,BlockPos pos){{
        Object[] args=new Object[types.length];
        for(int i=0;i<types.length;i++){{Class<?> t=types[i];
            if(t.isInstance(level))args[i]=level;else if(t.isInstance(maid)||t==Entity.class)args[i]=maid;else if(t.isInstance(state))args[i]=state;
            else if(t.isInstance(pos))args[i]=pos;else if(t==boolean.class||t==Boolean.class){{
                try{{Object property=Class.forName("net.minecraft.world.level.block.state.properties.BlockStateProperties").getField("OPEN").get(null);Method getValue=state.getClass().getMethod("getValue",Class.forName("net.minecraft.world.level.block.state.properties.Property"));args[i]=!Boolean.TRUE.equals(getValue.invoke(state,property));}}catch(Exception e){{args[i]=Boolean.TRUE;}}
            }}else return null;
        }}return args;
    }}
    public static <T>T find(Object root,Class<T> type,int depth){{return find(root,type,depth,Collections.newSetFromMap(new IdentityHashMap<>()));}}
    private static <T>T find(Object root,Class<T> type,int depth,Set<Object> seen){{
        if(root==null||depth<0||seen.contains(root))return null;if(type.isInstance(root))return type.cast(root);seen.add(root);
        for(Method m:root.getClass().getMethods()){{if(m.getParameterCount()!=0||Modifier.isStatic(m.getModifiers()))continue;try{{Object v=m.invoke(root);if(type.isInstance(v))return type.cast(v);}}catch(Exception ignored){{}}}}
        if(depth==0)return null;
        for(Class<?> c=root.getClass();c!=null;c=c.getSuperclass())for(Field f:c.getDeclaredFields()){{if(Modifier.isStatic(f.getModifiers()))continue;try{{f.setAccessible(true);Object v=f.get(root);T found=find(v,type,depth-1,seen);if(found!=null)return found;}}catch(Exception ignored){{}}}}
        return null;
    }}
}}
''')

# Insert reachability immediately after the first BlockPos local in concrete actions.
for stem in ("UseBlockAction","InspectContainerAction","TransferContainerAction","SmeltAction","SmeltCollectAction","BreakBlockAction","PlaceBlockAction"):
    try:path=find(stem)
    except FileNotFoundError:continue
    text=path.read_text()
    if "MaidActionSafety.requireReachable" not in text:
        # infer the context variable from a method parameter, default context
        ctx="context"
        mm=re.search(r"\(([^)]*(?:ActionContext|MaidActionContext)[^)]*)\)\s*\{",text)
        if mm:
            for part in mm.group(1).split(','):
                if "Context" in part:ctx=re.split(r"\s+",part.strip())[-1];break
        bp=re.search(r"(?P<indent>^[ \t]*)BlockPos\s+(?P<pos>\w+)\s*=.*?;",text,re.M)
        if bp:
            insertion=bp.group(0)+f"\n{bp.group('indent')}MaidActionSafety.requireReachable({ctx}, {bp.group('pos')});"
            text=text[:bp.start()]+insertion+text[bp.end():]
        else:report["warnings"].append(f"{stem}: BlockPos local not found")
    modify(path,text)

# Replace direct setBlock expression in UseBlockAction with legitimate helper.
try:
    path=find("UseBlockAction");text=path.read_text();ctx="context";pos="pos"
    mm=re.search(r"\(([^)]*(?:ActionContext|MaidActionContext)[^)]*)\)\s*\{",text)
    if mm:
        for part in mm.group(1).split(','):
            if "Context" in part:ctx=re.split(r"\s+",part.strip())[-1]
    bp=re.search(r"BlockPos\s+(\w+)\s*=",text)
    if bp:pos=bp.group(1)
    # balanced replacement for .setBlock(...)
    start_match=re.search(r"\b\w+\.setBlock\s*\(",text)
    if start_match:
        start=start_match.start();open_idx=text.find('(',start_match.start());depth=0;end=None
        for i in range(open_idx,len(text)):
            if text[i]=='(':depth+=1
            elif text[i]==')':
                depth-=1
                if depth==0:end=i+1;break
        if end:
            text=text[:start]+f"MaidActionSafety.useSupportedBlock({ctx}, {pos})"+text[end:]
    if ".setBlock(" in text:report["warnings"].append("UseBlockAction still contains setBlock")
    modify(path,text)
except FileNotFoundError:pass

# Craft must never traverse non-crafting recipe types.
try:
    path=find("CraftAction");text=path.read_text()
    if "instanceof net.minecraft.world.item.crafting.CraftingRecipe" not in text:
        loop=re.search(r"(?P<indent>^[ \t]*)for\s*\(\s*(?:Recipe<\?>|var)\s+(?P<var>\w+)\s*:\s*[^)]+\)\s*\{",text,re.M)
        if loop:
            insertion=loop.group(0)+f"\n{loop.group('indent')}    if (!({loop.group('var')} instanceof net.minecraft.world.item.crafting.CraftingRecipe)) continue;"
            text=text[:loop.start()]+insertion+text[loop.end():]
        else:report["warnings"].append("CraftAction recipe loop not found")
    modify(path,text)
except FileNotFoundError:pass

# Furnace actions: require a real AbstractFurnaceBlockEntity and accumulate collection counts.
for stem in ("SmeltAction","SmeltCollectAction"):
    try:path=find(stem)
    except FileNotFoundError:continue
    text=path.read_text()
    # Replace instanceof Container binding and all references in that source conservatively.
    m=re.search(r"instanceof\s+(?:net\.minecraft\.world\.)?Container\s+(\w+)",text)
    if m:
        old=m.group(1);text=text[:m.start()]+f"instanceof net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity furnace"+text[m.end():];text=re.sub(rf"\b{re.escape(old)}\b","furnace",text)
    text=re.sub(r"\bcollected\s*=\s*([^;]+\.getCount\(\))\s*;",r"collected += \1;",text)
    modify(path,text)

# ---------- event publisher and Forge hooks ----------
modid="maid_ai_bridge"
mods_toml=next((p for p in (ROOT.parents[2]/"resources").rglob("mods.toml")),None)
if mods_toml:
    mt=re.search(r'modId\s*=\s*"([^"]+)"',mods_toml.read_text(errors="ignore"));modid=mt.group(1) if mt else modid
write(controller_dir/"MaidBridgeEventPublisher.java",f'''package {controller_pkg};

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.time.Instant;
import java.util.Collections;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Queue;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;

/** Runtime EVENT producer. Events use the same socket as snapshots and are deduplicated by event_id. */
public final class MaidBridgeEventPublisher {{
    private static final Gson GSON=new Gson();private static final Queue<String> QUEUE=new ConcurrentLinkedQueue<>();private static final AtomicLong SEQ=new AtomicLong();
    private static String inventoryHash="";private static int lastDay=-1;private static double lastHealth=-1;private static String lastAction="";private static long lastWaveTick=-9999;
    private MaidBridgeEventPublisher(){{}}
    public static void publish(String type,String severity,Map<String,Object> data){{
        JsonObject payload=new JsonObject();String eventId=UUID.randomUUID().toString();payload.addProperty("event_id",eventId);payload.addProperty("event_type",type);payload.addProperty("severity",severity);payload.addProperty("game_tick",number(data.get("game_tick"),0));payload.add("data",GSON.toJsonTree(data));
        JsonObject envelope=new JsonObject();envelope.addProperty("protocol_version",1);envelope.addProperty("type","EVENT");envelope.addProperty("message_id",eventId);envelope.addProperty("game_tick",number(data.get("game_tick"),0));envelope.addProperty("timestamp_ms",System.currentTimeMillis());envelope.add("payload",payload);QUEUE.add(GSON.toJson(envelope));
    }}
    public static void observeSnapshot(Object snapshot){{
        if(snapshot==null)return;long tick=longValue(snapshot,"gameTick","game_tick","tick");int day=(int)longValue(snapshot,"day","gameDay");double health=doubleValue(snapshot,"health");String inventory=String.valueOf(value(snapshot,"inventory"));
        Map<String,Object> data=new HashMap<>();data.put("game_tick",tick);data.put("day",day);
        if(lastDay>=0&&day!=lastDay)publish("DAY_CHANGED","INFO",data);lastDay=day;
        if(!inventory.equals(inventoryHash)&&!inventoryHash.isBlank())publish("INVENTORY_CHANGED","INFO",data);inventoryHash=inventory;
        if(lastHealth>=0&&health<lastHealth){{data.put("amount",lastHealth-health);data.put("health",health);publish("DAMAGE_TAKEN",health<=6?"ERROR":"WARN",data);}}
        if(health>0&&health<=6)publish("LOW_HEALTH","ERROR",data);lastHealth=health;
        Object entities=value(snapshot,"nearbyEntities","nearby_entities");int targeting=countTargeting(entities);
        if(targeting>=4&&tick-lastWaveTick>100){{data.put("count",targeting);publish("HOSTILE_WAVE_DETECTED","ERROR",data);lastWaveTick=tick;}}
    }}
    public static void observeAction(Object controller){{
        Object action=findNamed(controller,"currentAction",4);if(action==null)action=findNamed(controller,"actionEngine",3);String now=String.valueOf(action);
        if(!now.equals(lastAction)){{Map<String,Object> data=new HashMap<>();data.put("state",now);publish(lastAction.isBlank()?"ACTION_STARTED":"ACTION_STATE_CHANGED","INFO",data);lastAction=now;}}
    }}
    public static void flush(Object root){{
        if(QUEUE.isEmpty()||root==null)return;Object sender=findSender(root,5,Collections.newSetFromMap(new IdentityHashMap<>()));if(sender==null)return;
        Method method=sendMethod(sender);if(method==null)return;String raw;
        while((raw=QUEUE.poll())!=null)try{{method.setAccessible(true);method.invoke(sender,raw);}}catch(Exception e){{QUEUE.add(raw);break;}}
    }}
    private static Method sendMethod(Object root){{for(Method m:root.getClass().getMethods())if((m.getName().equals("send")||m.getName().equals("sendText")||m.getName().equals("sendJson")||m.getName().equals("sendMessage"))&&m.getParameterCount()==1&&m.getParameterTypes()[0]==String.class)return m;return null;}}
    private static Object findSender(Object root,int depth,Set<Object> seen){{if(root==null||depth<0||seen.contains(root))return null;seen.add(root);if(sendMethod(root)!=null)return root;if(depth==0)return null;for(Class<?> c=root.getClass();c!=null;c=c.getSuperclass())for(Field f:c.getDeclaredFields()){{if(Modifier.isStatic(f.getModifiers()))continue;try{{f.setAccessible(true);Object found=findSender(f.get(root),depth-1,seen);if(found!=null)return found;}}catch(Exception ignored){{}}}}return null;}}
    private static Object findNamed(Object root,String name,int depth){{if(root==null||depth<0)return null;for(Method m:root.getClass().getMethods())if(m.getParameterCount()==0&&m.getName().equalsIgnoreCase(name))try{{return m.invoke(root);}}catch(Exception ignored){{}}for(Class<?> c=root.getClass();c!=null;c=c.getSuperclass())for(Field f:c.getDeclaredFields())if(f.getName().equalsIgnoreCase(name))try{{f.setAccessible(true);return f.get(root);}}catch(Exception ignored){{}}return null;}}
    private static Object value(Object root,String... names){{if(root==null)return null;for(String name:names){{Object value=findNamed(root,name,0);if(value!=null)return value;}}return null;}}
    private static long longValue(Object root,String...names){{Object v=value(root,names);return v instanceof Number?((Number)v).longValue():0;}}
    private static double doubleValue(Object root,String...names){{Object v=value(root,names);return v instanceof Number?((Number)v).doubleValue():0;}}
    private static long number(Object value,long fallback){{return value instanceof Number?((Number)value).longValue():fallback;}}
    private static int countTargeting(Object entities){{if(!(entities instanceof Iterable<?> list))return 0;int count=0;for(Object e:list){{Object cat=value(e,"category");Object targeting=value(e,"targetingMaid","targeting_maid","recentAttacker","recent_attacker");if("HOSTILE".equals(String.valueOf(cat))||Boolean.TRUE.equals(targeting))count++;}}return count;}}
}}
''')
write(controller_dir/"MaidAiForgeEvents.java",f'''package {controller_pkg};

import java.util.HashMap;
import java.util.Map;
import net.minecraftforge.event.entity.living.LivingDamageEvent;
import net.minecraftforge.event.entity.living.LivingDeathEvent;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;

@Mod.EventBusSubscriber(modid="{modid}",bus=Mod.EventBusSubscriber.Bus.FORGE)
public final class MaidAiForgeEvents {{
    private MaidAiForgeEvents(){{}}
    private static boolean maid(Object entity){{return entity!=null&&entity.getClass().getSimpleName().equals("EntityMaid");}}
    @SubscribeEvent public static void damage(LivingDamageEvent event){{if(!maid(event.getEntity()))return;Map<String,Object> data=new HashMap<>();data.put("amount",event.getAmount());data.put("health",event.getEntity().getHealth());data.put("entity_uuid",event.getEntity().getUUID().toString());if(event.getSource().getEntity()!=null){{data.put("attacker_uuid",event.getSource().getEntity().getUUID().toString());data.put("attacker_type",event.getSource().getEntity().getType().toString());}}MaidBridgeEventPublisher.publish("DAMAGE_TAKEN",event.getAmount()>=4?"ERROR":"WARN",data);}}
    @SubscribeEvent public static void death(LivingDeathEvent event){{if(!maid(event.getEntity()))return;Map<String,Object> data=new HashMap<>();data.put("entity_uuid",event.getEntity().getUUID().toString());if(event.getSource().getEntity()!=null)data.put("attacker_uuid",event.getSource().getEntity().getUUID().toString());MaidBridgeEventPublisher.publish("MAID_DEATH","CRITICAL",data);}}
}}
''')

# Patch observation service through wrapper when possible.
if observation:
    text=observation.read_text()
    if "MaidBridgeEventPublisher.observeSnapshot" not in text:
        pkg=package(observation)
        # Match a public method whose return type contains Snapshot and has a body.
        pattern=re.compile(r"(?P<indent>^[ \t]*)(?P<mods>public\s+)(?P<ret>[\w.<>]*Snapshot[\w.<>]*)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?P<tail>\s*(?:throws\s+[^\{{]+)?\s*)\{",re.M)
        m=pattern.search(text)
        if m:
            names=[re.split(r"\s+",p.strip())[-1] for p in m.group("params").split(',') if p.strip()];name=m.group("name");renamed=name+"Observed";orig=m.group(0);renamed_sig=orig.replace(name+"(",renamed+"(",1)
            wrapper=(f"{m.group('indent')}public {m.group('ret')} {name}({m.group('params')}){m.group('tail')}{{\n"
                     f"{m.group('indent')}    {m.group('ret')} snapshot = {renamed}({', '.join(names)});\n"
                     f"{m.group('indent')}    {controller_pkg}.MaidBridgeEventPublisher.observeSnapshot(snapshot);\n"
                     f"{m.group('indent')}    return snapshot;\n{m.group('indent')}}}\n\n")
            text=text[:m.start()]+wrapper+renamed_sig+text[m.end():];modify(observation,text)
        else:report["warnings"].append("snapshot-producing observation method not found")

# Patch controller tick for action event production and queued transport flush.
text=controller.read_text()
if "MaidBridgeEventPublisher.observeAction(this)" not in text:
    m=re.search(r"(?P<indent>^[ \t]*)(?:public|private|protected)\s+void\s+tick\s*\([^)]*\)\s*\{",text,re.M)
    if not m:m=re.search(r"(?P<indent>^[ \t]*)(?:public|private|protected)\s+void\s+\w*[Tt]ick\w*\s*\([^)]*\)\s*\{",text,re.M)
    if m:
        insert=m.group(0)+f"\n{m.group('indent')}    MaidBridgeEventPublisher.observeAction(this);\n{m.group('indent')}    MaidBridgeEventPublisher.flush(this);"
        text=text[:m.start()]+insert+text[m.end():]
    else:report["warnings"].append("controller tick method not found")
modify(controller,text)

(Path(__file__).resolve().parents[1]/"bridge_patch_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
