import inspect
from memory import delete_memory, save_memory
from tools.files import edit_file, find_files, grep_code, read_file, write_file
from tools.media import open_youtube_browser, play_youtube_music, skip_music, stop_music
from tools.system import click_mouse, launch_app, press_key, run_terminal, see_screen, set_confirm_handler, type_text
from tools.web import read_webpage, web_search
from tools.skills import load_skill

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


ALL_TOOLS = [
    see_screen,
    
    launch_app,
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
    skip_music,
    stop_music,
    load_skill,
]

tool_schemas, tool_map = build_tool_schemas(ALL_TOOLS)


# === Register repo_map ===
from tools.files import repo_map as _repo_map

if "tool_map" in globals() and isinstance(tool_map, dict):
    tool_map["repo_map"] = _repo_map

if "tool_schemas" in globals() and isinstance(tool_schemas, list):
    if not any((t.get("function") or {}).get("name") == "repo_map" for t in tool_schemas):
        tool_schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "repo_map",
                    "description": (
                        "Scan a directory and return a compact AST symbol map of Python files, "
                        "classes, functions, and exact line ranges. Call this before read_file when exploring a repo."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {
                                "type": "string",
                                "description": "Directory path to map (defaults to /home/ilham/Projects/Ciel_V3).",
                            }
                        },
                        "required": [],
                    },
                },
            }
        )


# === Register Jarvis Desktop Tools ===
from tools.system import (
    get_desktop_context as _get_desktop_context,
    mpv_control as _mpv_control,
    system_control as _system_control,
)

if "tool_map" in globals() and isinstance(tool_map, dict):
    tool_map["get_desktop_context"] = _get_desktop_context
    tool_map["mpv_control"] = _mpv_control
    tool_map["system_control"] = _system_control

_JARVIS_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_desktop_context",
            "description": (
                "Inspect the user's current active window title, highlighted selection/clipboard text, "
                "and RAM/battery status. Call this when the user refers to 'this', 'my screen/clipboard', or a copied error."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mpv_control",
            "description": "Control background mpv player via IPC: status, toggle, next, prev, duck, unduck, volume, stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: status, toggle, next, prev, duck, unduck, volume, or stop.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Optional numeric volume percentage (0-100).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_control",
            "description": "Adjust OS audio volume, screen brightness, or send a desktop notification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: volume, brightness, or notify.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Percentage (e.g. '40%' or 'mute') or notification message text.",
                    },
                },
                "required": ["action", "value"],
            },
        },
    },
]

if "tool_schemas" in globals() and isinstance(tool_schemas, list):
    existing_names = {(t.get("function") or {}).get("name") for t in tool_schemas}
    for _schema in _JARVIS_SCHEMAS:
        if _schema["function"]["name"] not in existing_names:
            tool_schemas.append(_schema)
