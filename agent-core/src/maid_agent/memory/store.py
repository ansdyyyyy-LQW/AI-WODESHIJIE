from __future__ import annotations

import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Iterable
from uuid import uuid4

from maid_agent.memory.schema import SCHEMA_SQL
from maid_agent.protocol.models import BridgeEvent, StateSnapshot


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value))


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            self._migrate(conn)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    def _migrate(self, conn: sqlite3.Connection) -> None:
        additions: dict[str, dict[str, str]] = {
            "events": {"event_key": "TEXT", "source": "TEXT NOT NULL DEFAULT 'agent'"},
            "goals": {"parent_goal_id": "TEXT"},
            "world_locations": {"last_seen_day": "INTEGER NOT NULL DEFAULT 0", "metadata_json": "TEXT NOT NULL DEFAULT '{}'"},
            "resource_observations": {"first_seen": "INTEGER NOT NULL DEFAULT 0", "last_seen_day": "INTEGER NOT NULL DEFAULT 0", "confidence": "REAL NOT NULL DEFAULT 1.0", "metadata_json": "TEXT NOT NULL DEFAULT '{}'"},
            "skills": {"goal_tags_json": "TEXT NOT NULL DEFAULT '[]'", "last_used_at": "TEXT", "last_failure_code": "TEXT"},
            "rnd_cycles": {
                "mode": "TEXT NOT NULL DEFAULT 'READY'", "source_workspace": "TEXT NOT NULL DEFAULT ''",
                "production_version": "TEXT NOT NULL DEFAULT ''", "source_hash": "TEXT NOT NULL DEFAULT ''",
                "phase": "TEXT NOT NULL DEFAULT 'DECIDING_DIRECTION'", "outcome": "TEXT",
                "project_id": "TEXT NOT NULL DEFAULT ''", "project_size": "TEXT",
                "continuation_decision": "TEXT NOT NULL DEFAULT 'NEW'",
                "budget_plan_json": "TEXT NOT NULL DEFAULT '{}'", "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
                "project_state_json": "TEXT NOT NULL DEFAULT '{}'", "failure_state_json": "TEXT NOT NULL DEFAULT '{}'",
                "handled": "INTEGER NOT NULL DEFAULT 0",
                "owner_pid": "INTEGER", "owner_started_at": "TEXT",
                "dsh_session_id": "TEXT NOT NULL DEFAULT ''",
                "dsh_version": "TEXT NOT NULL DEFAULT ''",
                "dsh_profile_version": "TEXT NOT NULL DEFAULT ''",
                "dsh_cli_version": "TEXT NOT NULL DEFAULT ''",
                "dsh_workspace": "TEXT NOT NULL DEFAULT ''",
                "dsh_current_phase": "TEXT NOT NULL DEFAULT ''",
                "dsh_phase_progress_json": "TEXT NOT NULL DEFAULT '{}'",
                "dsh_last_finish_reason": "TEXT NOT NULL DEFAULT ''",
                "dsh_last_event_at": "TEXT",
                "baseline_commit": "TEXT NOT NULL DEFAULT ''",
            },
            "token_usage": {"cycle_id": "TEXT"},
            "building_checkpoints": {
                "completed_indices_json": "TEXT NOT NULL DEFAULT '[]'",
                "skipped_optional_indices_json": "TEXT NOT NULL DEFAULT '[]'",
            },
        }
        for table, columns in additions.items():
            existing = self._columns(conn, table)
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_event_key ON events(event_key) WHERE event_key IS NOT NULL")
        active_cycles = conn.execute(
            "SELECT cycle_id FROM rnd_cycles WHERE status IN ('CREATED','RUNNING','SUSPENDED') "
            "ORDER BY created_at ASC, cycle_id ASC"
        ).fetchall()
        for duplicate in active_cycles[1:]:
            conn.execute(
                "UPDATE rnd_cycles SET status='WAITING_USER', outcome='WAITING_USER', "
                "summary='启动时发现旧版重复活动记录；历史与产物已保留，等待人工决定是否继续', "
                "owner_pid=NULL, owner_started_at=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE cycle_id=?",
                (str(duplicate["cycle_id"]),),
            )
        conn.execute("DROP INDEX IF EXISTS idx_rnd_single_active")
        conn.execute(
            "CREATE UNIQUE INDEX idx_rnd_single_active "
            "ON rnd_cycles((1)) WHERE status IN ('CREATED','RUNNING','SUSPENDED')"
        )

    # ----- events -----
    def record_event(
        self,
        *,
        game_day: int,
        game_tick: int,
        event_type: str,
        severity: str,
        payload: dict[str, Any],
        position: dict[str, Any] | None = None,
        event_key: str | None = None,
        source: str = "agent",
    ) -> str:
        event_id = str(uuid4())
        with self.connection() as conn:
            try:
                conn.execute(
                    """INSERT INTO events(id,event_key,game_day,game_tick,type,severity,position_json,payload_json,source)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (event_id,event_key,game_day,game_tick,event_type,severity,_json(position) if position else None,_json(payload),source),
                )
            except sqlite3.IntegrityError:
                if event_key:
                    row=conn.execute("SELECT id FROM events WHERE event_key=?",(event_key,)).fetchone()
                    return str(row[0]) if row else event_id
                raise
        return event_id

    def record_bridge_event(self, event: BridgeEvent, *, fallback_day: int = 0) -> tuple[str, bool]:
        with self.connection() as conn:
            row=conn.execute("SELECT id FROM events WHERE event_key=?",(event.event_id,)).fetchone()
            if row:
                return str(row[0]), False
        event_id=self.record_event(
            game_day=event.game_day if event.game_day is not None else fallback_day,
            game_tick=event.game_tick,
            event_type=event.event_type,
            severity=event.severity,
            payload={**event.data,"time_of_day":event.time_of_day,"period":event.period},
            position=event.position.model_dump() if event.position else None,
            event_key=event.event_id,
            source="bridge",
        )
        return event_id, True

    def recent_events(self, *, limit: int = 100, min_day: int | None = None, types: Iterable[str] | None = None,
                      severity: str | None = None) -> list[dict[str, Any]]:
        clauses=[]; args:list[Any]=[]
        if min_day is not None: clauses.append("game_day>=?"); args.append(min_day)
        if types:
            values=list(types); clauses.append("type IN (%s)" % ",".join("?"*len(values))); args.extend(values)
        if severity: clauses.append("severity=?"); args.append(severity)
        query="SELECT * FROM events"+(" WHERE "+" AND ".join(clauses) if clauses else "")+" ORDER BY game_day DESC,game_tick DESC,created_at DESC LIMIT ?"
        args.append(max(1,min(limit,5000)))
        with self.connection() as conn: rows=conn.execute(query,args).fetchall()
        return [self._event_row(row) for row in rows]

    def recall_events(self, *, types: list[str] | None = None, since_day: int | None = None,
                      severity: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.recent_events(limit=limit,min_day=since_day,types=types,severity=severity)

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        item=dict(row); item["payload"]=_loads(item.pop("payload_json",None),{})
        pos=item.pop("position_json",None)
        if pos:item["position"]=_loads(pos,{})
        return item

    # ----- locations/resources/structures -----
    def remember_location(self, *, name: str, dimension: str, x: float, y: float, z: float,
                          tags: list[str] | None = None, confidence: float = 1.0,
                          game_tick: int = 0, game_day: int = 0, notes: str = "",
                          metadata: dict[str, Any] | None = None, location_id: str | None = None) -> str:
        tags=sorted(set(tags or [])); metadata=metadata or {}
        location_id=location_id or f"{dimension}:{round(x/8)}:{round(y/4)}:{round(z/8)}:{'-'.join(tags[:2]) or 'location'}"
        with self.connection() as conn:
            conn.execute("""INSERT INTO world_locations(id,name,dimension,x,y,z,tags_json,confidence,first_seen_tick,last_seen_tick,last_seen_day,notes,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,x=excluded.x,y=excluded.y,z=excluded.z,
              tags_json=excluded.tags_json,confidence=MAX(world_locations.confidence,excluded.confidence),last_seen_tick=excluded.last_seen_tick,
              last_seen_day=excluded.last_seen_day,notes=CASE WHEN excluded.notes='' THEN world_locations.notes ELSE excluded.notes END,
              metadata_json=excluded.metadata_json""",
              (location_id,name,dimension,x,y,z,_json(tags),confidence,game_tick,game_tick,game_day,notes,_json(metadata)))
        return location_id

    def recall_locations(self, *, tags: list[str] | None = None, dimension: str | None = None,
                         near: dict[str,float] | None = None, limit: int = 20) -> list[dict[str, Any]]:
        clauses=[];args:list[Any]=[]
        if dimension:clauses.append("dimension=?");args.append(dimension)
        query="SELECT * FROM world_locations"+(" WHERE "+" AND ".join(clauses) if clauses else "")+" ORDER BY last_seen_tick DESC LIMIT 500"
        with self.connection() as conn:rows=conn.execute(query,args).fetchall()
        result=[]
        required=set(tags or [])
        for row in rows:
            item=dict(row);item["tags"]=_loads(item.pop("tags_json"),[]);item["metadata"]=_loads(item.pop("metadata_json",None),{})
            if required and not required.intersection(item["tags"]):continue
            if near:
                item["distance"] = math.dist((item["x"],item["y"],item["z"]),(near["x"],near["y"],near["z"]))
            result.append(item)
        if near:result.sort(key=lambda r:(r.get("distance",1e18),-r["last_seen_tick"]))
        return result[:max(1,min(limit,100))]

    def upsert_resource_observation(self, *, block_id: str, dimension: str, x: int, y: int, z: int,
                                    exposed: bool = True, estimated_count: int = 1, game_tick: int = 0,
                                    game_day: int = 0, confidence: float = 1.0,
                                    metadata: dict[str,Any] | None = None) -> str:
        resource_id=f"{dimension}:{block_id}:{x}:{y}:{z}"
        with self.connection() as conn:
            conn.execute("""INSERT INTO resource_observations(resource_id,block_id,dimension,x,y,z,observed_exposed,estimated_count,first_seen,last_seen,last_seen_day,exhausted,confidence,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET observed_exposed=excluded.observed_exposed,
              estimated_count=MAX(resource_observations.estimated_count,excluded.estimated_count),last_seen=excluded.last_seen,last_seen_day=excluded.last_seen_day,
              exhausted=0,confidence=MAX(resource_observations.confidence,excluded.confidence),metadata_json=excluded.metadata_json""",
              (resource_id,block_id,dimension,x,y,z,1 if exposed else 0,max(1,estimated_count),game_tick,game_tick,game_day,0,confidence,_json(metadata or {})))
        return resource_id

    def mark_resource_exhausted(self, *, resource_id: str | None = None, dimension: str | None = None,
                                x: int | None = None, y: int | None = None, z: int | None = None) -> None:
        with self.connection() as conn:
            if resource_id:conn.execute("UPDATE resource_observations SET exhausted=1 WHERE resource_id=?",(resource_id,))
            elif None not in (dimension,x,y,z):conn.execute("UPDATE resource_observations SET exhausted=1 WHERE dimension=? AND x=? AND y=? AND z=?",(dimension,x,y,z))

    def recall_resources(self, *, resource_query: str | None = None, dimension: str | None = None,
                         near: dict[str,float] | None = None, limit: int = 30) -> list[dict[str,Any]]:
        clauses=["exhausted=0"];args:list[Any]=[]
        if dimension:clauses.append("dimension=?");args.append(dimension)
        if resource_query:
            query=resource_query.removeprefix("#minecraft:")
            if query=="logs":clauses.append("(block_id LIKE '%_log' OR block_id LIKE '%_stem')")
            elif query=="ores":clauses.append("block_id LIKE '%_ore'")
            else:clauses.append("block_id LIKE ?");args.append(f"%{query}%")
        sql="SELECT * FROM resource_observations WHERE "+" AND ".join(clauses)+" ORDER BY last_seen DESC LIMIT 500"
        with self.connection() as conn:rows=conn.execute(sql,args).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item["metadata"]=_loads(item.pop("metadata_json",None),{})
            if near:item["distance"]=math.dist((item["x"],item["y"],item["z"]),(near["x"],near["y"],near["z"]))
            result.append(item)
        if near:result.sort(key=lambda r:(r.get("distance",1e18),-r["last_seen"]))
        return result[:max(1,min(limit,100))]

    def remember_structure(self, *, kind: str, name: str, dimension: str, x: float, y: float, z: float,
                           tags: list[str] | None = None, game_tick: int = 0,
                           metadata: dict[str,Any] | None = None, structure_id: str | None = None) -> str:
        structure_id=structure_id or f"{dimension}:{kind}:{int(x)}:{int(y)}:{int(z)}"
        with self.connection() as conn:
            conn.execute("""INSERT INTO structures(structure_id,kind,name,dimension,x,y,z,tags_json,state,first_seen_tick,last_seen_tick,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(structure_id) DO UPDATE SET name=excluded.name,tags_json=excluded.tags_json,
              state=excluded.state,last_seen_tick=excluded.last_seen_tick,metadata_json=excluded.metadata_json""",
              (structure_id,kind,name,dimension,x,y,z,_json(sorted(set(tags or []))),"KNOWN",game_tick,game_tick,_json(metadata or {})))
        return structure_id

    def recall_structures(self, *, kind: str | None = None, dimension: str | None = None, limit: int = 50) -> list[dict[str,Any]]:
        clauses=["state='KNOWN'"];args=[]
        if kind:clauses.append("kind=?");args.append(kind)
        if dimension:clauses.append("dimension=?");args.append(dimension)
        sql="SELECT * FROM structures"+(" WHERE "+" AND ".join(clauses) if clauses else "")+" ORDER BY last_seen_tick DESC LIMIT ?";args.append(limit)
        with self.connection() as conn:rows=conn.execute(sql,args).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item["tags"]=_loads(item.pop("tags_json"),[]);item["metadata"]=_loads(item.pop("metadata_json"),{});result.append(item)
        return result

    def mark_structure_missing(self, *, kind:str, dimension:str, x:float, y:float, z:float, radius:float=4.0)->int:
        with self.connection() as conn:
            cursor=conn.execute("""UPDATE structures SET state='MISSING' WHERE state='KNOWN' AND kind=? AND dimension=? AND ((x-?)*(x-?)+(y-?)*(y-?)+(z-?)*(z-?))<=?""",
                (kind,dimension,x,x,y,y,z,z,radius*radius))
            return int(cursor.rowcount)

    # ----- Goal/plan/strategy recovery -----
    def save_model(self, table: str, key_column: str, key: str, model: Any, *, status: str) -> None:
        if table not in {"goals","plans"}:raise ValueError("unsupported model table")
        raw=model.model_dump_json() if hasattr(model,"model_dump_json") else _json(model)
        with self.connection() as conn:
            if table=="goals":
                conn.execute("""INSERT INTO goals(goal_id,type,objective,priority,status,model_json,parent_goal_id)
                  VALUES(?,?,?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET status=excluded.status,model_json=excluded.model_json,
                  parent_goal_id=excluded.parent_goal_id,updated_at=CURRENT_TIMESTAMP""",
                  (key,_enum(model.type),model.objective,model.priority,_enum(status),raw,str(model.parent_goal_id) if getattr(model,"parent_goal_id",None) else None))
            else:
                conn.execute("""INSERT INTO plans(plan_id,goal_id,status,checkpoint_json,model_json)
                  VALUES(?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET status=excluded.status,checkpoint_json=excluded.checkpoint_json,
                  model_json=excluded.model_json,updated_at=CURRENT_TIMESTAMP""",
                  (key,str(model.goal_id),_enum(status),_json(getattr(model,"checkpoint",{})),raw))

    def load_latest_active_goal(self) -> dict[str,Any] | None:
        with self.connection() as conn:row=conn.execute("SELECT model_json FROM goals WHERE status IN ('ACTIVE','PAUSED','PAUSED_MATERIALS','NEEDS_REVALIDATION') ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'NEEDS_REVALIDATION' THEN 1 ELSE 2 END,updated_at DESC,rowid DESC LIMIT 1").fetchone()
        return _loads(row[0],None) if row else None

    def load_plan(self, plan_id: str) -> dict[str,Any] | None:
        with self.connection() as conn:
            row=conn.execute("SELECT model_json FROM plans WHERE plan_id=?",(plan_id,)).fetchone()
        return _loads(row[0],None) if row else None

    def load_goal(self, goal_id: str) -> dict[str,Any] | None:
        with self.connection() as conn:
            row=conn.execute("SELECT model_json FROM goals WHERE goal_id=?",(goal_id,)).fetchone()
        return _loads(row[0],None) if row else None

    def load_latest_active_plan(self, *, goal_id: str | None = None) -> dict[str,Any] | None:
        sql="SELECT model_json FROM plans WHERE status IN ('PENDING','RUNNING','PAUSED','NEEDS_REVALIDATION')";args=[]
        if goal_id:sql+=" AND goal_id=?";args.append(goal_id)
        sql+=" ORDER BY updated_at DESC,rowid DESC LIMIT 1"
        with self.connection() as conn:row=conn.execute(sql,args).fetchone()
        return _loads(row[0],None) if row else None

    def save_strategy_state(self, model: Any) -> None:
        raw=model.model_dump_json() if hasattr(model,"model_dump_json") else _json(model)
        with self.connection() as conn:conn.execute("INSERT INTO strategy_state(singleton,model_json) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET model_json=excluded.model_json,updated_at=CURRENT_TIMESTAMP",(raw,))

    def load_strategy_state(self) -> dict[str,Any] | None:
        with self.connection() as conn:row=conn.execute("SELECT model_json FROM strategy_state WHERE singleton=1").fetchone()
        return _loads(row[0],None) if row else None

    # ----- capability gaps -----
    def record_capability_gap(self, gap: Any) -> dict[str,Any]:
        with self.connection() as conn:
            row=conn.execute("SELECT occurrence_count FROM capability_gaps WHERE gap_id=?",(gap.gap_id,)).fetchone()
            count=(int(row[0])+1) if row else max(1,int(gap.occurrence_count))
            stored=gap.model_copy(update={"occurrence_count":count}) if hasattr(gap,"model_copy") else gap
            raw=stored.model_dump(mode="json") if hasattr(stored,"model_dump") else dict(stored)
            conn.execute(
                """INSERT INTO capability_gaps(
                     gap_id,desired_objective,expression_failure_reason,missing_capability_type,
                     occurrence_count,impact,last_game_day,last_game_tick,last_occurred_at,status,model_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(gap_id) DO UPDATE SET
                     desired_objective=excluded.desired_objective,
                     expression_failure_reason=excluded.expression_failure_reason,
                     missing_capability_type=excluded.missing_capability_type,
                     occurrence_count=excluded.occurrence_count,
                     impact=excluded.impact,last_game_day=excluded.last_game_day,
                     last_game_tick=excluded.last_game_tick,last_occurred_at=excluded.last_occurred_at,
                     status=excluded.status,model_json=excluded.model_json,updated_at=CURRENT_TIMESTAMP""",
                (
                    stored.gap_id,stored.desired_objective,stored.expression_failure_reason,
                    stored.missing_capability_type,count,stored.impact,stored.last_game_day,
                    stored.last_game_tick,stored.last_occurred_at,stored.status,_json(raw),
                ),
            )
        return raw

    def list_capability_gaps(self, *, limit: int = 50, status: str | None = "OPEN") -> list[dict[str,Any]]:
        sql="SELECT model_json FROM capability_gaps";args:list[Any]=[]
        if status is not None:sql+=" WHERE status=?";args.append(status)
        sql+=" ORDER BY last_game_day DESC,last_game_tick DESC,updated_at DESC LIMIT ?";args.append(max(1,min(limit,500)))
        with self.connection() as conn:rows=conn.execute(sql,args).fetchall()
        return [_loads(row[0],{}) for row in rows]

    def set_runtime_state(self,key:str,value:Any)->None:
        with self.connection() as conn:conn.execute("INSERT INTO runtime_state(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=CURRENT_TIMESTAMP",(key,_json(value)))

    def get_runtime_state(self,key:str,default:Any=None)->Any:
        with self.connection() as conn:row=conn.execute("SELECT value_json FROM runtime_state WHERE key=?",(key,)).fetchone()
        return _loads(row[0],default) if row else default

    # ----- threat/build/telemetry helpers -----
    def upsert_threat_window(self, row: dict[str,Any]) -> None:
        with self.connection() as conn:
            conn.execute("""INSERT INTO threat_windows(window_id,day,period,started_tick,ended_tick,hostile_contacts,unique_hostiles,damage_taken,deaths,retreats,base_damage_events,targeting_peak,entry_direction_json,attacker_types_json,metadata_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(window_id) DO UPDATE SET ended_tick=excluded.ended_tick,hostile_contacts=excluded.hostile_contacts,
              unique_hostiles=excluded.unique_hostiles,damage_taken=excluded.damage_taken,deaths=excluded.deaths,retreats=excluded.retreats,
              base_damage_events=excluded.base_damage_events,targeting_peak=excluded.targeting_peak,entry_direction_json=excluded.entry_direction_json,
              attacker_types_json=excluded.attacker_types_json,metadata_json=excluded.metadata_json,updated_at=CURRENT_TIMESTAMP""",
              (row["window_id"],row["day"],row["period"],row["started_tick"],row.get("ended_tick"),row.get("hostile_contacts",0),row.get("unique_hostiles",0),row.get("damage_taken",0),row.get("deaths",0),row.get("retreats",0),row.get("base_damage_events",0),row.get("targeting_peak",0),_json(row.get("entry_direction_histogram",{})),_json(row.get("attacker_types",{})),_json(row.get("metadata",{}))))

    def recent_threat_windows(self,limit:int=10)->list[dict[str,Any]]:
        with self.connection() as conn:rows=conn.execute("SELECT * FROM threat_windows ORDER BY day DESC,started_tick DESC LIMIT ?",(limit,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item["entry_direction_histogram"]=_loads(item.pop("entry_direction_json"),{});item["attacker_types"]=_loads(item.pop("attacker_types_json"),{});item["metadata"]=_loads(item.pop("metadata_json"),{});result.append(item)
        return result

    def load_threat_window(self,window_id:str)->dict[str,Any]|None:
        with self.connection() as conn:
            row=conn.execute("SELECT * FROM threat_windows WHERE window_id=?",(window_id,)).fetchone()
        if row is None:return None
        item=dict(row);item["entry_direction_histogram"]=_loads(item.pop("entry_direction_json"),{});item["attacker_types"]=_loads(item.pop("attacker_types_json"),{});item["metadata"]=_loads(item.pop("metadata_json"),{});return item

    def save_build_checkpoint(self, *, build_id:str, blueprint:dict[str,Any], origin:dict[str,Any], rotation:int,
                              next_segment:int, completed_blocks:int, status:str, missing_items:dict[str,int]|None=None,
                              completed_indices:list[int]|set[int]|None=None,
                              skipped_optional_indices:list[int]|set[int]|None=None,last_error:str="") -> None:
        indices=sorted({int(value) for value in (completed_indices or []) if int(value)>=0})
        skipped=sorted({int(value) for value in (skipped_optional_indices or []) if int(value)>=0})
        with self.connection() as conn:
            conn.execute("""INSERT INTO building_checkpoints(build_id,blueprint_json,origin_json,rotation,next_segment,completed_blocks,completed_indices_json,skipped_optional_indices_json,status,missing_items_json,last_error)
              VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(build_id) DO UPDATE SET next_segment=excluded.next_segment,completed_blocks=excluded.completed_blocks,
              completed_indices_json=excluded.completed_indices_json,skipped_optional_indices_json=excluded.skipped_optional_indices_json,
              status=excluded.status,missing_items_json=excluded.missing_items_json,last_error=excluded.last_error,updated_at=CURRENT_TIMESTAMP""",
              (build_id,_json(blueprint),_json(origin),rotation,next_segment,completed_blocks,_json(indices),_json(skipped),status,_json(missing_items or {}),last_error))

    def load_build_checkpoint(self,build_id:str)->dict[str,Any]|None:
        with self.connection() as conn:row=conn.execute("SELECT * FROM building_checkpoints WHERE build_id=?",(build_id,)).fetchone()
        if not row:return None
        item=dict(row);item["blueprint"]=_loads(item.pop("blueprint_json"),{});item["origin"]=_loads(item.pop("origin_json"),{});item["missing_items"]=_loads(item.pop("missing_items_json"),{});item["completed_indices"]=_loads(item.pop("completed_indices_json",None),[]);item["skipped_optional_indices"]=_loads(item.pop("skipped_optional_indices_json",None),[]);return item

    def list_build_checkpoints(self,limit:int=50)->list[dict[str,Any]]:
        with self.connection() as conn:rows=conn.execute("SELECT * FROM building_checkpoints ORDER BY updated_at DESC LIMIT ?",(limit,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item["blueprint"]=_loads(item.pop("blueprint_json"),{});item["origin"]=_loads(item.pop("origin_json"),{});item["missing_items"]=_loads(item.pop("missing_items_json"),{});item["completed_indices"]=_loads(item.pop("completed_indices_json",None),[]);item["skipped_optional_indices"]=_loads(item.pop("skipped_optional_indices_json",None),[]);result.append(item)
        return result

    def record_llm_request(self, **row: Any) -> None:
        with self.connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO llm_requests(request_id,ledger,purpose,model,http_status,ok,latency_ms,prompt_tokens,completion_tokens,total_tokens,estimated,error_code,cycle_id,game_day)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (row["request_id"],row["ledger"],row["purpose"],row["model"],row.get("http_status"),1 if row.get("ok") else 0,row.get("latency_ms",0),row.get("prompt_tokens",0),row.get("completion_tokens",0),row.get("total_tokens",0),1 if row.get("estimated") else 0,row.get("error_code","")[:500],row.get("cycle_id"),row.get("game_day")))

    def recent_llm_requests(self,limit:int=20)->list[dict[str,Any]]:
        with self.connection() as conn:return [dict(r) for r in conn.execute("SELECT * FROM llm_requests ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()]

    def recall_for_goal(self, goal: Any, snapshot: StateSnapshot, *, limit: int = 12) -> dict[str,Any]:
        text=(getattr(goal,"objective","") or "").lower(); query=None; tags=[]
        for token in ("iron","coal","stone","wood","log","food","chest","furnace"):
            if token in text:query=token;break
        if any(x in text for x in ("基地","住所","安全","base","shelter")):tags=["safe","base","shelter"]
        near=snapshot.position.model_dump()
        return {
            "locations":self.recall_locations(tags=tags,dimension=snapshot.dimension,near=near,limit=limit),
            "resources":self.recall_resources(resource_query=query,dimension=snapshot.dimension,near=near,limit=limit),
            "failures":self.recent_events(limit=limit,min_day=max(0,snapshot.day-5),types=["ACTION_FAILED","ACTION_STUCK","GOAL_BLOCKED","MAID_DEATH"]),
            "structures":self.recall_structures(dimension=snapshot.dimension,limit=limit),
        }

    def summary(self)->dict[str,int]:
        with self.connection() as conn:
            return {
                "locations":conn.execute("SELECT COUNT(*) FROM world_locations").fetchone()[0],
                "resources":conn.execute("SELECT COUNT(*) FROM resource_observations WHERE exhausted=0").fetchone()[0],
                "structures":conn.execute("SELECT COUNT(*) FROM structures").fetchone()[0],
                "events":conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                "goals":conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0],
                "skills":conn.execute("SELECT COUNT(*) FROM skills WHERE status='ACTIVE'").fetchone()[0],
                "capability_gaps":conn.execute("SELECT COUNT(*) FROM capability_gaps WHERE status='OPEN'").fetchone()[0],
                "rnd_cycles":conn.execute("SELECT COUNT(*) FROM rnd_cycles").fetchone()[0],
                "builds":conn.execute("SELECT COUNT(*) FROM building_checkpoints").fetchone()[0],
            }
