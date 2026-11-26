import difflib
import glob
import json
import os
import subprocess
import sys
from config import MAX_OUTPUT_CHARS

SENSITIVE_PATH_PARTS = (".env", ".ssh", ".gnupg", ".aws", ".netrc", "id_rsa", "id_ed25519")

import shutil, time
from pathlib import Path

CHECKPOINT_ROOT = Path("/home/ilham/Projects/Ciel_V3/.ciel_checkpoints")
_LAST_CHECKPOINT: Path | None = None



def _is_sensitive_path(filepath: str) -> bool:
    norm = os.path.abspath(os.path.expanduser(filepath)).replace("\\", "/")
    parts = norm.split("/")
    return any(part in SENSITIVE_PATH_PARTS or part.startswith(".env") for part in parts)


def _verify_python_edit(filepath: str) -> str:
    if not str(filepath).endswith(".py") or not os.path.exists(filepath):
        return ""
    res = subprocess.run(
        [sys.executable, "-m", "py_compile", filepath],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if res.returncode != 0:
        err = (res.stderr or res.stdout).strip()[:1200]
        return f"\n[SYNTAX/COMPILE ERROR — FIX IMMEDIATELY]:\n{err}"

    proj_dir = os.path.dirname(os.path.abspath(filepath))
    tests_dir = os.path.join(proj_dir, "tests")
    if os.path.isdir(tests_dir):
        t_res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", tests_dir],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if t_res.returncode != 0:
            out = (t_res.stdout + t_res.stderr).strip()[:1500]
            return f"\n[PYTEST FAILED — FIX BEFORE REPLYING]:\n{out}"
        return "\n[Verified: py_compile + pytest passed]"
    return "\n[Verified: py_compile passed]"


def find_files(pattern: str, directory: str = ".") -> str:
    """Finds files matching a glob pattern (e.g., '**/*.py' or '*ciel*') inside a directory."""
    matches = glob.glob(os.path.join(directory, pattern), recursive=True)
    return "\n".join(matches[:100]) if matches else "No matching files found."


def grep_code(pattern: str, directory: str = ".") -> str:
    """Searches for a text or regex pattern across code files in a directory."""
    print(f"[Searching Code]: '{pattern}' in {directory}")
    cmd = (
        f"rg -n --max-count 50 {json.dumps(pattern)} {json.dumps(directory)} 2>/dev/null "
        f"|| grep -rn --exclude='.env*' {json.dumps(pattern)} {json.dumps(directory)} | head -n 50"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    output = result.stdout.strip()
    return output[:MAX_OUTPUT_CHARS] if output else f"No matches found for '{pattern}'."


def read_file(filepath: str, start_line: int = 1, end_line: int = 250) -> str:
    """Reads a file with line numbers (default lines 1-250) so you can inspect code accurately."""
    if _is_sensitive_path(filepath):
        return f"Access denied: '{filepath}' is protected."
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(1, start_line)
        end = min(total, end_line)
        numbered = [f"{i}: {lines[i - 1]}" for i in range(start, end + 1)]
        header = f"--- {filepath} (lines {start}-{end} of {total}) ---\n"
        return (header + "".join(numbered))[:MAX_OUTPUT_CHARS]
    except Exception as e:
        return f"Error reading {filepath}: {e}"


def edit_file(filepath: str, old_text: str, new_text: str) -> str:
    """Surgically replaces an exact block of text in a file and prints a diff."""
    if _is_sensitive_path(filepath):
        return f"Access denied: '{filepath}' is protected."
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if old_text not in content:
            return (
                "Error: `old_text` not found in file. Call `read_file` first to get "
                "the exact indentation and characters."
            )
        if content.count(old_text) > 1:
            return "Error: `old_text` matched multiple places. Include more surrounding lines to make it unique."

        updated = content.replace(old_text, new_text, 1)
        diff = "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{filepath}",
                tofile=f"b/{filepath}",
                n=2,
            )
        )
        print(f"\n[Code Edit in {filepath}]:\n{diff}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
        return f"Successfully edited {filepath}." + _verify_python_edit(filepath)
    except Exception as e:
        return f"Error editing {filepath}: {e}"


def write_file(filepath: str, content: str) -> str:
    """Creates or overwrites a file with the given content."""
    if _is_sensitive_path(filepath):
        return f"Access denied: '{filepath}' is protected."
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filepath}." + _verify_python_edit(filepath)
    except Exception as e:
        return f"Error writing {filepath}: {e}"


# === CIEL Checkpoints & Undo ===
import json as _cp_json
import shutil as _cp_shutil
import time as _cp_time
from datetime import datetime as _cp_dt
from pathlib import Path as _cp_Path

CHECKPOINT_ROOT = _cp_Path("/home/ilham/Projects/Ciel_V3/.ciel_checkpoints")


def checkpoint_file(target_path: str, action: str = "edit", **_kwargs) -> str | None:
    if not target_path:
        return None
    src = _cp_Path(target_path).expanduser().resolve()
    if not src.is_file():
        return None
    stamp = _cp_dt.now().strftime("%Y%m%d_%H%M%S_%f")
    snap_dir = CHECKPOINT_ROOT / stamp
    snap_dir.mkdir(parents=True, exist_ok=True)
    saved_file = snap_dir / src.name
    _cp_shutil.copy2(src, saved_file)
    meta = {
        "original_path": str(src),
        "saved_name": src.name,
        "action": action,
        "timestamp": _cp_time.time(),
    }
    (snap_dir / "manifest.json").write_text(_cp_json.dumps(meta, indent=2), encoding="utf-8")
    snaps = sorted(p for p in CHECKPOINT_ROOT.iterdir() if p.is_dir())
    for old in snaps[:-40]:
        _cp_shutil.rmtree(old, ignore_errors=True)
    return str(snap_dir)


def undo_last_edit() -> str:
    if not CHECKPOINT_ROOT.exists():
        return "No checkpoints found."
    snaps = sorted(p for p in CHECKPOINT_ROOT.iterdir() if p.is_dir())
    while snaps:
        snap_dir = snaps.pop()
        manifest = snap_dir / "manifest.json"
        if not manifest.exists():
            _cp_shutil.rmtree(snap_dir, ignore_errors=True)
            continue
        meta = _cp_json.loads(manifest.read_text(encoding="utf-8"))
        orig = _cp_Path(meta["original_path"])
        saved = snap_dir / meta["saved_name"]
        if saved.is_file():
            orig.parent.mkdir(parents=True, exist_ok=True)
            _cp_shutil.copy2(saved, orig)
            _cp_shutil.rmtree(snap_dir, ignore_errors=True)
            return f"Restored {orig} (from checkpoint {snap_dir.name})"
        _cp_shutil.rmtree(snap_dir, ignore_errors=True)
    return "No checkpoints found."


def _wrap_mutating_file_fn(fn):
    def _wrapped(*args, **kwargs):
        target = (
            kwargs.get("path")
            or kwargs.get("file_path")
            or kwargs.get("filepath")
            or (args[0] if args and isinstance(args[0], str) else None)
        )
        if target:
            checkpoint_file(target, action=fn.__name__)
        return fn(*args, **kwargs)
    _wrapped.__name__ = fn.__name__
    _wrapped.__doc__ = fn.__doc__
    return _wrapped


for _fn_name in ("edit_file", "write_file", "patch_file", "replace_in_file", "delete_file"):
    if _fn_name in globals() and callable(globals()[_fn_name]):
        globals()[_fn_name] = _wrap_mutating_file_fn(globals()[_fn_name])


# === CIEL AST Repo Map ===
import ast as _rm_ast
from pathlib import Path as _rm_Path

_RM_SKIP_DIRS = {
    "venv", ".venv", ".git", "__pycache__", ".ciel_checkpoints",
    "node_modules", "bin", ".mypy_cache", ".pytest_cache", "dist", "build",
}


def _format_arg_names(args_node: _rm_ast.arguments) -> str:
    names = [a.arg for a in args_node.posonlyargs + args_node.args]
    if args_node.vararg:
        names.append(f"*{args_node.vararg.arg}")
    names.extend(a.arg for a in args_node.kwonlyargs)
    if args_node.kwarg:
        names.append(f"**{args_node.kwarg.arg}")
    return ", ".join(names)


def repo_map(directory: str = "/home/ilham/Projects/Ciel_V3", max_chars: int = 3500) -> str:
    """Return a compact AST symbol map (classes, functions, line numbers) for Python files in a directory."""
    root = _rm_Path(directory).expanduser().resolve()
    if not root.exists():
        return f"Directory not found: {directory}"

    py_files = []
    for p in sorted(root.rglob("*.py")):
        if any(part in _RM_SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        py_files.append(p)

    if not py_files:
        return f"No Python files found in {root}"

    sections = []
    for pf in py_files:
        rel = pf.relative_to(root).as_posix()
        try:
            source = pf.read_text(encoding="utf-8")
            tree = _rm_ast.parse(source, filename=rel)
        except Exception as exc:
            sections.append(f"{rel} (parse error: {exc})")
            continue

        line_count = len(source.splitlines())
        entries = []
        for node in tree.body:
            if isinstance(node, _rm_ast.ClassDef):
                bases = ", ".join(_rm_ast.unparse(b) for b in node.bases) if node.bases else ""
                sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
                entries.append(f"  {sig} [L{node.lineno}-L{node.end_lineno}]")
                for sub in node.body:
                    if isinstance(sub, (_rm_ast.FunctionDef, _rm_ast.AsyncFunctionDef)):
                        prefix = "async def" if isinstance(sub, _rm_ast.AsyncFunctionDef) else "def"
                        entries.append(f"    {prefix} {sub.name}({_format_arg_names(sub.args)}) [L{sub.lineno}]")
            elif isinstance(node, (_rm_ast.FunctionDef, _rm_ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, _rm_ast.AsyncFunctionDef) else "def"
                entries.append(f"  {prefix} {node.name}({_format_arg_names(node.args)}) [L{node.lineno}-L{node.end_lineno}]")

        header = f"{rel} ({line_count}L)"
        sections.append(header + ("\n" + "\n".join(entries) if entries else ""))

    output = "\n".join(sections)
    if len(output) > max_chars:
        output = output[:max_chars].rsplit("\n", 1)[0] + "\n... [truncated; pass a subdirectory for more]"
    return output
