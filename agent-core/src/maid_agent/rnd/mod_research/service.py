from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx


class ModResearchService:
    """Resolve installable Modrinth versions; downloads always remain in Handoff."""

    GAME_VERSION = "1.20.1"
    LOADER = "forge"
    MODRINTH = "https://api.modrinth.com/v2"

    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    @staticmethod
    def _compatible_metadata(version: dict[str, Any]) -> bool:
        return (
            ModResearchService.GAME_VERSION in (version.get("game_versions") or [])
            and ModResearchService.LOADER in {
                str(value).lower() for value in (version.get("loaders") or [])
            }
            and any(
                str(file.get("filename", "")).lower().endswith(".jar")
                and str(file.get("url", "")).startswith(("https://", "http://"))
                for file in (version.get("files") or [])
            )
        )

    async def _json(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        response = await client.get(f"{self.MODRINTH}{path}", params=params)
        response.raise_for_status()
        return response.json()

    async def _compatible_versions(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        *,
        resolving: set[str] | None = None,
        cache: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        resolving = resolving or set()
        cache = cache if cache is not None else {}
        if project_id in cache:
            return cache[project_id]
        if project_id in resolving:
            return []
        resolving.add(project_id)
        raw_versions = await self._json(
            client,
            f"/project/{project_id}/version",
            params={
                "game_versions": json.dumps([self.GAME_VERSION]),
                "loaders": json.dumps([self.LOADER]),
            },
        )
        compatible: list[dict[str, Any]] = []
        for version in raw_versions:
            if not self._compatible_metadata(version):
                continue
            dependencies, dependencies_ok = await self._resolve_dependencies(
                client, version, resolving=resolving, cache=cache
            )
            files = [
                {
                    "filename": file.get("filename"),
                    "url": file.get("url"),
                    "primary": bool(file.get("primary", False)),
                    "size": file.get("size"),
                    "hashes": {
                        key: value
                        for key, value in (file.get("hashes") or {}).items()
                        if key in {"sha512", "sha1"}
                    },
                }
                for file in (version.get("files") or [])
                if str(file.get("filename", "")).lower().endswith(".jar")
            ]
            compatible.append(
                {
                    "project_id": str(version.get("project_id") or project_id),
                    "version_id": str(version.get("id") or ""),
                    "version_number": str(version.get("version_number") or ""),
                    "name": str(version.get("name") or ""),
                    "game_versions": list(version.get("game_versions") or []),
                    "loaders": list(version.get("loaders") or []),
                    "dependencies": dependencies,
                    "dependencies_compatible": dependencies_ok,
                    "files": files,
                    "status": "COMPATIBLE" if dependencies_ok else "INCOMPATIBLE",
                }
            )
        resolving.discard(project_id)
        cache[project_id] = compatible
        return compatible

    async def _resolve_dependencies(
        self,
        client: httpx.AsyncClient,
        version: dict[str, Any],
        *,
        resolving: set[str],
        cache: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        required_ok = True
        for dependency in version.get("dependencies") or []:
            dependency_type = str(dependency.get("dependency_type") or "required").lower()
            project_id = dependency.get("project_id")
            version_id = dependency.get("version_id")
            resolved_project = str(project_id or "")
            compatible_version: dict[str, Any] | None = None
            error = ""
            try:
                if version_id:
                    pinned = await self._json(client, f"/version/{version_id}")
                    resolved_project = str(pinned.get("project_id") or resolved_project)
                    if self._compatible_metadata(pinned):
                        if resolved_project in resolving:
                            nested, nested_ok = [], True
                        else:
                            resolving.add(resolved_project)
                            try:
                                nested, nested_ok = await self._resolve_dependencies(
                                    client, pinned, resolving=resolving, cache=cache
                                )
                            finally:
                                resolving.discard(resolved_project)
                        compatible_version = {
                            "project_id": resolved_project,
                            "version_id": str(pinned.get("id") or version_id),
                            "version_number": str(pinned.get("version_number") or ""),
                            "dependencies": nested,
                            "dependencies_compatible": nested_ok,
                        }
                        if not nested_ok:
                            compatible_version = None
                elif resolved_project:
                    candidates = await self._compatible_versions(
                        client, resolved_project, resolving=resolving, cache=cache
                    )
                    compatible_version = next(
                        (candidate for candidate in candidates if candidate["status"] == "COMPATIBLE"),
                        None,
                    )
                else:
                    error = "dependency has neither project_id nor version_id"
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                error = str(exc)
            compatible = compatible_version is not None
            if dependency_type == "required" and not compatible:
                required_ok = False
            rows.append(
                {
                    "dependency_type": dependency_type,
                    "project_id": resolved_project or None,
                    "requested_version_id": version_id,
                    "compatible": compatible,
                    "resolved_version": compatible_version,
                    "error": error,
                }
            )
        return rows, required_ok

    async def _modrinth_candidate(
        self,
        client: httpx.AsyncClient,
        hit: dict[str, Any],
        *,
        cache: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        project_id = str(hit.get("project_id") or hit.get("slug") or "")
        project = await self._json(client, f"/project/{project_id}")
        versions = await self._compatible_versions(client, project_id, cache=cache)
        installable = [version for version in versions if version["status"] == "COMPATIBLE"]
        license_data = project.get("license") or {}
        return {
            "source": "modrinth",
            "project_id": str(project.get("id") or project_id),
            "slug": project.get("slug"),
            "title": project.get("title") or hit.get("title"),
            "description": project.get("description") or hit.get("description"),
            "downloads": project.get("downloads", hit.get("downloads")),
            "project_url": f"https://modrinth.com/mod/{project.get('slug') or project_id}",
            "license": {
                "id": license_data.get("id"),
                "name": license_data.get("name"),
                "url": license_data.get("url"),
            },
            "compatible_versions": installable,
            "rejected_versions": [version for version in versions if version["status"] != "COMPATIBLE"],
            "status": "COMPATIBLE" if installable else "INCOMPATIBLE",
            "installation": "HANDOFF_ONLY",
        }

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        headers = {"User-Agent": "MaidAI-RnD/0.2"}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            modrinth: list[dict[str, Any]] = []
            github: list[dict[str, Any]] = []
            errors: list[str] = []
            cache: dict[str, list[dict[str, Any]]] = {}
            try:
                search = await self._json(
                    client,
                    "/search",
                    params={
                        "query": query,
                        "limit": min(limit, 20),
                        "facets": '[["project_type:mod"]]',
                    },
                )
                for hit in search.get("hits", [])[: min(limit, 20)]:
                    try:
                        modrinth.append(await self._modrinth_candidate(client, hit, cache=cache))
                    except (httpx.HTTPError, ValueError, TypeError) as exc:
                        modrinth.append(
                            {
                                "source": "modrinth",
                                "project_id": hit.get("project_id"),
                                "title": hit.get("title"),
                                "status": "UNVERIFIED",
                                "error": str(exc),
                                "installation": "HANDOFF_ONLY",
                            }
                        )
            except httpx.HTTPError as exc:
                errors.append(f"modrinth: {exc}")
            try:
                response = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": f"{query} minecraft mod", "per_page": min(limit, 20)},
                )
                if response.is_success:
                    for hit in response.json().get("items", []):
                        github.append(
                            {
                                "source": "github",
                                "full_name": hit.get("full_name"),
                                "description": hit.get("description"),
                                "stars": hit.get("stargazers_count"),
                                "license": (hit.get("license") or {}).get("spdx_id"),
                                "default_branch": hit.get("default_branch"),
                                "html_url": hit.get("html_url"),
                                "status": "REFERENCE_ONLY",
                                "installable": False,
                                "reason": "GitHub repository metadata alone does not verify a Forge 1.20.1 release.",
                            }
                        )
            except httpx.HTTPError as exc:
                errors.append(f"github: {exc}")
        return {
            "query": query,
            "requirements": {"game_version": self.GAME_VERSION, "loader": self.LOADER},
            "modrinth": modrinth,
            "github": github,
            "errors": errors,
        }

    async def research_to_handoff(
        self, queries: list[str], handoff_dir: Path
    ) -> list[dict[str, Any]]:
        handoff_dir = Path(handoff_dir)
        handoff_dir.mkdir(parents=True, exist_ok=True)
        results = [await self.search(query) for query in queries[:20]]
        (handoff_dir / "mod_research.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (handoff_dir / "README_不要自动安装.txt").write_text(
            "这里只有已核验或明确标记为不兼容的候选 Mod。MaidAI 不会复制到 Minecraft mods；必须由用户人工检查、安装并重启。\n",
            encoding="utf-8",
        )
        return results

    async def download_modrinth_version(
        self, project_id: str, version_id: str, handoff_dir: Path
    ) -> dict[str, Any]:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{project_id}-{version_id}")
        target_dir = Path(handoff_dir) / "mods" / safe
        target_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "MaidAI-RnD/0.2"}
        async with httpx.AsyncClient(
            timeout=120, follow_redirects=True, headers=headers
        ) as client:
            meta = await self._json(client, f"/version/{version_id}")
            if str(meta.get("project_id")) != str(project_id):
                raise ValueError("version does not belong to the requested project")
            if not self._compatible_metadata(meta):
                raise ValueError("version is not a downloadable Forge 1.20.1 JAR")
            dependencies, dependencies_ok = await self._resolve_dependencies(
                client, meta, resolving={str(project_id)}, cache={}
            )
            if not dependencies_ok:
                raise ValueError("required dependency has no compatible Forge 1.20.1 version")
            manifest = []
            verified_files: list[tuple[str, bytes, dict[str, Any]]] = []
            for file in meta.get("files", []):
                if not str(file.get("filename", "")).lower().endswith(".jar"):
                    continue
                url=str(file.get("url") or "")
                if not url.startswith(("https://","http://")):
                    raise ValueError(f"invalid download URL for {file.get('filename')}")
                response = await client.get(url)
                response.raise_for_status()
                data = response.content
                hashes = file.get("hashes") or {}
                if not hashes.get("sha512") and not hashes.get("sha1"):
                    raise ValueError(f"Modrinth did not provide a verifiable hash for {file['filename']}")
                actual_sha512 = hashlib.sha512(data).hexdigest()
                actual_sha1 = hashlib.sha1(data).hexdigest()
                if hashes.get("sha512") and hashes["sha512"].lower() != actual_sha512:
                    raise ValueError(f"sha512 mismatch for {file['filename']}")
                if hashes.get("sha1") and hashes["sha1"].lower() != actual_sha1:
                    raise ValueError(f"sha1 mismatch for {file['filename']}")
                filename=Path(str(file["filename"])).name
                entry={"filename":filename,"sha512":actual_sha512,"sha1":actual_sha1,"sha256":hashlib.sha256(data).hexdigest(),"size":len(data),"primary":file.get("primary",False)}
                verified_files.append((filename,data,entry))
            if not verified_files:raise ValueError("compatible metadata contained no downloadable JAR")
            for filename,data,entry in verified_files:
                (target_dir/filename).write_bytes(data);manifest.append(entry)
        result = {
            "project_id": project_id,
            "version_id": version_id,
            "version_number": meta.get("version_number"),
            "game_versions": meta.get("game_versions"),
            "loaders": meta.get("loaders"),
            "dependencies": dependencies,
            "destination": str(target_dir),
            "files": manifest,
            "installation": "HANDOFF_ONLY",
        }
        (target_dir / "manifest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result
