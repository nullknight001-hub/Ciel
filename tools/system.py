import base64
from io import BytesIO
import os
import re
import shutil
import subprocess
import sys
import time
from PIL import Image
import config
from config import (
    MAX_OUTPUT_CHARS,
    alias_available,
    get_client_and_model,
    resolve_default_smart_alias,
)

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

DANGEROUS_PATTERNS = (
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\b(shutdown|reboot|poweroff)\b",
    r":\(\)\{",
)

_confirm_handler = None


def set_confirm_handler(fn):
    """Registers an optional UI confirmation callback(command: str) -> bool."""
    global _confirm_handler
    _confirm_handler = fn


def _is_dangerous_command(cmd: str) -> bool:
    stripped = cmd.strip()
    parts = stripped.split()
    first_word = parts[0] if parts else ""
    if (
        first_word in DANGEROUS_PREFIXES
        or " rm " in f" {stripped} "
        or "sudo " in stripped
    ):
        return True
    return any(re.search(pat, stripped) for pat in DANGEROUS_PATTERNS)


def _confirm_command(cmd: str) -> bool:
    if not _is_dangerous_command(cmd):
        return True
    if _confirm_handler is not None:
        return bool(_confirm_handler(cmd))
    if not sys.stdin or not sys.stdin.isatty():
        return False
    print(f"\n[APPROVAL REQUIRED] Agent wants to run: {cmd}")
    return input("Allow? [y/N]: ").strip().lower() == "y"


def run_terminal(command: str) -> str:
    """Runs a bash command in the Linux terminal and returns stdout/stderr."""
    if not _confirm_command(command):
        return "Command denied by user or non-interactive environment."

    print(f"[Running]: {command}")
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


def launch_app(command: str) -> str:
    """Launches a GUI desktop application or windowed script detached in the background without blocking."""
    print(f"[Launching App]: {command}")
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


def see_screen(
    question: str = "Describe what is on the screen and any errors visible.",
) -> str:
    """Takes a screenshot of the user's screen and analyzes it visually to read errors, UI, or test apps."""
    print("[Taking Screenshot & Looking...]")
    img_path = "/tmp/ciel_vision.png"
    if os.path.exists(img_path):
        try:
            os.remove(img_path)
        except OSError:
            pass

    if os.environ.get("WAYLAND_DISPLAY"):
        candidates = [
            ["spectacle", "-b", "-n", "-f", "-o", img_path],
            ["grim", img_path],
            ["gnome-screenshot", "-f", img_path],
            ["flameshot", "full", "-p", img_path],
        ]
    else:
        candidates = [
            ["spectacle", "-b", "-n", "-f", "-o", img_path],
            ["scrot", "-o", img_path],
            ["flameshot", "full", "-p", img_path],
            ["gnome-screenshot", "-f", img_path],
        ]

    errors = []
    captured = False
    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(img_path):
            captured = True
            break
        err_detail = (res.stderr or res.stdout).strip()
        errors.append(f"{cmd[0]}: {err_detail or f'exit {res.returncode}'}")

    if not captured:
        if not errors:
            return "Error: No screenshot tool found. Install 'spectacle' (KDE), 'grim' (Wayland), or 'scrot' (X11)."
        return f"Error: Screen capture failed ({'; '.join(errors)})."

    try:
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            if img.width > 1280:
                ratio = 1280 / img.width
                img = img.resize((1280, int(img.height * ratio)))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64_img = base64.b64encode(buf.getvalue()).decode("utf-8")

        alias = config.current_alias
        vision_alias = (
            alias
            if alias in ("flash", "pro", "flash-or") and alias_available(alias)
            else resolve_default_smart_alias()
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
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                        },
                    ],
                }
            ],
            temperature=0.1,
        )
        return f"[Visual Observation]:\n{vision_resp.choices[0].message.content}"
    except Exception as e:
        return f"Vision error: {e}"


# === CIEL Jarvis Desktop Hooks ===
import glob as _jv_glob
import json as _jv_json
import os as _jv_os
import shutil as _jv_shutil
import socket as _jv_socket
import subprocess as _jv_sub

MPV_IPC_SOCKET = "/tmp/mpv-socket"
_MPV_PREV_VOLUME: float = 100.0


def _run_quick(cmd: list[str], timeout: float = 1.5) -> str:
    if not _jv_shutil.which(cmd[0]):
        return ""
    try:
        res = _jv_sub.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (res.stdout or "").strip()
    except Exception:
        return ""


def get_desktop_context(max_clip_chars: int = 1200) -> str:
    """Return active window title, current selection/clipboard text, and RAM/battery stats."""
    # 1. Active window title (Wayland Hyprland/Sway or X11)
    win_title = ""
    if _jv_os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and _jv_shutil.which("hyprctl"):
        raw = _run_quick(["hyprctl", "activewindow", "-j"])
        if raw:
            try:
                data = _jv_json.loads(raw)
                win_title = f"{data.get('class', '')}: {data.get('title', '')}".strip(": ")
            except Exception:
                pass
    if not win_title and _jv_os.environ.get("SWAYSOCK") and _jv_shutil.which("swaymsg"):
        raw = _run_quick(["swaymsg", "-t", "get_tree"])
        m = re.search(r'"focused":\s*true[^}]*"name":\s*"([^"]+)"', raw)
        if m:
            win_title = m.group(1)
    if not win_title and _jv_shutil.which("kdotool"):
        wid = _run_quick(["kdotool", "getactivewindow"])
        if wid:
            win_title = _run_quick(["kdotool", "getwindowname", wid])
    if (
        not win_title
        and "KDE" in _jv_os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        and _jv_shutil.which("gdbus")
        and _jv_shutil.which("journalctl")
    ):
        try:
            js_path = "/tmp/ciel_kwin_active.js"
            with open(js_path, "w", encoding="utf-8") as jf:
                jf.write(
                    'var w = workspace.activeWindow;\n'
                    'if (w) { print("CIEL_ACTIVE_WIN:" + (w.resourceClass || "") + "::" + (w.caption || "")); }\n'
                )
            _run_quick([
                "gdbus", "call", "--session",
                "--dest", "org.kde.KWin",
                "--object-path", "/Scripting",
                "--method", "org.kde.kwin.Scripting.unloadScript",
                "ciel_active_win",
            ])
            load_out = _run_quick([
                "gdbus", "call", "--session",
                "--dest", "org.kde.KWin",
                "--object-path", "/Scripting",
                "--method", "org.kde.kwin.Scripting.loadScript",
                js_path,
                "ciel_active_win",
            ])
            m_id = re.search(r"(\d+)", load_out)
            if m_id:
                obj_path = f"/Scripting/Script{m_id.group(1)}"
                _run_quick([
                    "gdbus", "call", "--session",
                    "--dest", "org.kde.KWin",
                    "--object-path", obj_path,
                    "--method", "org.kde.kwin.Script.run",
                ])
                _run_quick([
                    "gdbus", "call", "--session",
                    "--dest", "org.kde.KWin",
                    "--object-path", obj_path,
                    "--method", "org.kde.kwin.Script.stop",
                ])
                logs = _run_quick([
                    "journalctl", "--user", "-u", "plasma-kwin_wayland",
                    "-n", "15", "--no-pager", "-o", "cat",
                ])
                hits = re.findall(r"CIEL_ACTIVE_WIN:([^\n]+)", logs)
                if hits:
                    cls_name, _, caption = hits[-1].partition("::")
                    win_title = f"{cls_name}: {caption}".strip(": ")
        except Exception:
            pass
    if not win_title and _jv_shutil.which("xdotool"):
        win_title = _run_quick(["xdotool", "getactivewindow", "getwindowname"])
    if not win_title and _jv_shutil.which("xprop"):
        wid = _run_quick(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
        m = re.search(r"0x[0-9a-fA-F]+", wid)
        if m and m.group(0) != "0x0":
            raw_name = _run_quick(["xprop", "-id", m.group(0), "WM_NAME"])
            if "=" in raw_name:
                win_title = raw_name.split("=", 1)[1].strip().strip('"')

    # 2. Highlighted selection first, then standard clipboard
    clip = ""
    for cmd in (
        ["wl-paste", "--primary", "--no-newline"],
        ["wl-paste", "--no-newline"],
        ["xclip", "-o", "-selection", "primary"],
        ["xclip", "-o", "-selection", "clipboard"],
    ):
        clip = _run_quick(cmd)
        if clip:
            break
    if len(clip) > max_clip_chars:
        clip = clip[:max_clip_chars] + "... [truncated]"

    # 3. RAM & Battery status
    ram_info = ""
    try:
        mem = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total_gb = mem.get("MemTotal", 0) / (1024 * 1024)
        avail_gb = mem.get("MemAvailable", 0) / (1024 * 1024)
        used_gb = max(0.0, total_gb - avail_gb)
        ram_info = f"{used_gb:.1f}/{total_gb:.1f} GB RAM used"
    except Exception:
        pass

    bat_info = ""
    for bat in sorted(_jv_glob.glob("/sys/class/power_supply/BAT*")):
        try:
            cap = open(f"{bat}/capacity", encoding="utf-8").read().strip()
            stat_txt = open(f"{bat}/status", encoding="utf-8").read().strip()
            bat_info = f"{cap}% ({stat_txt})"
            break
        except Exception:
            pass

    lines = [
        f"Active Window: {win_title or 'Unknown'}",
        f"System: {' | '.join(x for x in (ram_info, bat_info) if x) or 'N/A'}",
        f"Clipboard/Selection:\n{clip if clip else '(empty)'}",
    ]
    return "\n".join(lines)


def _find_mpv_socket() -> str | None:
    if _jv_os.path.exists(MPV_IPC_SOCKET):
        return MPV_IPC_SOCKET
    candidates = sorted(_jv_glob.glob("/tmp/*mpv*"))
    for c in candidates:
        if not c.endswith(".log"):
            return c
    return None


def _mpv_ipc(command: list) -> dict | None:
    sock_path = _find_mpv_socket()
    if not sock_path:
        return None
    try:
        with _jv_socket.socket(_jv_socket.AF_UNIX, _jv_socket.SOCK_STREAM) as s:
            s.settimeout(0.8)
            s.connect(sock_path)
            payload = (_jv_json.dumps({"command": command}) + "\n").encode("utf-8")
            s.sendall(payload)
            raw = s.recv(4096).decode("utf-8", errors="replace").splitlines()
            for line in raw:
                if line.strip():
                    return _jv_json.loads(line)
    except Exception:
        return None
    return None


def mpv_control(action: str = "status", value: str = "") -> str:
    """Control background mpv player via IPC socket: status, toggle, next, prev, duck, unduck, volume, stop."""
    global _MPV_PREV_VOLUME
    act = (action or "status").strip().lower()
    if not _find_mpv_socket():
        return "mpv IPC socket not active (no background mpv player running)."

    if act == "status":
        title = (_mpv_ipc(["get_property", "media-title"]) or {}).get("data", "Unknown")
        paused = (_mpv_ipc(["get_property", "pause"]) or {}).get("data", False)
        vol = (_mpv_ipc(["get_property", "volume"]) or {}).get("data", "?")
        state = "Paused" if paused else "Playing"
        return f"mpv [{state}] | Track: {title} | Volume: {vol}%"
    if act in ("toggle", "pause", "play"):
        _mpv_ipc(["cycle", "pause"])
        paused = (_mpv_ipc(["get_property", "pause"]) or {}).get("data", False)
        return "mpv paused." if paused else "mpv resumed."
    if act in ("next", "skip"):
        _mpv_ipc(["playlist-next"])
        return "Skipped to next track in mpv."
    if act in ("prev", "previous"):
        _mpv_ipc(["playlist-prev"])
        return "Went to previous track in mpv."
    if act == "duck":
        cur = (_mpv_ipc(["get_property", "volume"]) or {}).get("data")
        if isinstance(cur, (int, float)) and cur > 25:
            _MPV_PREV_VOLUME = float(cur)
        target = float(value) if value else 20.0
        _mpv_ipc(["set_property", "volume", target])
        return f"Ducked mpv volume to {target:.0f}%."
    if act == "unduck":
        restore = float(value) if value else _MPV_PREV_VOLUME
        _mpv_ipc(["set_property", "volume", restore])
        return f"Restored mpv volume to {restore:.0f}%."
    if act == "volume":
        lvl = max(0.0, min(130.0, float(value or 80)))
        _mpv_ipc(["set_property", "volume", lvl])
        return f"Set mpv volume to {lvl:.0f}%."
    if act == "stop":
        _mpv_ipc(["quit"])
        return "Stopped mpv."
    return f"Unknown mpv action: {action}"


def system_control(action: str, value: str = "") -> str:
    """Control OS volume, screen brightness, or send a desktop notification."""
    act = (action or "").strip().lower()
    val = str(value or "").strip()

    if act == "volume":
        if _jv_shutil.which("wpctl"):
            if val in ("mute", "toggle"):
                _jv_sub.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], check=False)
                return "Toggled audio mute."
            pct = max(0, min(150, int(re.sub(r"[^0-9]", "", val) or "50")))
            _jv_sub.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%"], check=False)
            return f"System volume set to {pct}%."
        if _jv_shutil.which("pactl"):
            if val in ("mute", "toggle"):
                _jv_sub.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], check=False)
                return "Toggled audio mute."
            pct = max(0, min(150, int(re.sub(r"[^0-9]", "", val) or "50")))
            _jv_sub.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"], check=False)
            return f"System volume set to {pct}%."
        return "Neither wpctl nor pactl is installed."

    if act == "brightness":
        pct = max(5, min(100, int(re.sub(r"[^0-9]", "", val) or "60")))
        if _jv_shutil.which("brightnessctl"):
            _jv_sub.run(["brightnessctl", "set", f"{pct}%"], check=False)
            return f"Screen brightness set to {pct}%."
        if _jv_shutil.which("gdbus"):
            max_out = _run_quick([
                "gdbus", "call", "--session",
                "--dest", "org.kde.Solid.PowerManagement",
                "--object-path", "/org/kde/Solid/PowerManagement/Actions/BrightnessControl",
                "--method", "org.kde.Solid.PowerManagement.Actions.BrightnessControl.brightnessMax",
            ])
            m = re.search(r"(\d+)", max_out)
            if m:
                target = int(int(m.group(1)) * pct / 100)
                _jv_sub.run([
                    "gdbus", "call", "--session",
                    "--dest", "org.kde.Solid.PowerManagement",
                    "--object-path", "/org/kde/Solid/PowerManagement/Actions/BrightnessControl",
                    "--method", "org.kde.Solid.PowerManagement.Actions.BrightnessControl.setBrightness",
                    str(target),
                ], check=False)
                return f"Screen brightness set to {pct}% (KDE PowerDevil)."
        return "Neither brightnessctl nor KDE PowerDevil D-Bus is available."

    if act == "notify":
        if not _jv_shutil.which("notify-send"):
            return "notify-send not installed."
        _jv_sub.run(["notify-send", "CIEL", val or "Task completed."], check=False)
        return f"Notification sent: {val or 'Task completed.'}"

    return f"Unsupported system_control action: {action}"
def _ydotool_env() -> dict:
    import os
    env = os.environ.copy()
    sock = "/tmp/.ydotool_socket"
    if os.path.exists(sock) and "YDOTOOL_SOCKET" not in env:
        env["YDOTOOL_SOCKET"] = sock
    return env


def press_key(key: str) -> str:
    """Presses a key using ydotool (for example 'space', 'k', 'Return', 'Tab', 'Escape', 'f', 'm')."""
    import shutil
    if not shutil.which("ydotool"):
        return "Error: ydotool is not installed."
    key_map = {
        "space": "57:1 57:0",
        "k": "37:1 37:0",
        "return": "28:1 28:0",
        "enter": "28:1 28:0",
        "tab": "15:1 15:0",
        "escape": "1:1 1:0",
        "esc": "1:1 1:0",
        "f": "33:1 33:0",
        "m": "50:1 50:0",
        "left": "105:1 105:0",
        "right": "106:1 106:0",
        "up": "103:1 103:0",
        "down": "108:1 108:0",
    }
    norm = (key or "").strip().lower()
    seq = key_map.get(norm)
    env = _ydotool_env()
    try:
        args = ["ydotool", "key", *seq.split()] if seq else ["ydotool", "key", key]
        res = subprocess.run(args, env=env, capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return f"Error pressing key '{key}': {(res.stderr or res.stdout).strip()}"
        return f"Pressed key '{key}'."
    except Exception as e:
        return f"Error pressing key '{key}': {e}"


def type_text(text: str) -> str:
    """Types text on the active window using ydotool."""
    import shutil
    if not shutil.which("ydotool"):
        return "Error: ydotool is not installed."
    env = _ydotool_env()
    try:
        res = subprocess.run(["ydotool", "type", "--", text], env=env, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return f"Error typing text: {(res.stderr or res.stdout).strip()}"
        return f"Typed text ({len(text)} chars)."
    except Exception as e:
        return f"Error typing text: {e}"


def click_mouse(x: int, y: int, button: str = "left") -> str:
    """Moves cursor to screen coordinates (x, y) and clicks ('left', 'right', or 'middle')."""
    import shutil
    if not shutil.which("ydotool"):
        return "Error: ydotool is not installed."
    btn_code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get((button or "left").lower(), "0xC0")
    env = _ydotool_env()
    try:
        mv = subprocess.run(["ydotool", "mousemove", "--absolute", "-x", str(int(x)), "-y", str(int(y))], env=env, capture_output=True, text=True, timeout=5)
        if mv.returncode != 0:
            return f"Error moving mouse: {(mv.stderr or mv.stdout).strip()}"
        clk = subprocess.run(["ydotool", "click", btn_code], env=env, capture_output=True, text=True, timeout=5)
        if clk.returncode != 0:
            return f"Error clicking mouse: {(clk.stderr or clk.stdout).strip()}"
        return f"Clicked {button} at ({x}, {y})."
    except Exception as e:
        return f"Error clicking mouse: {e}"
