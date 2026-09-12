"""Browse existing work directories with the same boundary as task creation."""

import os
from pathlib import Path
import re

from fastapi import HTTPException

from server.config import settings

WORKSPACE_ROOT = settings.workspace_root


def work_directory(value):
    try:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_relative_to(WORKSPACE_ROOT.resolve()):
            raise HTTPException(400, f"请选择 {WORKSPACE_ROOT} 下已存在的工作目录")
        if not path.is_dir():
            raise HTTPException(400, "此路径不是文件夹")
        if not os.access(path, os.R_OK | os.X_OK):
            raise HTTPException(403, "当前用户没有权限读取此文件夹")
        return path
    except (OSError, ValueError, RuntimeError) as e:
        raise HTTPException(400, "文件夹不存在或无法访问，请检查路径") from e


def natural_key(name):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def browse(value, hidden=False, query="", offset=0, limit=100):
    path = work_directory(value)
    entries = []
    try:
        with os.scandir(path) as children:
            for child in children:
                if (
                    not hidden and child.name.startswith(".")
                ) or query.casefold() not in child.name.casefold():
                    continue
                try:
                    if not child.is_dir():
                        continue
                    resolved = Path(child.path).resolve()
                    if not resolved.is_relative_to(WORKSPACE_ROOT.resolve()):
                        continue
                    entries.append(
                        {
                            "name": child.name,
                            "path": child.path,
                            "accessible": os.access(resolved, os.R_OK | os.X_OK),
                        }
                    )
                except (OSError, RuntimeError):
                    continue
    except OSError as e:
        raise HTTPException(403, "无法读取此文件夹，请选择其他目录") from e
    entries.sort(key=lambda item: natural_key(item["name"]))
    root = WORKSPACE_ROOT.resolve()
    breadcrumbs = [{"name": root.name, "path": str(root)}]
    parent = root
    for part in path.relative_to(root).parts:
        parent = parent / part
        breadcrumbs.append({"name": part, "path": str(parent)})
    return {
        "path": str(path),
        "parent": str(path.parent) if path != root else None,
        "breadcrumbs": breadcrumbs,
        "entries": entries[offset : offset + limit],
        "total": len(entries),
        "offset": offset,
        "limit": limit,
    }
