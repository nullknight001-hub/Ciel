#!/home/ilham/Projects/Ciel_V3/venv/bin/python
import json, os, shutil, socket, subprocess, time

SOCK_PATH = "/tmp/ciel_dropdown.sock"
LOCK_FILE = "/tmp/ciel_quick_ask.lock"
MARKER = "Inspect/help with this selection from my screen:"

def is_debounced(cooldown: float = 0.3) -> bool:
    now = time.time()
    try:
        if os.path.exists(LOCK_FILE) and (now - os.path.getmtime(LOCK_FILE)) < cooldown:
            return True
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass
    return False

def grab_active_selection() -> str:
    for cmd in (
        ["wl-paste", "--primary", "--no-newline"],
        ["xclip", "-o", "-selection", "primary"],
    ):
        if shutil.which(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=0.5).stdout.strip()
                if out and MARKER not in out:
                    return out[:2500]
            except Exception:
                pass
    return ""

def send_to_running_dropdown(selection: str) -> bool:
    if not os.path.exists(SOCK_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            s.connect(SOCK_PATH)
            s.sendall(json.dumps({"cmd": "toggle", "selection": selection}).encode("utf-8"))
        return True
    except Exception:
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass
        return False

def main():
    if is_debounced():
        return
    clip = grab_active_selection()
    if send_to_running_dropdown(clip):
        return
    env = os.environ.copy()
    if clip:
        env["CIEL_INITIAL_PROMPT"] = f"{MARKER}\n\n```\n{clip}\n```"
    log_f = open("/tmp/ciel_quick_ask.log", "w", encoding="utf-8")
    subprocess.Popen(
        ["/home/ilham/Projects/Ciel_V3/venv/bin/python", "/home/ilham/Projects/Ciel_V3/desktop.py"],
        cwd="/home/ilham/Projects/Ciel_V3",
        env=env,
        stdout=log_f,
        stderr=log_f,
        start_new_session=True,
    )

if __name__ == "__main__":
    main()
