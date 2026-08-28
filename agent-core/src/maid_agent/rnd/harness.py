from __future__ import annotations

import asyncio,hashlib,json,os,shutil,subprocess,sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maid_agent.rnd.models import RndCycle,RndMode
from maid_agent.rnd.locks import exclusive_file_lock

IGNORE={".git",".gradle",".venv",".runtime","build","dist","__pycache__",".pytest_cache","logs","run","data","node_modules"}


@dataclass
class HarnessResult:
    ok:bool;mode:RndMode;code:str;summary:str;workspace:Path|None;output_dir:Path;details:dict[str,Any]


class RndHarness:
    def __init__(self,*,runner_path:str|None,source_workspace:Path|None,work_root:Path):
        self.runner_path=runner_path;self.source_workspace=Path(source_workspace).resolve() if source_workspace else None;self.work_root=Path(work_root);self.work_root.mkdir(parents=True,exist_ok=True)

    def readiness(self)->tuple[RndMode,list[str]]:
        missing=[]
        if self.source_workspace is None or not self.source_workspace.exists():missing.append("source_workspace")
        if not self.runner_path:missing.append("runner_path")
        elif not Path(self.runner_path).exists() and shutil.which(self.runner_path) is None:missing.append("runner_executable")
        if "source_workspace" in missing:return RndMode.ANALYSIS_ONLY,missing
        if any(x in missing for x in ("runner_path","runner_executable")):return RndMode.ANALYSIS_ONLY,missing
        return RndMode.FULL_HARNESS,missing

    @staticmethod
    def _copy_source(source:Path,target:Path)->None:
        if target.exists():shutil.rmtree(target)
        shutil.copytree(source,target,ignore=shutil.ignore_patterns(*IGNORE),symlinks=False)

    @staticmethod
    def source_manifest(root:Path)->dict[str,Any]:
        files=[];digest=hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part in IGNORE for part in path.parts):continue
            rel=path.relative_to(root).as_posix()
            try:data=path.read_bytes()
            except OSError:continue
            sha=hashlib.sha256(data).hexdigest();files.append({"path":rel,"size":len(data),"sha256":sha});digest.update(rel.encode());digest.update(bytes.fromhex(sha))
        return {"root":str(root),"file_count":len(files),"source_hash":digest.hexdigest(),"files":files}

    async def run(self,cycle:RndCycle,*,attempt:int=1)->HarnessResult:
        lock_path=self.work_root/cycle.cycle_id/".harness.lock"
        with exclusive_file_lock(lock_path) as acquired:
            if not acquired:
                output=cycle.artifact_dir/"output"/f"harness-attempt-{attempt:02d}"
                result=HarnessResult(False,RndMode.FULL_HARNESS,"HARNESS_ALREADY_RUNNING","同一研发目录已有 Harness 正在运行，已拒绝重复操作。",None,output,{"lock":str(lock_path)})
                self._write_result(result)
                return result
            return await self._run_locked(cycle,attempt=attempt)

    async def resume(self,cycle:RndCycle,*,attempt:int=1)->HarnessResult:
        lock_path=self.work_root/cycle.cycle_id/".harness.lock"
        with exclusive_file_lock(lock_path) as acquired:
            if not acquired:
                output=cycle.artifact_dir/"output"/f"harness-attempt-{attempt:02d}"
                result=HarnessResult(False,RndMode.FULL_HARNESS,"HARNESS_ALREADY_RUNNING","同一研发目录已有 Harness 正在运行，已拒绝重复操作。",None,output,{"lock":str(lock_path)})
                self._write_result(result)
                return result
            return await self._run_locked(cycle,attempt=attempt,resume=True)

    async def _run_locked(self,cycle:RndCycle,*,attempt:int=1,resume:bool=False)->HarnessResult:
        mode,missing=self.readiness();output_root=cycle.artifact_dir/"output";output_root.mkdir(parents=True,exist_ok=True);output=output_root/f"harness-attempt-{attempt:02d}";output.mkdir(parents=True,exist_ok=True)
        workspace=self.work_root/cycle.cycle_id/f"attempt-{attempt:02d}"/"source"
        if resume and not workspace.exists():
            result=HarnessResult(False,mode,"RESUME_WORKSPACE_MISSING","已暂停的隔离研发工作区不存在，无法安全重建或假装继续。",None,output,{"missing_workspace":str(workspace)});self._write_result(result);return result
        if not resume and (self.source_workspace is None or not self.source_workspace.exists()):
            result=HarnessResult(True,RndMode.ANALYSIS_ONLY,"SOURCE_UNAVAILABLE","缺少源码工作区，仅完成运行证据分析。",None,output,{"missing":missing});self._write_result(result);return result
        if not resume:self._copy_source(self.source_workspace,workspace)
        elif "source_workspace" in missing:
            missing=[item for item in missing if item!="source_workspace"]
            mode=RndMode.FULL_HARNESS if not missing else RndMode.ANALYSIS_ONLY
        manifest=self.source_manifest(workspace);(cycle.artifact_dir/"source_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        if mode!=RndMode.FULL_HARNESS:
            result=HarnessResult(True,mode,"RUNNER_UNAVAILABLE","已读取并隔离完整源码，但缺少可运行 Harness，仅输出分析输入。",workspace,output,{"missing":missing,"source_hash":manifest["source_hash"]});self._write_result(result);return result
        command=[sys.executable,str(self.runner_path)] if str(self.runner_path).lower().endswith(".py") else [str(self.runner_path)]
        input_dir=cycle.artifact_dir/"input"
        if not input_dir.exists():input_dir=cycle.artifact_dir/"rnd-input"
        command += ["--input",str(input_dir),"--source",str(workspace),"--output",str(output),"--cycle-id",cycle.cycle_id]
        env={k:v for k,v in os.environ.items() if k in {"PATH","HOME","USERPROFILE","TEMP","TMP","SYSTEMROOT","WINDIR","JAVA_HOME","GRADLE_USER_HOME","LANG"}}
        # Never inherit keys or arbitrary model credentials into subprocesses.
        process=await asyncio.create_subprocess_exec(*command,cwd=workspace,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        try:
            stdout,stderr=await process.communicate()
        except asyncio.CancelledError:
            await self._terminate_process_tree(process)
            (output/"harness.stdout.log").write_text("R&D Harness 在 Runtime 停止时被取消。\n",encoding="utf-8")
            (output/"harness.stderr.log").write_text("CANCELLED\n",encoding="utf-8")
            self._write_result(HarnessResult(
                False,mode,"CANCELLED","隔离 Harness 已取消并关闭子进程。",workspace,output,
                {"attempt":attempt,"source_hash":manifest["source_hash"]},
            ))
            raise
        (output/"harness.stdout.log").write_bytes(stdout);(output/"harness.stderr.log").write_bytes(stderr)
        ok=process.returncode==0
        failure=self._failure_summary(output) if not ok else {}
        result=HarnessResult(ok,mode,"SUCCESS" if ok else "HARNESS_FAILED","隔离 Harness 已完成源码读取、修改尝试与构建。" if ok else "隔离 Harness 执行失败。",workspace,output,{"returncode":process.returncode,"source_hash":manifest["source_hash"],"attempt":attempt,**failure});self._write_result(result)
        shutil.copy2(output/"harness_result.json",output_root/"harness_result.json")
        return result

    @staticmethod
    async def _terminate_process_tree(process:asyncio.subprocess.Process)->None:
        if process.returncode is not None:return
        if os.name=="nt" and process.pid:
            try:
                killer=await asyncio.create_subprocess_exec(
                    "taskkill","/PID",str(process.pid),"/T","/F",
                    stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await asyncio.wait_for(killer.wait(),timeout=3)
            except (OSError,asyncio.TimeoutError):
                pass
        else:
            with suppress(ProcessLookupError):process.terminate()
        try:await asyncio.wait_for(process.wait(),timeout=3)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):process.kill()
            with suppress(asyncio.TimeoutError):await asyncio.wait_for(process.wait(),timeout=2)

    @staticmethod
    def _failure_summary(output:Path)->dict[str,Any]:
        result_path=output/"runner_result.json"
        if not result_path.exists():return {"error_summary":"R&D runner did not produce runner_result.json"}
        try:raw=json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:return {"error_summary":f"runner_result unreadable: {exc}"}
        pieces=[]
        if raw.get("error"):pieces.append(str(raw["error"]))
        failed=[]
        for command in raw.get("commands",[]):
            if int(command.get("returncode",0))==0:continue
            entry={"name":command.get("name"),"returncode":command.get("returncode")}
            for key in ("stderr_log","stdout_log"):
                path=Path(str(command.get(key) or ""))
                if path.is_file():
                    text=path.read_text(encoding="utf-8",errors="replace")
                    entry[key.removesuffix("_log")]=text[-12_000:]
                    if text.strip():pieces.append(text[-12_000:])
            failed.append(entry)
            if failed:break
        return {"error_summary":"\n".join(pieces)[:16_000],"failed_commands":failed,"runner_error":raw.get("error")}

    @staticmethod
    def _write_result(result:HarnessResult)->None:
        result.output_dir.mkdir(parents=True,exist_ok=True);(result.output_dir/"harness_result.json").write_text(json.dumps({"ok":result.ok,"mode":result.mode,"code":result.code,"summary":result.summary,"workspace":str(result.workspace) if result.workspace else None,"details":result.details},ensure_ascii=False,indent=2,default=str),encoding="utf-8")
