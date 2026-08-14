from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


APP_NAME = "AkSharePPBridge"
APP_DISPLAY_NAME = "AkShare–Portfolio Performance 桥接器"
BRIDGE_VERSION = "2.0.3"
SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = 18765
TASK_NAME = "AkSharePPBridge.Service"


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def runtime_python() -> Path:
    configured = os.environ.get("AKSHARE_PP_RUNTIME_PYTHON")
    if configured:
        return Path(configured).expanduser().resolve()
    packaged = install_dir() / "runtime" / "python.exe"
    if packaged.exists():
        return packaged
    return Path(sys.executable).resolve()


def data_dir() -> Path:
    override = os.environ.get("AKSHARE_PP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


def ensure_data_dirs() -> None:
    for path in (
        data_dir(),
        cache_dir(),
        logs_dir(),
        runtimes_dir(),
        state_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)


def cache_dir() -> Path:
    return data_dir() / "cache"


def logs_dir() -> Path:
    return data_dir() / "logs"


def runtimes_dir() -> Path:
    return data_dir() / "runtimes"


def state_dir() -> Path:
    return data_dir() / "state"


def runtime_state_file() -> Path:
    return state_dir() / "runtime.json"


def update_status_file() -> Path:
    return state_dir() / "update.json"


def service_state_file() -> Path:
    return state_dir() / "service.json"


def settings_file() -> Path:
    return data_dir() / "settings.json"


def service_log_file() -> Path:
    return logs_dir() / "service.log"


def gui_log_file() -> Path:
    return logs_dir() / "desktop.log"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def active_runtime() -> dict[str, Any]:
    state = read_json(runtime_state_file(), {}) or {}
    active = state.get("active")
    if isinstance(active, dict):
        path = active.get("path")
        if path and Path(path).is_dir():
            return active
    return {"version": "bundled", "path": None}


def activate_runtime_path() -> dict[str, Any]:
    override = os.environ.get("AKSHARE_RUNTIME_OVERRIDE")
    runtime = {"version": "override", "path": override} if override else active_runtime()
    path = runtime.get("path")
    if path:
        resolved = str(Path(path).resolve())
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
    return runtime


def active_runtime_path() -> str | None:
    runtime = active_runtime()
    path = runtime.get("path")
    return str(Path(path).resolve()) if path else None


ensure_data_dirs()
