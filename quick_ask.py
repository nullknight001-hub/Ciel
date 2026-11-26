#!/home/ilham/Projects/Ciel_V3/venv/bin/python
import json, os, socket, subprocess, time

SOCK_PATH = "/tmp/ciel_dropdown.sock"
LOCK_FILE = "/tmp/ciel_quick_ask.lock"


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
    if toggle_running_dropdown():
        return

    log_f = open("/tmp/ciel_quick_ask.log", "w", encoding="utf-8")
    subprocess.Popen(
        ["/home/ilham/Projects/Ciel_V3/venv/bin/python", "/home/ilham/Projects/Ciel_V3/desktop.py"],
        cwd="/home/ilham/Projects/Ciel_V3",
        env=os.environ.copy(),
        stdout=log_f,
        stderr=log_f,
        start_new_session=True,
    )


if __name__ == "__main__":
    main()
