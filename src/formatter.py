import os
import sys
from typing import Optional


class TerminalFormatter:
    def __init__(self, enable_color: Optional[bool] = None):
        if enable_color is not None:
            self.enabled = enable_color
        else:
            is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
            self.enabled = is_tty and "NO_COLOR" not in os.environ

        self.GREEN = "\033[92m" if self.enabled else ""
        self.RED = "\033[91m" if self.enabled else ""
        self.YELLOW = "\033[93m" if self.enabled else ""
        self.BOLD = "\033[1m" if self.enabled else ""
        self.DIM = "\033[2m" if self.enabled else ""
        self.RESET = "\033[0m" if self.enabled else ""

    def pass_tag(self) -> str:
        return f"[{self.GREEN}PASS{self.RESET}]"

    def fail_tag(self) -> str:
        return f"[{self.RED}FAIL{self.RESET}]"

    def error_tag(self) -> str:
        return f"[{self.YELLOW}ERR {self.RESET}]"

    def print_diff(self, label: str, content: str, color: str) -> None:
        lines = content.splitlines()
        if len(lines) == 1:
            print(f"       {self.BOLD}{label}:{self.RESET} {color}{lines[0]}{self.RESET}")
        else:
            print(f"       {self.BOLD}{label}:{self.RESET}")
            for l in lines:
                print(f"         {color}│ {l}{self.RESET}")


fmt = TerminalFormatter()