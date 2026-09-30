# In main.py
import ctypes
import os
from pathlib import Path
import sys
import traceback

class SafeLogWriter:
    def __init__(self, log_path: Path):
        self.log_path = log_path

    def write(self, message: str):
        if not message:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(message)
        except OSError:
            pass

    def flush(self):
        pass

log_file = Path(__file__).resolve().parent / "pbi-guard.log"
if sys.stdout is None:
    sys.stdout = SafeLogWriter(log_file)
if sys.stderr is None:
    sys.stderr = SafeLogWriter(log_file)

from src.cli import main

if __name__ == "__main__":
    try:
        main()
    except SystemExit as se:
        if se.code != 0:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"Process exited with SystemExit code: {se.code}\n")
        sys.exit(se.code)
    except Exception as e:
        crash_log = Path(__file__).resolve().parent / "crash.log"
        with open(crash_log, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(
                0,
                f"PBI Guard encountered an error:\n\n{e}\n\nDetails saved to crash.log",
                "PBI Guard Error",
                0x10,
            )
        sys.exit(1)