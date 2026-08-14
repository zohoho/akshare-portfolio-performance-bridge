from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_tk() -> None:
    if not getattr(sys, "frozen", False):
        return
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    tcl = root / "tcl" / "tcl8.6"
    tk = root / "tcl" / "tk8.6"
    if tcl.is_dir():
        os.environ["TCL_LIBRARY"] = str(tcl)
    if tk.is_dir():
        os.environ["TK_LIBRARY"] = str(tk)
