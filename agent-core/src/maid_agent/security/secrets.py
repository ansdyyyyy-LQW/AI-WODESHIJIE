from __future__ import annotations

import os,re
from pathlib import Path

from maid_agent.security.dpapi import DpapiSecretStore


class SecretStore:
    """Keyring first, then current-user Windows DPAPI; never plaintext config storage."""
    SERVICE="MaidAI"
    def __init__(self,data_dir:Path|None=None):
        root=Path(data_dir) if data_dir else Path(os.environ.get("APPDATA",Path.home()/"AppData"/"Roaming"))/"MaidAI"
        self.dpapi=DpapiSecretStore(root/"secrets"/"dpapi.json")
    @staticmethod
    def _env_name(secret_id:str)->str:return "MAIDAI_SECRET_"+re.sub(r"[^A-Za-z0-9]","_",secret_id).upper()
    def get(self,secret_id:str)->str|None:
        if not secret_id:return None
        try:
            import keyring
            value=keyring.get_password(self.SERVICE,secret_id)
            if value:return value
        except Exception:pass
        try:
            value=self.dpapi.get(secret_id)
            if value:return value
        except Exception:pass
        return os.environ.get(self._env_name(secret_id))
    def set(self,secret_id:str,value:str)->None:
        if not secret_id:raise ValueError("secret_id 不能为空")
        try:
            import keyring
            keyring.set_password(self.SERVICE,secret_id,value)
            return
        except Exception:pass
        try:self.dpapi.set(secret_id,value)
        except Exception as exc:raise RuntimeError("系统凭据存储和 Windows 加密存储均不可用，API Key 未保存。") from exc
    def delete(self,secret_id:str)->None:
        try:
            import keyring
            keyring.delete_password(self.SERVICE,secret_id)
        except Exception:pass
        try:self.dpapi.delete(secret_id)
        except Exception:pass
