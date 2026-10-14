"""Shared test helpers (cross-platform exec strings, etc.)."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def py_c(code: str) -> str:
    """``sys.executable -c <code>`` quoted for the executor's ``shlex.split`` rules."""
    exe = str(Path(sys.executable).resolve())
    if os.name == "nt":
        exe_q = f'"{exe}"' if (" " in exe or "\t" in exe) else exe
        code_q = '"' + code.replace("\\", "\\\\").replace('"', r"\"") + '"'
        return f"{exe_q} -c {code_q}"
    return f"{shlex.quote(exe)} -c {shlex.quote(code)}"


def py_print(message: str) -> str:
    """``hook.exec`` that prints ``message`` (no shell builtins)."""
    return py_c(f"print({message!r})")


def py_exit(code: int) -> str:
    """``hook.exec`` that exits with ``code``."""
    return py_c(f"raise SystemExit({int(code)})")
