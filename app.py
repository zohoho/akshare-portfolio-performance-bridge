from __future__ import annotations

import argparse
import logging
import os
import sys
import threading

from app_paths import activate_runtime_path, gui_log_file, service_log_file
from runtime_tk import configure_tk


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--start-service", action="store_true")
    parser.add_argument("--stop-service", action="store_true")
    parser.add_argument("--restart-service", action="store_true")
    parser.add_argument("--enable-startup", action="store_true")
    parser.add_argument("--disable-startup", action="store_true")
    return parser.parse_args()


def main() -> None:
    configure_tk()
    arguments = parse_args()
    runtime = activate_runtime_path()
    if runtime.get("path"):
        os.environ["PYTHONPATH"] = str(runtime["path"])

    if arguments.service:
        from service_manager import ensure_control_token

        os.environ.setdefault("AKSHARE_PP_CONTROL_TOKEN", ensure_control_token())
        if sys.stdout is None or sys.stderr is None:
            log_path = service_log_file()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_stream = log_path.open("a", encoding="utf-8", buffering=1)
            sys.stdout = log_stream
            sys.stderr = log_stream

        from bridge import main as bridge_main
        from update_manager import update_check_loop

        threading.Thread(target=update_check_loop, daemon=True).start()
        sys.argv = [sys.argv[0]]
        bridge_main()
        return

    if (
        arguments.start_service
        or arguments.stop_service
        or arguments.restart_service
        or arguments.enable_startup
        or arguments.disable_startup
    ):
        from service_manager import (
            restart_service,
            set_startup_enabled,
            start_service,
            stop_service,
        )

        if arguments.enable_startup:
            set_startup_enabled(True)
        elif arguments.disable_startup:
            set_startup_enabled(False)
        elif arguments.stop_service:
            stop_service()
        elif arguments.restart_service:
            restart_service()
        else:
            start_service()
        return

    from gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
