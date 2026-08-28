from __future__ import annotations
from typing import Any


def resolve_references(value:Any,*,previous:dict[str,Any]|None=None,context:dict[str,Any]|None=None)->Any:
    root={"previous":previous or {},**(context or {})}
    def resolve(item:Any)->Any:
        if isinstance(item,str) and item.startswith("$"):
            current:Any=root
            for part in item[1:].split("."):
                if isinstance(current,dict) and part in current:current=current[part]
                elif isinstance(current,list) and part.isdigit() and int(part)<len(current):current=current[int(part)]
                else:raise ValueError(f"unresolved reference: {item}")
            return current
        if isinstance(item,dict):return {k:resolve(v) for k,v in item.items()}
        if isinstance(item,list):return [resolve(v) for v in item]
        return item
    return resolve(value)
