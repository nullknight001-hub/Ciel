#!/home/ilham/Projects/Ciel_V3/venv/bin/python
import datetime, json, os, socket, subprocess, time

SOCK_PATH = "/tmp/ciel_dropdown.sock"
LOCK_FILE = "/tmp/ciel_quick_ask.lock"
TRACE_LOG = "/tmp/ciel_hotkey.log"


def trace(msg: str):
    try:
        with open(TRACE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def is_debounced(cooldown: float = 0.25) -> bool:
    now = time.time()
    try:
        if os.path.exists(LOCK_FILE) and (now - os.path.getmtime(LOCK_FILE)) < cooldown:
            return True
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        pass
    return False


def toggle_running_dropdown() -> bool:
    if not os.path.exists(SOCK_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            s.connect(SOCK_PATH)
            s.sendall(json.dumps({"cmd": "toggle", "selection": ""}).encode("utf-8"))
        trace("Toggled running CIEL HUD via socket")
        return True
    except Exception as exc:
        trace(f"Socket failed ({exc}), starting ciel-hud.service")
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass
        return False


def main():
    if is_debounced():
        return
    if toggle_running_dropdown():
        return
    trace("Starting ciel-hud.service")
    subprocess.run(["systemctl", "--user", "start", "ciel-hud.service"], check=False)


if __name__ == "__main__":
    main()
