from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app_paths import (
    BRIDGE_VERSION,
    active_runtime,
    data_dir,
    read_json,
    runtime_python,
    runtime_state_file,
    runtimes_dir,
    update_status_file,
    write_json,
)
from service_manager import restart_service


PYPI_URL = "https://pypi.org/pypi/akshare/json"
CHECK_INTERVAL = timedelta(hours=24)
STABLE_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")


def parse_version(value: str) -> tuple[int, ...]:
    pieces: list[int] = []
    for part in value.split("."):
        number = "".join(character for character in part if character.isdigit())
        pieces.append(int(number or 0))
    return tuple(pieces)


def latest_stable_release(payload: dict[str, Any]) -> str:
    candidates: list[str] = []
    for version, files in payload.get("releases", {}).items():
        if not STABLE_VERSION_PATTERN.fullmatch(str(version)):
            continue
        if files and any(not item.get("yanked", False) for item in files):
            candidates.append(str(version))
    if not candidates:
        raise RuntimeError("PyPI 没有可安装的 AkShare 稳定版本")
    return max(candidates, key=parse_version)


def installed_akshare_version() -> str:
    code = (
        "import importlib.metadata as m; "
        "print(m.version('akshare'))"
    )
    environment = os.environ.copy()
    active = active_runtime()
    if active.get("path"):
        environment["PYTHONPATH"] = str(active["path"])
    result = subprocess.run(
        [str(runtime_python()), "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        creationflags=0x08000000 if os.name == "nt" else 0,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        return str(active.get("version", "unknown"))
    return result.stdout.strip() or str(active.get("version", "unknown"))


def check_latest_version(*, force: bool = False, timeout: float = 12) -> dict[str, Any]:
    current = installed_akshare_version()
    cached = read_json(update_status_file(), {}) or {}
    checked_at = cached.get("checked_at")
    if checked_at and not force:
        try:
            checked = datetime.fromisoformat(checked_at)
            if datetime.now(timezone.utc) - checked < CHECK_INTERVAL:
                return {**cached, "current_version": current, "cached": True}
        except ValueError:
            pass

    request = urllib.request.Request(
        PYPI_URL,
        headers={"Accept": "application/json", "User-Agent": f"AkSharePPBridge/{BRIDGE_VERSION}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = latest_stable_release(payload)
        result = {
            "current_version": current,
            "latest_version": latest,
            "update_available": parse_version(latest) > parse_version(current),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": None,
            "cached": False,
        }
    except Exception as exc:
        result = {
            **cached,
            "current_version": current,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": str(exc),
            "cached": False,
        }
    write_json(update_status_file(), result)
    return result


def _pip_command(target: Path, version: str) -> list[str]:
    return [
        str(runtime_python()),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-compile",
        "--target",
        str(target),
        f"akshare=={version}",
    ]


def _validate_runtime(runtime_path: Path, version: str, progress: Callable[[str], None]) -> None:
    environment = os.environ.copy()
    environment["AKSHARE_RUNTIME_OVERRIDE"] = str(runtime_path)
    environment["AKSHARE_PP_DATA_DIR"] = str(data_dir())
    environment["PYTHONPATH"] = str(runtime_path)

    smoke_code = (
        "import akshare as ak;"
        "import importlib.metadata as m;"
        "import pandas as pd;"
        f"assert m.version('akshare')=='{version}';"
        "f=ak.fund_open_fund_info_em(symbol='000305',indicator='单位净值走势');"
        "assert not f.empty and len(f.columns)>=2;"
        "h=ak.stock_zh_a_hist_tx(symbol='sz300308',start_date='20260101',end_date='20500101',adjust='',timeout=25);"
        "assert not h.empty and {'date','close'}.issubset(h.columns);"
        "s=ak.stock_zh_a_spot_tx();"
        "assert not s[s['code'].astype(str).eq('sz300308')].empty;"
        f"print('{version}')"
    )
    progress("正在验证 000305 基金和 300308.SZ 股票接口…")
    result = subprocess.run(
        [str(runtime_python()), "-c", smoke_code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "新版接口验证失败")


def _remove_older_runtimes(keep: set[str]) -> None:
    for path in runtimes_dir().iterdir():
        if path.is_dir() and path.name not in keep and not path.name.startswith(".installing-"):
            shutil.rmtree(path, ignore_errors=True)


def install_version(version: str, progress: Callable[[str], None] = lambda _message: None) -> dict[str, Any]:
    version = version.strip()
    if not version:
        raise ValueError("缺少 AkShare 版本号")

    current_state = read_json(runtime_state_file(), {}) or {}
    current_active = active_runtime()
    current_active = {
        "version": installed_akshare_version()
        if not current_active.get("path")
        else current_active.get("version"),
        "path": current_active.get("path"),
    }
    target = runtimes_dir() / version
    staging = runtimes_dir() / f".installing-{version}-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        progress(f"正在下载并安装 AkShare {version}…")
        result = subprocess.run(
            _pip_command(staging, version),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "pip 安装失败")

        _validate_runtime(staging, version, progress)
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)

        next_state = {
            "active": {"version": version, "path": str(target)},
            "previous": current_active,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        write_json(runtime_state_file(), next_state)

        progress("新版验证通过，正在重启报价服务…")
        try:
            health = restart_service(expected_akshare_version=version)
        except Exception:
            restored_state = current_state or {"active": current_active}
            write_json(runtime_state_file(), restored_state)
            restart_service()
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise

        previous_version = str(current_active.get("version", installed_akshare_version()))
        _remove_older_runtimes({version, previous_version})
        status = check_latest_version(force=True)
        return {"health": health, "update": status}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def rollback(progress: Callable[[str], None] = lambda _message: None) -> dict[str, Any]:
    state = read_json(runtime_state_file(), {}) or {}
    previous = state.get("previous")
    if not isinstance(previous, dict):
        raise RuntimeError("没有可回滚的 AkShare 版本")
    previous_path = previous.get("path")
    if previous_path and not Path(previous_path).is_dir():
        raise RuntimeError("上一版本运行目录已不存在")

    active = active_runtime()
    write_json(
        runtime_state_file(),
        {
            "active": previous,
            "previous": active,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )
    progress(f"正在回滚到 AkShare {previous.get('version', 'bundled')}…")
    try:
        return restart_service(expected_akshare_version=None if not previous_path else str(previous["version"]))
    except Exception:
        write_json(runtime_state_file(), state)
        restart_service()
        raise


def maybe_check_in_background() -> None:
    try:
        check_latest_version(force=False)
    except Exception:
        pass


def update_check_loop() -> None:
    """Check at startup, then wake hourly; cached checks enforce 24 hours."""
    while True:
        maybe_check_in_background()
        time.sleep(60 * 60)
