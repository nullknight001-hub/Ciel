import json
import os
import re
import time
from config import (
    BASE_DIR,
    MODELS,
    alias_available,
    choose_routed_alias,
    get_client_and_model,
    has_any_api_key,
    resolve_default_smart_alias,
)
from memory import load_memories
from tools import tool_map, tool_schemas
from tools.skills import list_skills

def _base_build_system_prompt() -> str:
    skills_list = ", ".join(list_skills()) or "none"
    cwd = os.getcwd()
    persona_path = os.path.join(BASE_DIR, "persona.txt")
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
- Your Own Source Directory: {BASE_DIR}

[CODING & TOOL WORKFLOW]
- Available playbooks in `skills/`: {skills_list}. When a request matches one (e.g., audio/network fixes, media conversion, gallery search), call `load_skill(name)` first and follow its safe steps.
- When asked to fix, refactor, or add features to code: explore with `find_files`/`grep_code`, read with `read_file`, edit surgically with `edit_file`, and verify with `run_terminal`.
- Use `save_memory` automatically when Ilham shares personal facts, preferences, or project paths.
- Use `web_search` and `read_webpage` for live information, `play_youtube_music`/`skip_music`/`stop_music` for music, `launch_app` for GUI programs, and `see_screen` when asked to inspect errors or test visually.
{project_context}
[PERMANENT MEMORIES]
{memory_block}
""".strip()


def _msg_role(m) -> str:
    return m["role"] if isinstance(m, dict) else getattr(m, "role", "")


def trim_history(messages: list, max_messages: int = 8) -> list:
    if len(messages) <= max_messages + 1:
        return list(messages)
    sys_msg = messages[0]
    last_user_idx = 1
    for i, m in enumerate(messages):
        if _msg_role(m) == "user":
            last_user_idx = i
    start_idx = min(len(messages) - max_messages, last_user_idx)
    window = list(messages[start_idx:])
    while window and _msg_role(window[0]) != "user":
        window.pop(0)
    return [sys_msg] + window


def _parse_raw_tool_json(content: str):
    if not content:
        return None
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        name = obj.get("name")
        if isinstance(name, str) and name in tool_map:
            args = obj.get("arguments") or {}
            if isinstance(args, dict):
                return name, args
    except Exception:
        pass
    return None


def _execute_raw_tool(parsed, messages: list, on_tool=None):
    name, args = parsed
    if on_tool:
        on_tool(f"{name}({args})")
    try:
        result = tool_map[name](**args)
    except Exception as error:
        result = f"Tool Error calling {name}: {error}"
    messages.append({"role": "assistant", "content": json.dumps({"name": name, "arguments": args})})
    messages.append({"role": "user", "content": f"[Tool {name} output]: {str(result)[:2500]}\nConfirm concisely."})
    return result

PLAN_MODE: bool = False

READ_ONLY_TOOLS = {
    "read_file",
    "list_files",
    "list_dir",
    "find_files",
    "grep_search",
    "search_files",
    "repo_map",
    "git_status",
    "git_diff",
    "git_log",
    "load_skill",
    "web_search",
    "fetch_url",
    "see_screen",
    "get_desktop_context",
    "mpv_control",
}

MUTATING_TOOLS = {
    "edit_file",
    "write_file",
    "patch_file",
    "replace_in_file",
    "delete_file",
    "move_file",
    "run_terminal",
    "bash",
}

_DANGEROUS_CMD_RE = re.compile(
    r"(\brm\s+-[rf]{1,2}\s+(/|~|\$HOME|/home/ilham/?)(\s|$)|\bmkfs\b|\bdd\s+if=|:[(][)][{]:\|:&[}];:)"
)


def set_plan_mode(enabled: bool) -> bool:
    global PLAN_MODE
    PLAN_MODE = bool(enabled)
    return PLAN_MODE


def toggle_plan_mode() -> bool:
    return set_plan_mode(not PLAN_MODE)


def is_plan_mode() -> bool:
    return PLAN_MODE


def _filter_tools_for_plan(schemas: list) -> list:
    filtered = []
    for item in schemas:
        fn_name = (item.get("function") or {}).get("name", "")
        if fn_name in READ_ONLY_TOOLS:
            filtered.append(item)
    return filtered


def _pre_tool_hook(fn_name: str, args: dict, plan_active: bool) -> str | None:
    if plan_active and (fn_name in MUTATING_TOOLS or fn_name not in READ_ONLY_TOOLS):
        return (
            f"[Plan Mode Active]: Blocked '{fn_name}' because Plan Mode is read-only. "
            "Finish presenting your numbered plan, or tell the user to run `/plan` (or press Shift+Tab) to unlock edits."
        )
    if fn_name in ("run_terminal", "bash"):
        cmd = str(args.get("command") or args.get("cmd") or "")
        if _DANGEROUS_CMD_RE.search(cmd):
            return f"[PreToolUse Safety Block]: Refused potentially destructive command: {cmd}"
    if fn_name in ("edit_file", "write_file", "patch_file", "replace_in_file", "delete_file"):
        target = args.get("path") or args.get("file_path") or args.get("filepath")
        if target:
            try:
                from tools.files import checkpoint_file
                checkpoint_file(str(target), action=fn_name)
            except Exception:
                pass
    return None


def run_turn(
    messages: list,
    alias: str = "auto",
    effort: str = "low",
    on_token=None,
    on_tool=None,
    plan_mode: bool | None = None,
) -> str:
    if not has_any_api_key():
        warn = "No API keys found. Add GEMINI_API_KEY, GROQ_API_KEY, or OPEN_ROUTER_API_KEY to .env."
        if on_token:
            on_token(warn)
        return warn

    active_plan = PLAN_MODE if plan_mode is None else bool(plan_mode)
    steps = 0
    failed_aliases: set[str] = set()
    fallback_chain = ("qwen27b", "gpt120b", "flash", "north-code", "flint", "free")

    while steps < 8:
        steps += 1
        last_u = next(
            (x.get("content", "") for x in reversed(messages) if isinstance(x, dict) and x.get("role") == "user"),
            "",
        )
        routed = choose_routed_alias(alias, last_u, steps)
        if routed in failed_aliases:
            routed = next(
                (c for c in fallback_chain if c not in failed_aliases and alias_available(c)),
                routed,
            )

        attach_tools = (
            active_plan
            or alias != "auto"
            or steps > 1
            or len((last_u or "").strip()) > 120
            or bool(
                re.search(
                    r"\b(read|edit|write|create|patch|fix|debug|run|execute|install|search|browse|"
                    r"look up|screen|screenshot|play|stop|skip|music|remember|save memory|forget|find|"
                    r"grep|git|check|inspect|files|folder|project|repo|audit|desktop|ciel|codebase|"
                    r"launch|open|photo|picture|gallery|settings|ls|cat|mkdir|rm|cp|mv|ur|your|clipboard|window|battery|volume|brightness|pause|resume|track|song)\b"
                    r"|https?://|\.py\b|\.json\b|\.sh\b",
                    (last_u or "").lower(),
                )
            )
        )
        provider = MODELS.get(routed, {}).get("provider", "")

        try:
            client, active_model = get_client_and_model(routed)
            working_msgs = list(trim_history(messages))
            if active_plan:
                working_msgs.insert(
                    1 if working_msgs and working_msgs[0].get("role") == "system" else 0,
                    {
                        "role": "system",
                        "content": (
                            "[PLAN MODE ACTIVE — READ-ONLY]\n"
                            "Write, edit, delete, and terminal execution tools are locked.\n"
                            "1. Inspect relevant files using read-only tools first (never guess contents).\n"
                            "2. Output a concise, numbered implementation plan with exact file paths and steps.\n"
                            "3. Ask the user to confirm and toggle `/plan` off (or press Shift+Tab) before executing."
                        ),
                    },
                )

            create_kwargs = {
                "model": active_model,
                "messages": working_msgs,
                "temperature": 0.2,
                "stream": True,
            }
            if attach_tools:
                active_schemas = _filter_tools_for_plan(tool_schemas) if active_plan else tool_schemas
                if active_schemas:
                    create_kwargs["tools"] = active_schemas

            active_effort = "low" if not attach_tools else effort
            model_lower = active_model.lower()
            if provider == "groq":
                if "qwen" in model_lower:
                    create_kwargs["reasoning_effort"] = "none" if not attach_tools else "default"
                    create_kwargs["extra_body"] = {"reasoning_format": "hidden"}
                elif "gpt-oss" in model_lower:
                    create_kwargs["reasoning_effort"] = active_effort
            elif provider == "gemini":
                create_kwargs["reasoning_effort"] = active_effort
            elif provider == "openrouter":
                create_kwargs["extra_body"] = {"reasoning": {"effort": active_effort}}

            for attempt in range(2):
                try:
                    stream = client.chat.completions.create(**create_kwargs)
                    break
                except Exception as re_err:
                    if "503" in str(re_err) and attempt == 0:
                        time.sleep(1.5)
                        continue
                    raise
        except Exception as e:
            err_str = str(e)
            if any(code in err_str for code in ("400", "402", "404", "413", "429", "503", "Connection", "timeout", "ConnectError")):
                failed_aliases.add(routed)
                next_cloud = next(
                    (c for c in fallback_chain if c not in failed_aliases and alias_available(c)),
                    None,
                )
                if next_cloud:
                    alias = next_cloud
                    steps -= 1
                    continue
                if alias_available("local") and "local" not in failed_aliases:
                    alias = "local"
                    steps -= 1
                    continue

            while len(messages) > 1 and _msg_role(messages[-1]) != "user":
                messages.pop()
            err_msg = f"[API Error]: {getattr(e, 'body', None) or repr(e)}"
            if on_token:
                on_token(err_msg)
            return err_msg

        chunks = []
        tc_acc: dict[int, dict] = {}
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue
            if delta.content:
                chunks.append(delta.content)
                if on_token:
                    on_token(delta.content)
            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else 0
                    entry = tc_acc.setdefault(
                        idx,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            entry["function"]["arguments"] += tc.function.arguments

        reply = "".join(chunks)

        if not tc_acc:
            parsed_raw = _parse_raw_tool_json(reply)
            if parsed_raw:
                raw_name = parsed_raw.get("name", "")
                raw_args = parsed_raw.get("arguments") or {}
                blocked = _pre_tool_hook(raw_name, raw_args, active_plan)
                if blocked:
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": blocked})
                    continue
                _execute_raw_tool(parsed_raw, messages, on_tool)
                continue
            if (
                attach_tools
                and steps == 1
                and re.search(r"\b(i'll|i will|let me|taking a look|check(ing)? (your|my|the))\b", reply.lower())
            ):
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": "Execute the required tool call right now instead of describing what you will do.",
                })
                continue
            messages.append({"role": "assistant", "content": reply})
            return reply

        tool_calls_list = [tc_acc[i] for i in sorted(tc_acc)]
        assistant_msg = {"role": "assistant", "tool_calls": tool_calls_list}
        if reply:
            assistant_msg["content"] = reply
        messages.append(assistant_msg)

        for call in tool_calls_list:
            fn_name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            blocked = _pre_tool_hook(fn_name, args, active_plan)
            if blocked:
                if on_tool:
                    on_tool(f"[BLOCKED] {fn_name}")
                result = blocked
            else:
                if on_tool:
                    on_tool(f"{fn_name}({args})")
                try:
                    result = (
                        tool_map[fn_name](**args)
                        if fn_name in tool_map
                        else f"Unknown tool: {fn_name}"
                    )
                except Exception as tool_err:
                    result = f"Tool Error calling {fn_name}: {tool_err}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{steps}",
                    "content": str(result)[:2000],
                }
            )


def _pre_cielmd_build_system_prompt(*args, **kwargs):
    base = _base_build_system_prompt(*args, **kwargs)
    self_dir = os.path.dirname(os.path.abspath(__file__))
    if self_dir not in base:
        base += (
            f"\n\n[Workspace & Tool Rules]\n"
            f"- Current date: {time.strftime('%Y-%m-%d')}\n"
            f"- Your own source code and workspace lives in: {self_dir}\n"
            f"- When asked about 'your files', 'ur files', 'your code', or 'what is new', inspect {self_dir} directly (e.g. ls -lt {self_dir} or git -C {self_dir} status) instead of scanning ~.\n"
            f"- Call tools immediately without narrating what you are going to do first."
        )
    return base


def _load_ciel_md_context() -> str:
    seen = set()
    blocks = []
    for candidate in (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "CIEL.md"),
        os.path.join(os.getcwd(), "CIEL.md"),
    ):
        real = os.path.realpath(candidate)
        if real in seen or not os.path.isfile(real):
            continue
        seen.add(real)
        try:
            with open(real, "r", encoding="utf-8") as f:
                content = f.read().strip()[:2200]
            if content:
                blocks.append(f"[Project Context: {real}]\n{content}")
        except Exception:
            pass
    return "\n\n".join(blocks)


def build_system_prompt(*args, **kwargs):
    base_prompt = _pre_cielmd_build_system_prompt(*args, **kwargs)
    ctx = _load_ciel_md_context()
    if ctx and "[Project Context:" not in base_prompt:
        base_prompt += f"\n\n{ctx}"
    return base_prompt
