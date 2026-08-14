from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app_paths import (
    APP_DISPLAY_NAME,
    SERVICE_HOST,
    SERVICE_PORT,
    TASK_NAME,
    active_runtime_path,
    data_dir,
    install_dir,
    read_json,
    runtime_python,
    service_log_file,
    service_state_file,
    write_json,
)


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def app_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(install_dir() / "app.py"), *arguments]


def service_command(*, windowed: bool = False) -> list[str]:
    """Run the quote service with the private, updateable Python runtime."""
    python = runtime_python()
    if windowed:
        candidate = python.with_name("pythonw.exe")
        if candidate.exists():
            python = candidate
    return [str(python), str(install_dir() / "app.py"), "--service"]


def _request_json(
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    timeout: float = 3,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://{SERVICE_HOST}:{SERVICE_PORT}{path}",
        method=method,
        headers={"X-AkShare-Bridge-Token": token or ""},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_health(timeout: float = 2) -> dict[str, Any] | None:
    try:
        payload = _request_json("/health", timeout=timeout)
        if payload.get("service") == "AkShare Portfolio Performance Bridge":
            return payload
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return None


def wait_for_health(expected_version: str | None = None, timeout: float = 25) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = get_health(timeout=1.5)
        if health and (expected_version is None or health.get("akshare_version") == expected_version):
            return health
        time.sleep(0.5)
    return None


def ensure_control_token() -> str:
    state = read_json(service_state_file(), {}) or {}
    token = state.get("control_token")
    if not token:
        token = secrets.token_urlsafe(32)
        state["control_token"] = token
        write_json(service_state_file(), state)
    return str(token)


def _listening_pid() -> int | None:
    try:
        result = subprocess.run(
            ["netstat.exe", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pattern = re.compile(
        rf"^\s*TCP\s+{re.escape(SERVICE_HOST)}:{SERVICE_PORT}\s+\S+\s+LISTENING\s+(\d+)\s*$",
        re.IGNORECASE,
    )
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def stop_service(*, allow_legacy_takeover: bool = True) -> bool:
    health = get_health()
    if not health:
        return True

    state = read_json(service_state_file(), {}) or {}
    token = state.get("control_token")
    if token:
        try:
            _request_json("/control/shutdown", method="POST", token=str(token), timeout=4)
        except (OSError, ValueError, urllib.error.URLError):
            pass
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not get_health(timeout=0.5):
                return True
            time.sleep(0.25)

    if allow_legacy_takeover and health.get("service") == "AkShare Portfolio Performance Bridge":
        pid = _listening_pid()
        if pid:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=8,
                check=False,
            )
            time.sleep(0.5)
    return get_health(timeout=1) is None


def start_service(*, expected_akshare_version: str | None = None) -> dict[str, Any]:
    health = get_health()
    if health:
        return health

    token = ensure_control_token()
    log_path = service_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["AKSHARE_PP_DATA_DIR"] = str(data_dir())
    environment["AKSHARE_PP_CONTROL_TOKEN"] = token
    active_path = active_runtime_path()
    if active_path:
        environment["PYTHONPATH"] = active_path

    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            service_command(),
            cwd=str(install_dir()),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
    state = read_json(service_state_file(), {}) or {}
    state.update({"pid": process.pid, "control_token": token, "started_by": APP_DISPLAY_NAME})
    write_json(service_state_file(), state)

    health = wait_for_health(expected_akshare_version, timeout=30)
    if not health:
        raise RuntimeError(f"报价服务启动失败，请查看日志：{log_path}")
    return health


def restart_service(*, expected_akshare_version: str | None = None) -> dict[str, Any]:
    if not stop_service():
        raise RuntimeError("无法停止现有报价服务")
    return start_service(expected_akshare_version=expected_akshare_version)


def startup_task_command() -> str:
    command = service_command(windowed=True)
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def startup_enabled() -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def set_startup_enabled(enabled: bool) -> None:
    if enabled:
        command = [
            "schtasks.exe",
            "/Create",
            "/TN",
            TASK_NAME,
            "/TR",
            startup_task_command(),
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ]
    else:
        if not startup_enabled():
            return
        command = ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 and not (not enabled and "cannot find" in result.stderr.lower()):
        detail = (result.stdout or result.stderr).strip()
        raise RuntimeError(detail or "无法更新 Windows 登录启动任务")


def open_path(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # type: ignore[attr-defined]
