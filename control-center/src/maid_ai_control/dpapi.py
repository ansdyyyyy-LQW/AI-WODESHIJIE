from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_ENTROPY = b"MaidAI.DPAPI.v1"


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("DPAPI is available only on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    _ = (source_buffer, entropy_buffer)
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class DpapiSecretStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        entries = raw.get("entries") if isinstance(raw, dict) else None
        return {str(key): str(value) for key, value in entries.items()} if isinstance(entries, dict) else {}

    def _save(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": 1, "entries": entries}, separators=(",", ":")), encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    def get(self, secret_id: str) -> str | None:
        with self._lock:
            encoded = self._load().get(secret_id)
            if not encoded:
                return None
            return _crypt(base64.b64decode(encoded), protect=False).decode("utf-8")

    def set(self, secret_id: str, value: str) -> None:
        with self._lock:
            entries = self._load()
            entries[secret_id] = base64.b64encode(_crypt(value.encode("utf-8"), protect=True)).decode("ascii")
            self._save(entries)

    def delete(self, secret_id: str) -> None:
        with self._lock:
            entries = self._load()
            if secret_id in entries:
                del entries[secret_id]
                self._save(entries)
