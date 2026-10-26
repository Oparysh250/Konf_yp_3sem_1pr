"""Эмулятор командной строки UNIX-подобной ОС (вариант 14).

GUI на Tkinter, VFS из ZIP в памяти, CSV-логирование,
команды: ls, cd, du, cal, tail, chmod, conf-dump, exit.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import csv
import io
import shlex
import sys
import tkinter as tk
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from tkinter import scrolledtext

DEFAULT_TAIL_LINES = 10
DIR_MODE = 0o755
FILE_MODE = 0o644


# ---------- Парсер ----------

class ParseError(Exception):
    """Ошибка разбора ввода."""


def parse_line(line: str) -> tuple[str, list[str]]:
    """Разбирает строку на команду и аргументы (с поддержкой кавычек)."""
    stripped = line.strip()
    if not stripped:
        return "", []
    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        raise ParseError(f"ошибка разбора: {exc}") from exc
    return (tokens[0], tokens[1:]) if tokens else ("", [])


# ---------- VFS ----------

class VfsError(Exception):
    """Ошибка работы с VFS."""


@dataclass
class VfsNode:
    """Узел VFS: файл или директория."""
    name: str
    is_dir: bool
    data: bytes = b""
    children: dict = field(default_factory=dict)
    mode: int = 0o644

    def __post_init__(self) -> None:
        if self.is_dir:
            self.mode = DIR_MODE


class Vfs:
    """Дерево файлов и папок, загруженное из ZIP в память."""

    def __init__(self, name: str, root: VfsNode) -> None:
        self.name = name
        self.root = root
        self.cwd = "/"

    @classmethod
    def from_zip(cls, path: str, name: str | None = None) -> "Vfs":
        """Загружает VFS из ZIP-архива."""
        try:
            with zipfile.ZipFile(path, "r") as zf:
                root = VfsNode(name="", is_dir=True, mode=DIR_MODE)
                vfs_name = name or Path(path).stem
                for info in zf.infolist():
                    if info.is_dir():
                        cls._ensure_dir(root, info.filename)
                        continue
                    parts = [p for p in info.filename.split("/") if p]
                    if not parts:
                        continue
                    parent = cls._ensure_dir(root, "/".join(parts[:-1]))
                    parent.children[parts[-1]] = VfsNode(
                        name=parts[-1], is_dir=False,
                        data=zf.read(info), mode=FILE_MODE,
                    )
                return cls(vfs_name, root)
        except FileNotFoundError as exc:
            raise VfsError(f"VFS не найдена: {path}") from exc
        except zipfile.BadZipFile as exc:
            raise VfsError(f"неверный формат VFS (не ZIP): {path}") from exc

    @staticmethod
    def _ensure_dir(root: VfsNode, path: str) -> VfsNode:
        node = root
        for part in [p for p in path.split("/") if p]:
            if part not in node.children:
                node.children[part] = VfsNode(
                    name=part, is_dir=True, mode=DIR_MODE)
            child = node.children[part]
            if not child.is_dir:
                raise VfsError(f"не директория: {path}")
            node = child
        return node

    def _split(self, path: str) -> list[str]:
        base = self.cwd if not path.startswith("/") else "/"
        combined = (base.rstrip("/") + "/" + path).split("/")
        parts: list[str] = []
        for p in combined:
            if p in ("", "."):
                continue
            if p == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(p)
        return parts

    def resolve(self, path: str) -> VfsNode:
        """Возвращает узел по пути."""
        node = self.root
        for part in self._split(path):
            if not node.is_dir:
                raise VfsError(f"не директория: {part}")
            child = node.children.get(part)
            if child is None:
                raise VfsError(f"нет такого файла или каталога: {path}")
            node = child
        return node

    def listdir(self, path: str = ".") -> list[VfsNode]:
        node = self.resolve(path)
        if not node.is_dir:
            raise VfsError(f"не директория: {path}")
        return sorted(node.children.values(),
                      key=lambda n: (not n.is_dir, n.name))

    def change_dir(self, path: str) -> None:
        node = self.resolve(path)
        if not node.is_dir:
            raise VfsError(f"не директория: {path}")
        parts = self._split(path)
        self.cwd = "/" + "/".join(parts) if parts else "/"

    def read_bytes(self, path: str) -> bytes:
        node = self.resolve(path)
        if node.is_dir:
            raise VfsError(f"это директория: {path}")
        return node.data

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        raw = self.read_bytes(path)
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            return base64.b64encode(raw).decode("ascii")
        if text.startswith("base64:"):
            return base64.b64decode(text[7:]).decode(encoding, "replace")
        return text

    def size_of(self, path: str) -> int:
        node = self.resolve(path)
        if not node.is_dir:
            return len(node.data)
        total = 0
        stack = [node]
        while stack:
            cur = stack.pop()
            for child in cur.children.values():
                if child.is_dir:
                    stack.append(child)
                else:
                    total += len(child.data)
        return total

    def chmod(self, path: str, mode: int) -> None:
        self.resolve(path).mode = mode


# ---------- Команды ----------

@dataclass
class CommandResult:
    """Результат выполнения команды."""
    output: str = ""
    status: str = "ok"
    message: str = ""
    exit_requested: bool = False


def cmd_ls(vfs: Vfs, args: list[str]) -> CommandResult:
    target = args[0] if args else "."
    try:
        nodes = vfs.listdir(target)
    except VfsError as exc:
        return CommandResult(status="error", message=str(exc))
    lines = [f"{n.mode:04o} {n.name}{'/' if n.is_dir else ''}"
             for n in nodes]
    return CommandResult(output="\n".join(lines))


def cmd_cd(vfs: Vfs, args: list[str]) -> CommandResult:
    target = args[0] if args else "/"
    try:
        vfs.change_dir(target)
    except VfsError as exc:
        return CommandResult(status="error", message=str(exc))
    return CommandResult()


def cmd_du(vfs: Vfs, args: list[str]) -> CommandResult:
    target = args[0] if args else "."
    try:
        size = vfs.size_of(target)
    except VfsError as exc:
        return CommandResult(status="error", message=str(exc))
    return CommandResult(output=f"{size}\t{target}")


def cmd_cal(vfs: Vfs, args: list[str]) -> CommandResult:  # noqa: ARG001
    today = date.today()
    year, month = today.year, today.month
    try:
        if len(args) == 1:
            month = int(args[0])
        elif len(args) >= 2:
            month, year = int(args[0]), int(args[1])
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        return CommandResult(status="error",
                             message="использование: cal [месяц] [год]")
    return CommandResult(output=calendar.month(year, month).rstrip())


def cmd_tail(vfs: Vfs, args: list[str]) -> CommandResult:
    count = DEFAULT_TAIL_LINES
    if len(args) >= 2 and args[0] == "-n":
        try:
            count = int(args[1])
        except ValueError:
            return CommandResult(status="error",
                                 message="неверное число строк")
        args = args[2:]
    if not args:
        return CommandResult(status="error",
                             message="использование: tail [-n N] FILE")
    try:
        text = vfs.read_text(args[0])
    except VfsError as exc:
        return CommandResult(status="error", message=str(exc))
    lines = text.splitlines()
    return CommandResult(output="\n".join(lines[-count:]))


def cmd_chmod(vfs: Vfs, args: list[str]) -> CommandResult:
    if len(args) != 2:
        return CommandResult(status="error",
                             message="использование: chmod MODE FILE")
    mode_str, path = args
    try:
        mode = int(mode_str, 8)
    except ValueError:
        return CommandResult(status="error",
                             message=f"неверный режим: {mode_str}")
    try:
        vfs.chmod(path, mode)
    except VfsError as exc:
        return CommandResult(status="error", message=str(exc))
    return CommandResult()


COMMANDS = {
    "ls": cmd_ls, "cd": cmd_cd, "du": cmd_du,
    "cal": cmd_cal, "tail": cmd_tail, "chmod": cmd_chmod,
}


def dispatch(vfs: Vfs, name: str, args: list[str]) -> CommandResult:
    if name == "exit":
        return CommandResult(exit_requested=True)
    if name == "conf-dump":
        return CommandResult(output=f"vfs={vfs.name}\ncwd={vfs.cwd}")
    func = COMMANDS.get(name)
    if func is None:
        return CommandResult(status="error",
                             message=f"неизвестная команда: {name}")
    try:
        return func(vfs, args)
    except Exception as exc:  # noqa: BLE001
        return CommandResult(status="error",
                             message=f"внутренняя ошибка: {exc}")


# ---------- Логирование ----------

class CommandLogger:
    """Пишет события вызова команд в CSV."""

    FIELDS = ["timestamp", "command", "args", "status", "message"]

    def __init__(self, log_path: str | None) -> None:
        self._path = Path(log_path) if log_path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                with self._path.open("w", newline="",
                                     encoding="utf-8") as f:
                    csv.writer(f).writerow(self.FIELDS)

    def log(self, command: str, args: list[str],
            status: str, message: str = "") -> None:
        if self._path is None:
            return
        with self._path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                datetime.now().isoformat(timespec="seconds"),
                command, " ".join(args), status, message,
            ])


# ---------- Ядро REPL ----------

@dataclass
class ShellResponse:
    """Ответ REPL для GUI."""
    prompt_line: str
    output: str
    is_error: bool
    exit_requested: bool = False


class Shell:
    """Эмулятор: VFS + логгер + обработка строк."""

    def __init__(self, vfs: Vfs, logger: CommandLogger) -> None:
        self.vfs = vfs
        self.logger = logger

    def prompt(self) -> str:
        return f"{self.vfs.name}:{self.vfs.cwd}$ "

    def execute(self, line: str) -> ShellResponse:
        prompt_line = self.prompt() + line
        try:
            name, args = parse_line(line)
        except ParseError as exc:
            self.logger.log("<parse>", [], "error", str(exc))
            return ShellResponse(prompt_line, str(exc), is_error=True)
        if not name:
            return ShellResponse(prompt_line, "", is_error=False)
        result = dispatch(self.vfs, name, args)
        self.logger.log(name, args, result.status, result.message)
        if result.status == "error":
            return ShellResponse(prompt_line, result.message,
                                 is_error=True)
        return ShellResponse(prompt_line, result.output,
                             is_error=False,
                             exit_requested=result.exit_requested)

    def run_script(self, script_path: str) -> list[ShellResponse]:
        """Выполняет стартовый скрипт (комментарии — #)."""
        responses: list[ShellResponse] = []
        path = Path(script_path)
        if not path.exists():
            responses.append(ShellResponse(
                "", f"скрипт не найден: {script_path}", True))
            return responses
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            resp = self.execute(line)
            responses.append(resp)
            if resp.exit_requested:
                break
        return responses


# ---------- GUI ----------

class ShellApp(tk.Tk):
    """Окно эмулятора."""

    def __init__(self, shell: Shell) -> None:
        super().__init__()
        self.shell = shell
        self.title(f"Эмулятор — {shell.vfs.name}")
        self.geometry("900x600")

        self.output = scrolledtext.ScrolledText(
            self, wrap=tk.WORD, state=tk.DISABLED,
            font=("Courier New", 11),
        )
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(self, textvariable=self.entry_var,
                              font=("Courier New", 11))
        self.entry.pack(fill=tk.X, padx=8, pady=8)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.focus_set()

        self._append(
            f"{shell.prompt()}"
            "(введите команду, exit для выхода)\n", "info")

    def _append(self, text: str, tag: str = "normal") -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text + "\n", tag)
        self.output.tag_configure("error", foreground="#b00020")
        self.output.tag_configure("info", foreground="#555555")
        self.output.configure(state=tk.DISABLED)
        self.output.see(tk.END)

    def _on_enter(self, _event: object) -> None:
        line = self.entry_var.get()
        self.entry_var.set("")
        if not line.strip():
            return
        resp = self.shell.execute(line)
        self._append(resp.prompt_line)
        if resp.output:
            self._append(resp.output,
                         "error" if resp.is_error else "normal")
        if resp.exit_requested:
            self.destroy()

    def run_script(self, script_path: str) -> None:
        for resp in self.shell.run_script(script_path):
            self._append(resp.prompt_line)
            if resp.output:
                self._append(resp.output,
                             "error" if resp.is_error else "normal")


# ---------- Точка входа ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="shell-emulator",
        description="Эмулятор UNIX-оболочки (вариант 14)",
    )
    parser.add_argument("--vfs", dest="vfs_path", default=None,
                        help="путь к ZIP-архиву VFS")
    parser.add_argument("--log", dest="log_path", default=None,
                        help="путь к CSV-файлу лога")
    parser.add_argument("--script", dest="script_path", default=None,
                        help="путь к стартовому скрипту")
    return parser.parse_args(argv)


def load_vfs(vfs_path: str | None) -> Vfs:
    """Загружает VFS или создаёт пустую по умолчанию."""
    if vfs_path is None:
        return Vfs("default", VfsNode(name="", is_dir=True,
                                       mode=DIR_MODE))
    return Vfs.from_zip(vfs_path)


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    logger = CommandLogger(cfg.log_path)
    try:
        vfs = load_vfs(cfg.vfs_path)
    except VfsError as exc:
        print(f"Ошибка загрузки VFS: {exc}", file=sys.stderr)
        return 1
    shell = Shell(vfs, logger)
    app = ShellApp(shell)
    if cfg.script_path:
        app.run_script(cfg.script_path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
