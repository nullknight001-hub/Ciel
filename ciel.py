import fcntl
import sys
import re
import difflib
import glob
import inspect
import json
import os
import subprocess
from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv
from openai import OpenAI
import requests

# Always load .env from Ciel_V3 regardless of where `ciel` is launched from
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _atomic_save_memory(entry_text):
    lock_path = MEMORY_FILE + ".lock"
    tmp_path = MEMORY_FILE + ".tmp"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            data = []
            if os.path.exists(MEMORY_FILE):
                with open(MEMORY_FILE, "r") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        data = loaded
            if entry_text not in data:
                data.append(entry_text)
            with open(tmp_path, "w") as tf:
                json.dump(data, tf, indent=2)
            os.replace(tmp_path, MEMORY_FILE)
            return len(data)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

MEMORY_FILE = os.path.join(os.path.expanduser("~"), ".ciel_memory.json")
MAX_OUTPUT_CHARS = 4000
DANGEROUS_PREFIXES = (
    "rm",
    "sudo",
    "mv",
    "dd",
    "mkfs",
    "chmod",
    "chown",
    "pacman",
    "kill",
)

# --- 1. MULTI-MODEL REGISTRY ---
MODELS = {
    "free": {
        "provider": "openrouter",
        "model": "openrouter/free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("OPEN_ROUTER_API_KEY"),
    },
    "groq": {
        "provider": "groq",
        "model": "llama-3.1-8b-instant",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": os.getenv("GROQ_API_KEY"),
    },
    "flash": {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.getenv("GEMINI_API_KEY"),
    },
    "pro": {
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.getenv("GEMINI_API_KEY"),
    },
    "flint": {
        "provider": "openrouter",
        "model": "recursive/flint-v1.0:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("OPEN_ROUTER_API_KEY"),
    },
    "particle": {
        "provider": "openrouter",
        "model": "particle-ai/particle-1:free",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("OPEN_ROUTER_API_KEY"),
    },
    "glm": {
        "provider": "openrouter",
        "model": "z-ai/glm-5.3-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("OPEN_ROUTER_API_KEY"),
    },
}

LOCAL_MODELS_PATH = os.path.join(BASE_DIR, "local_models.json")
if os.path.exists(LOCAL_MODELS_PATH):
    try:
        with open(LOCAL_MODELS_PATH, "r") as f:
            local_data = json.load(f)
            if isinstance(local_data, dict):
                MODELS.update(local_data)
    except Exception as e:
        print(f"Warning: Could not load local models: {e}")

current_alias = "auto"
current_effort = "low"  # reasoning effort for openrouter models: low | medium | high


def _msg_role(m):
    """Return the role of a message, whether it's a dict or a pydantic ChatCompletionMessage."""
    return m["role"] if isinstance(m, dict) else m.role


def _has_tool_calls(m):
    tc = m["tool_calls"] if isinstance(m, dict) else getattr(m, "tool_calls", None)
    return bool(tc)


def trim_history(messages, max_messages=8):
    if len(messages) <= max_messages + 1:
        return list(messages)
    sys_msg = next(iter(messages))
    # Find the latest user message so we never drop the active turn during multi-tool loops
    last_user_idx = 1
    for i, m in enumerate(messages):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "user":
            last_user_idx = i
    start_idx = min(len(messages) - max_messages, last_user_idx)
    window = list(messages[start_idx:])
    while window:
        first = next(iter(window))
        role = first.get("role") if isinstance(first, dict) else getattr(first, "role", "")
        if role == "user":
            break
        window.pop(0)
    return [sys_msg] + window


def get_client_and_model(alias: str):
    if alias == "auto":
        alias = "glm" if ("glm" in MODELS and os.getenv("OPEN_ROUTER_API_KEY")) else "flash"
    cfg = MODELS[alias]
    return OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"]), cfg["model"]


# --- 2. LONG-TERM MEMORY SYSTEM ---
def load_memories() -> list[str]:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_memory(fact: str) -> str:
    """Saves an important fact, user preference, project path, or rule to permanent memory."""
    before = len(load_memories())
    after = _atomic_save_memory(fact)
    if after > before:
        print(f"[🧠 Memory Saved]: {fact}")
        return f"Saved to permanent memory: {fact}"
    return "That fact is already in memory."


def delete_memory(keyword: str) -> str:
    """Removes any saved permanent memory containing the given keyword."""
    lock_path = MEMORY_FILE + ".lock"
    tmp_path = MEMORY_FILE + ".tmp"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            memories = load_memories()
            updated = [m for m in memories if keyword.lower() not in m.lower()]
            if len(updated) == len(memories):
                return f"No memory found matching '{keyword}'."
            with open(tmp_path, "w", encoding="utf-8") as tf:
                json.dump(updated, tf, indent=2, ensure_ascii=False)
            os.replace(tmp_path, MEMORY_FILE)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    print(f"[🗑 Memory Deleted]: matching '{keyword}'")
    return f"Removed memories matching '{keyword}'."


def delete_memory(keyword: str) -> str:
    """Removes any saved permanent memory containing the given keyword."""
    memories = load_memories()
    updated = [m for m in memories if keyword.lower() not in m.lower()]
    if len(updated) == len(memories):
        return f"No memory found matching '{keyword}'."
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
    print(f"[🗑️ Memory Deleted]: matching '{keyword}'")
    return f"Deleted memories matching '{keyword}'."


# --- 3. LIVE WEB SEARCH & WEBPAGE READER ---
def web_search(query: str, max_results: int = 5) -> str:
    """Searches the live web for current information, docs, or news."""
    print(f"[🌐 Searching Web]: {query}")
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No search results found."
        formatted = []
        for r in results:
            formatted.append(
                f"Title: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n"
            )
        output = "\n---\n".join(formatted)
        return output[:MAX_OUTPUT_CHARS]
    except Exception as e:
        return f"Web search error: {e}"


def read_webpage(url: str) -> str:
    """Fetches a webpage URL and returns its clean readable text."""
    print(f"[📄 Reading URL]: {url}")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n... [TRUNCATED]"
        return text or "Page had no readable text."
    except Exception as e:
        return f"Error reading webpage: {e}"


# --- 4. CODING & FILE TOOLS ---
def find_files(pattern: str, directory: str = ".") -> str:
    """Finds files matching a glob pattern (e.g., '**/*.py' or '*ciel*') inside a directory."""
    matches = glob.glob(os.path.join(directory, pattern), recursive=True)
    if not matches:
        return "No matching files found."
    return "\n".join(matches[:100])


def grep_code(pattern: str, directory: str = ".") -> str:
    """Searches for a text or regex pattern across code files in a directory."""
    print(f"[🔍 Searching Code]: '{pattern}' in {directory}")
    cmd = f"rg -n --max-count 50 {json.dumps(pattern)} {json.dumps(directory)} 2>/dev/null || grep -rn {json.dumps(pattern)} {json.dumps(directory)} | head -n 50"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    output = result.stdout.strip()
    return output[:MAX_OUTPUT_CHARS] if output else f"No matches found for '{pattern}'."


def read_file(filepath: str, start_line: int = 1, end_line: int = 250) -> str:
    """Reads a file with line numbers (default lines 1-250) so you can inspect code accurately."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(1, start_line)
        end = min(total, end_line)
        numbered = [f"{i}: {lines[i - 1]}" for i in range(start, end + 1)]
        header = f"--- {filepath} (lines {start}-{end} of {total}) ---\n"
        output = header + "".join(numbered)
        return output[:MAX_OUTPUT_CHARS]
    except Exception as e:
        return f"Error reading {filepath}: {e}"


def edit_file(filepath: str, old_text: str, new_text: str) -> str:
    """Surgically replaces an exact block of text in a file and prints a diff."""
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
        print(f"\n[📝 Code Edit in {filepath}]:\n{diff}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(updated)
        return f"Successfully edited {filepath}."
    except Exception as e:
        return f"Error editing {filepath}: {e}"


def _verify_python_edit(filepath):
    if not str(filepath).endswith(".py") or not os.path.exists(filepath):
        return ""
    res = subprocess.run(
        ["/home/ilham/Projects/Ciel_V3/venv/bin/python", "-m", "py_compile", filepath],
        capture_output=True, text=True, timeout=10
    )
    if res.returncode != 0:
        return f"\n[⚠️ SYNTAX/COMPILE ERROR — FIX IMMEDIATELY]:\n{(res.stderr or res.stdout).strip()[:1200]}"
    proj_dir = os.path.dirname(os.path.abspath(filepath))
    tests_dir = os.path.join(proj_dir, "tests")
    if os.path.isdir(tests_dir):
        t_res = subprocess.run(
            ["/home/ilham/Projects/Ciel_V3/venv/bin/python", "-m", "pytest", "-q", tests_dir],
            capture_output=True, text=True, timeout=25
        )
        if t_res.returncode != 0:
            return f"\n[⚠️ PYTEST FAILED — FIX BEFORE REPLYING]:\n{(t_res.stdout + t_res.stderr).strip()[:1500]}"
        return "\n[✅ Verified: py_compile + pytest passed]"
    return "\n[✅ Verified: py_compile passed]"

def write_file(filepath: str, content: str) -> str:
    """Creates or overwrites a file with the given content."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filepath}" + _verify_python_edit(filepath)
    except Exception as e:
        return f"Error writing {filepath}: {e}"


# --- 5. TERMINAL & BACKGROUND MUSIC TOOLS ---

DANGEROUS_PATTERNS = (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f", r"\bmkfs\b", r"\bdd\s+if=", r"\b(shutdown|reboot|poweroff)\b", r":\(\)\{")

def _confirm_command(cmd):
    if any(re.search(pat, cmd) for pat in DANGEROUS_PATTERNS):
        if not sys.stdin or not sys.stdin.isatty():
            return False
        ans = input(f"\n[⚠️ Dangerous command detected: {cmd}] Allow? [y/N]: ").strip().lower()
        return ans == "y"
    return True

def run_terminal(command: str) -> str:
    """Runs a bash command in the Linux terminal and returns stdout/stderr."""
    stripped = command.strip()
    parts = stripped.split()
    first_word = parts[0] if parts else ""

    needs_approval = (
        first_word in DANGEROUS_PREFIXES
        or " rm " in f" {stripped} "
        or "sudo " in stripped
    )

    if needs_approval:
        print(f"\n[⚠️ APPROVAL REQUIRED] Agent wants to run: {command}")
        choice = input("Allow? [y/N]: ").strip().lower()
        if choice != "y":
            return "Command denied by user."

    print(f"[⚙️ Running]: {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "(Command finished with no output)"
        if len(output) > MAX_OUTPUT_CHARS:
            return output[:MAX_OUTPUT_CHARS] + "\n... [TRUNCATED]"
        return output
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {e}"

def play_youtube_music(query: str) -> str:
    """Searches YouTube for multiple results, extracts unique video IDs, and plays a shuffled continuous playlist in mpv."""
    subprocess.run(["pkill", "-f", "mpv --no-video"], stderr=subprocess.DEVNULL)
    try:
        # Use yt-dlp to get 15 unique video IDs for the search query
        res = subprocess.run(
            ["yt-dlp", "--print", "id", f"ytsearch15:{query}"],
            capture_output=True,
            text=True,
            check=True
        )
        video_ids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not video_ids:
            return f"No results found on YouTube for: '{query}'"

        # Construct a temporary playlist file or pass URLs directly
        # mpv can take multiple URLs or we can write a temporary m3u / stream list
        import tempfile
        playlist_fd, playlist_path = tempfile.mkstemp(suffix=".m3u", prefix="ciel_playlist_")
        with os.fdopen(playlist_fd, "w", encoding="utf-8") as f:
            for vid in video_ids:
                f.write(f"https://www.youtube.com/watch?v={vid}\n")

        subprocess.Popen(
            [
                "mpv",
                "--no-video",
                "--shuffle",
                "--loop-playlist=inf",
                playlist_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Playing continuous shuffled playlist ({len(video_ids)} tracks) for: '{query}'"
    except Exception as e:
        return f"Error starting music: {e}"


def stop_music() -> str:
    """Stops any background YouTube music currently playing."""
    result = subprocess.run(["pkill", "-f", "mpv --no-video"])
    return (
        "Stopped background music."
        if result.returncode == 0
        else "No background music was playing."
    )


# --- 6. SYSTEM PROMPT & TOOL SCHEMA BUILDER ---
def build_system_prompt() -> str:
    cwd = os.getcwd()
    own_dir = os.path.dirname(os.path.abspath(__file__))

    # Load character/personality from persona.txt
    persona_path = os.path.join(own_dir, "persona.txt")
    if os.path.exists(persona_path):
        with open(persona_path, "r", encoding="utf-8") as f:
            persona_block = f.read().strip()
    else:
        persona_block = "You are CIEL, a sharp, loyal, action-first AI assistant for Ilham."

    memories = load_memories()
    memory_block = (
        "\n".join(f"- {m}" for m in memories)
        if memories
        else "- No saved memories yet."
    )

    project_context = ""
    ciel_md_path = os.path.join(cwd, "CIEL.md")
    if os.path.exists(ciel_md_path):
        with open(ciel_md_path, "r", encoding="utf-8") as f:
            project_context = f"\n[PROJECT INSTRUCTIONS FROM {ciel_md_path}]\n{f.read()[:2000]}\n"

    return f"""
{persona_block}

[SYSTEM CONTEXT]
- OS: {os.uname().sysname} | Home: {os.path.expanduser("~")}
- Current Working Directory: {cwd}
- Your Own Source Directory: {own_dir}

[CODING & TOOL WORKFLOW]
- When asked to fix, refactor, or add features to code: explore with `find_files`/`grep_code`, read with `read_file`, edit surgically with `edit_file`, and verify with `run_terminal`.
- Use `save_memory` automatically when Ilham shares personal facts, preferences, or project paths.
- Use `web_search` and `read_webpage` for live information, `play_youtube_music` for music, `launch_app` for GUI programs, and `see_screen` when asked to inspect errors or test visually.
{project_context}
[PERMANENT MEMORIES]
{memory_block}
""".strip()


def build_tool_schemas(funcs):
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    schemas, func_map = [], {}
    for fn in funcs:
        sig = inspect.signature(fn)
        props, required = {}, []
        for name, param in sig.parameters.items():
            p_type = type_map.get(param.annotation, "string")
            props[name] = {"type": p_type}
            if param.default is inspect.Parameter.empty:
                required.append(name)
        fn_def = {
            "name": fn.__name__,
            "description": (fn.__doc__ or "").strip(),
        }
        if props:
            fn_def["parameters"] = {
                "type": "object",
                "properties": props,
                "required": required,
            }
        schemas.append({"type": "function", "function": fn_def})
        func_map[fn.__name__] = fn
    return schemas, func_map

import base64
from io import BytesIO
from PIL import Image


import base64
from io import BytesIO
import time
from PIL import Image


def see_screen(
    question: str = "Describe what is on the screen and any errors visible.",
) -> str:
    """Takes a screenshot of the user's screen and analyzes it visually to read errors, UI, or test apps."""
    print("[👁️ Taking Screenshot & Looking...]")
    img_path = "/tmp/ciel_vision.png"

    if os.environ.get("WAYLAND_DISPLAY"):
        res = subprocess.run(["grim", img_path], capture_output=True)
    else:
        res = subprocess.run(["scrot", "-o", img_path], capture_output=True)

    if res.returncode != 0 or not os.path.exists(img_path):
        return "Error: Could not capture screen. Make sure 'grim' (Wayland) or 'scrot' (X11) is installed."

    try:
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, int(img.height * ratio)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

        vision_alias = (
            current_alias if current_alias in ("flash", "pro") else "flash"
        )
        v_client, v_model = get_client_and_model(vision_alias)

        vision_resp = v_client.chat.completions.create(
            model=v_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"You are CIEL's vision system. Inspect this screenshot carefully. {question}",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_img}"
                            },
                        },
                    ],
                }
            ],
            temperature=0.1,
        )
        return f"[Visual Observation]:\n{vision_resp.choices.message.content}"
    except Exception as e:
        return f"Vision error: {e}"


def launch_app(command: str) -> str:
    """Launches a GUI desktop application or windowed script detached in the background without blocking."""
    print(f"[🚀 Launching App]: {command}")
    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1.5)
        return f"Launched '{command}' in the background."
    except Exception as e:
        return f"Error launching '{command}': {e}"


ALL_TOOLS = [
    see_screen,
    save_memory,
    delete_memory,
    web_search,
    read_webpage,
    find_files,
    grep_code,
    read_file,
    edit_file,
    write_file,
    run_terminal,
    play_youtube_music,
    stop_music,
]

tool_schemas, tool_map = build_tool_schemas(ALL_TOOLS)


def choose_routed_alias(alias, user_text, step=1):
    if alias != "auto":
        return alias
    u = (user_text or "").lower().strip()
    is_complex = (
        step > 1
        or len(u) > 90
        or bool(re.search(r"\b(read|edit|write|create|patch|fix|debug|run|execute|install|search|browse|look up|screen|screenshot|play|stop|music|remember|save memory|forget|find|grep|git|check|inspect|files|folder|project|repo|audit|day1|desktop|ciel|secret|codebase|weakness|why|how|explain|compare|difference|ls|cat|mkdir|rm|cp|mv)\b|https?://|\.py\b|\.json\b", u))
    )
    if is_complex:
        return "flash"
    if "local" in MODELS:
        return "local"
    if "groq" in MODELS and os.getenv("GROQ_API_KEY"):
        return "groq"
    return "flash"



def has_any_api_key():
    return bool(
        "local" in MODELS
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GROQ_API_KEY")
        or os.getenv("OPEN_ROUTER_API_KEY")
    )

def run_turn(messages, alias="auto", effort="low", on_token=None, on_tool=None):
    if not has_any_api_key():
        warn = "No API keys found. Copy .env.example to .env and add GEMINI_API_KEY, GROQ_API_KEY, or OPEN_ROUTER_API_KEY."
        if on_token: on_token(warn)
        return warn
    steps = 0
    while steps < 8:
        steps += 1
        last_u = next((x.get("content", "") for x in reversed(messages) if isinstance(x, dict) and x.get("role") == "user"), "")
        routed = choose_routed_alias(alias, last_u, steps)
        needs_tools = (routed != "local") or (steps > 1)
        try:
            client, active_model = get_client_and_model(routed)
            create_kwargs = {
                "model": active_model,
                "messages": trim_history(messages),
                "tools": tool_schemas,
                "temperature": 0.2,
            }
            if routed in ("flash", "pro"):
                create_kwargs["reasoning_effort"] = "low" if not needs_tools else effort
            elif routed == "glm":
                create_kwargs["extra_body"] = {"reasoning_effort": "low" if not needs_tools else effort}
            if not needs_tools:
                create_kwargs["stream"] = True

            for _attempt in range(3):
                try:
                    response = client.chat.completions.create(**create_kwargs)
                    break
                except Exception as _re:
                    if "503" in str(_re) and _attempt < 2:
                        time.sleep(2)
                        continue
                    raise
        except Exception as e:
            if any(code in str(e) for code in ("402", "429", "503", "Connection", "timeout", "ConnectError")):
                if routed == "local":
                    routed = "groq" if ("groq" in MODELS and os.getenv("GROQ_API_KEY")) else "flash"
                    continue
                if "local" in MODELS and routed != "local":
                    routed = "local"
                    continue
            while len(messages) > 1 and _msg_role(messages[-1]) != "user":
                messages.pop()
            err_msg = f"[API Error]: {getattr(e, 'body', None) or repr(e)}"
            if on_token:
                on_token(err_msg)
            return err_msg

        if not needs_tools:
            chunks = []
            for chunk in response:
                delta = chunk.choices[0].delta.content if (chunk.choices and chunk.choices[0].delta) else None
                if delta:
                    chunks.append(delta)
                    if on_token:
                        on_token(delta)
            reply = "".join(chunks)
            messages.append({"role": "assistant", "content": reply})
            return reply

        msg = response.choices[0].message
        dumped = msg.model_dump(exclude_none=True)
        messages.append(dumped)

        if msg.tool_calls:
            for call in msg.tool_calls:
                fn_name = call.function.name
                args = json.loads(call.function.arguments or "{}")
                if on_tool:
                    on_tool(f"{fn_name}({args})")
                try:
                    result = tool_map[fn_name](**args) if fn_name in tool_map else f"Unknown tool: {fn_name}"
                except Exception as tool_err:
                    result = f"Tool Error calling {fn_name}: {tool_err}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })
        else:
            reply = msg.content or ""
            if on_token:
                on_token(reply)
            return reply
    return "[Max tool steps reached]"

# --- 7. MAIN AGENT LOOP ---
if __name__ == "__main__":
    client, active_model = get_client_and_model(current_alias)
    messages = [{"role": "system", "content": build_system_prompt()}]

    print(
        f"CIEL Online [{current_alias} -> {active_model}] ({len(load_memories())} memories). "
        "Type '/model' to switch AI, '/effort' for reasoning level, '/live' for voice chat, 'quit' to exit."
    )

    while True:
        user_input = input(f"\nYou ({current_alias}): ").strip()
        if not user_input:
            continue
        if user_input.lower() in ["quit", "exit"]:
            break

        # Live voice command
        if user_input.lower() == "/live":
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, "live_voice.py")])
            except KeyboardInterrupt:
                pass
            print(f"\nCIEL Online [{current_alias} -> {active_model}] (Resumed text chat)")
            continue

        # Model switcher command
        if user_input.startswith("/model"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                print(f"\nActive: {current_alias} ({active_model})")
                for k, v in MODELS.items():
                    marker = "*" if k == current_alias else " "
                    print(f" {marker} /model {k:<10} -> {v['provider']}: {v['model']}")
            else:
                choice = parts[1].strip().lower()
                if choice == "auto" or choice in MODELS:
                    current_alias = choice
                    if choice == "auto":
                        print("[🔄 Switched to auto -> smart routing (groq chat / flash tools)]")
                    else:
                        client, active_model = get_client_and_model(current_alias)
                        print(f"[🔄 Switched to {current_alias} -> {MODELS.get(current_alias, {'provider': 'auto'})['provider']}: {active_model}]")
                else:
                    print(
                        f"Unknown model alias '{choice}'. Type '/model' to see options."
                    )
            continue

        # Reasoning effort command
        if user_input.startswith("/effort"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                print(f"\nActive reasoning effort: {current_effort}")
                for level in ("low", "medium", "high"):
                    marker = "*" if level == current_effort else " "
                    print(f" {marker} /effort {level}")
            else:
                choice = parts[1].strip().lower()
                if choice in ("low", "medium", "high"):
                    current_effort = choice
                    print(f"[🧠 Reasoning effort set to '{current_effort}']")
                else:
                    print(f"Unknown effort level '{choice}'. Options: low, medium, high.")
            continue

        messages.append({"role": "user", "content": user_input})

        # Agentic tool-calling loop
        steps = 0
        while steps < 8:
            steps += 1
            try:
                _last_u = next((x.get("content", "") for x in reversed(messages) if isinstance(x, dict) and x.get("role") == "user"), "").lower()
                _needs_tools = steps > 1 or bool(re.search(r"\b(read|edit|write|create|patch|fix|debug|run|execute|install|search|browse|look up|screen|screenshot|play|stop|music|remember|save memory|forget|find|grep|git|check|inspect|files|folder|project|repo|audit|day1|desktop|secret|codebase|weakness|ls|cat|mkdir|rm|cp|mv)\b|https?://|\.py\b|\.json\b", _last_u))
                _routed_alias = current_alias
                if current_alias == "auto":
                    _routed_alias = "flash" if _needs_tools else ("local" if "local" in MODELS else "flash")
                client, active_model = get_client_and_model(_routed_alias)
                create_kwargs = {
                    "model": active_model,
                    "messages": trim_history(messages),
                    "tools": tool_schemas,
                    "temperature": 0.2,
                }
                if _routed_alias in ("flash", "pro"):
                    create_kwargs["reasoning_effort"] = "low" if not _needs_tools else current_effort
                elif _routed_alias == "glm":
                    create_kwargs["extra_body"] = {"reasoning_effort": "low" if not _needs_tools else current_effort}
                if not _needs_tools: create_kwargs["stream"] = True
                for _attempt in range(3):
                    try:
                        response = client.chat.completions.create(**create_kwargs)
                        break
                    except Exception as _re:
                        if "503" in str(_re) and _attempt < 2:
                            print("[⏳ Server busy (503), retrying in 2s...]")
                            time.sleep(2)
                            continue
                        raise
            except Exception as e:
                while len(messages) > 1 and (messages[-1].get("role") if isinstance(messages[-1], dict) else getattr(messages[-1], "role", "")) != "user":
                    messages.pop()
                if "429" in str(e):
                    print(
                        "[⏳ Rate limit hit. Switch models with '/model flint' or '/model local', or wait 20s.]"
                    )
                else:
                    print(f"\n[API Error]: {getattr(e, 'body', None) or repr(e)}")
                break

            if not _needs_tools:
                print("\nCIEL: ", end="", flush=True)
                _chunks = []
                for _chunk in response:
                    _d = _chunk.choices[0].delta.content if (_chunk.choices and _chunk.choices[0].delta) else None
                    if _d:
                        print(_d, end="", flush=True)
                        _chunks.append(_d)
                print()
                messages.append({"role": "assistant", "content": "".join(_chunks)})
                break

            msg = response.choices[0].message
            dumped = msg.model_dump(exclude_none=True)
            messages.append(dumped)

            if msg.tool_calls:
                for call in msg.tool_calls:
                    fn_name = call.function.name
                    args = json.loads(call.function.arguments or "{}")
                    try:
                        result = (
                            tool_map[fn_name](**args)
                            if fn_name in tool_map
                            else f"Unknown tool: {fn_name}"
                        )
                    except Exception as tool_err:
                        result = f"Tool Error calling {fn_name}: {tool_err}" 
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(result),
                    })
            else:
                print(f"\nCIEL: {msg.content}")
                break
