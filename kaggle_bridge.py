import atexit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_DIR = os.path.join(BASE_DIR, "kaggle_worker")
TPL_PATH = os.path.join(WORKER_DIR, "serve_qwen.tpl.py")
WORKER_SCRIPT_PATH = os.path.join(WORKER_DIR, "serve_qwen.py")
ENV_PATH = os.path.join(BASE_DIR, ".env")
BIN_DIR = os.path.join(BASE_DIR, "bin")
KAGGLE_BIN = os.path.join(os.path.dirname(sys.executable), "kaggle")
KERNEL_SLUG = "ilham001/ciel-qwen27b-server"

_lock = threading.Lock()
_state = {
    "status": "stopped",  # stopped | starting | running
    "cmd": "stop",        # run | stop
    "url": "",
    "session_id": "",
}
_control = {
    "httpd": None,
    "tunnel_proc": None,
    "callback_url": "",
}


def _write_env_url(url: str):
    if url:
        os.environ["KAGGLE_BASE_URL"] = url
    else:
        os.environ.pop("KAGGLE_BASE_URL", None)

    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = [line for line in f if not line.startswith("KAGGLE_BASE_URL=")]
    if url:
        lines.append(f"KAGGLE_BASE_URL={url}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def get_kaggle_status() -> tuple[str, str]:
    with _lock:
        return _state["status"], _state["url"]


def _ensure_cloudflared() -> str:
    sys_cf = shutil.which("cloudflared")
    if sys_cf:
        return sys_cf
    os.makedirs(BIN_DIR, exist_ok=True)
    local_cf = os.path.join(BIN_DIR, "cloudflared")
    if not os.path.exists(local_cf):
        print("[Kaggle] Downloading cloudflared binary for local callback tunnel...")
        r = requests.get(
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
            timeout=60,
        )
        r.raise_for_status()
        with open(local_cf, "wb") as f:
            f.write(r.content)
        os.chmod(local_cf, 0o755)
    return local_cf


class _ControlHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/heartbeat":
            self._send_json(404, {"error": "not found"})
            return
        qs = parse_qs(parsed.query)
        req_session = (qs.get("session") or [""])[0]
        with _lock:
            active_session = _state["session_id"]
            cmd = _state["cmd"]
        if cmd == "stop" or not req_session or req_session != active_session:
            self._send_json(200, {"cmd": "stop"})
        else:
            self._send_json(200, {"cmd": "run"})

    def do_POST(self):
        if self.path != "/register":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        req_session = data.get("session_id", "")
        url = data.get("url", "")
        with _lock:
            if (
                _state["cmd"] != "run"
                or _state["status"] == "stopped"
                or req_session != _state["session_id"]
                or not url.startswith("https://")
            ):
                self._send_json(200, {"cmd": "stop"})
                return
            _state["url"] = url
            _state["status"] = "running"

        _write_env_url(url)
        print(f"\n[Kaggle Ready] Qwen3.8-27B connected at {url}")
        self._send_json(200, {"cmd": "run"})

    def log_message(self, *args):
        pass


def _stop_control_plane():
    proc = _control.get("tunnel_proc")
    httpd = _control.get("httpd")
    _control["tunnel_proc"] = None
    _control["httpd"] = None
    _control["callback_url"] = ""
    if proc is not None:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    if httpd is not None:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass


def _start_control_plane(session_id: str) -> str:
    if (
        _control["callback_url"]
        and _control["tunnel_proc"]
        and _control["tunnel_proc"].poll() is None
    ):
        return _control["callback_url"]

    _stop_control_plane()
    cf_bin = _ensure_cloudflared()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ControlHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    proc = subprocess.Popen(
        [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    found_url = {"val": ""}
    ready_evt = threading.Event()

    def _read_cf():
        for line in proc.stdout:
            if not found_url["val"]:
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    found_url["val"] = m.group(0)
                    ready_evt.set()

    threading.Thread(target=_read_cf, daemon=True).start()
    if not ready_evt.wait(timeout=20) or not found_url["val"]:
        proc.kill()
        httpd.shutdown()
        httpd.server_close()
        raise RuntimeError("Failed to open local cloudflared callback tunnel.")

    with _lock:
        if _state["session_id"] != session_id or _state["cmd"] != "run":
            proc.kill()
            httpd.shutdown()
            httpd.server_close()
            raise RuntimeError("Kaggle start cancelled.")
        _control["httpd"] = httpd
        _control["tunnel_proc"] = proc
        _control["callback_url"] = found_url["val"]

    return found_url["val"]


def _push_worker(session_id: str):
    try:
        status_out = subprocess.check_output(
            [KAGGLE_BIN, "kernels", "status", KERNEL_SLUG],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
        if "QUEUED" in status_out or "RUNNING" in status_out:
            print(
                f"\n[Kaggle Busy] Previous run still active on Kaggle ({status_out.strip()}). "
                "Cancel it on Kaggle first."
            )
            with _lock:
                if _state["session_id"] == session_id:
                    _state["status"] = "stopped"
                    _state["cmd"] = "stop"
            _stop_control_plane()
            return
    except Exception:
        pass

    with _lock:
        if _state["session_id"] != session_id or _state["cmd"] != "run":
            return

    try:
        subprocess.run(
            [KAGGLE_BIN, "kernels", "push", "-p", WORKER_DIR, "--accelerator", "NvidiaTeslaT4"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"\n[Kaggle Error] Failed to start kernel: {e.stderr.strip()}")
        with _lock:
            if _state["session_id"] == session_id:
                _state["status"] = "stopped"
                _state["cmd"] = "stop"
        _stop_control_plane()


def start_kaggle_worker() -> str:
    with _lock:
        if _state["status"] == "running":
            return f"Kaggle 27B already running ({_state['url']})."
        if _state["status"] == "starting":
            return "Kaggle 27B is already booting in the background (~3 min)..."

        session_id = uuid.uuid4().hex
        _state["status"] = "starting"
        _state["cmd"] = "run"
        _state["url"] = ""
        _state["session_id"] = session_id

    try:
        callback_url = _start_control_plane(session_id)
        with _lock:
            if _state["session_id"] != session_id or _state["cmd"] != "run":
                _stop_control_plane()
                return "Kaggle start cancelled."

        with open(TPL_PATH, "r", encoding="utf-8") as f:
            tpl = f.read()
        rendered = (
            tpl.replace("__CONTROL_URL__", callback_url)
            .replace("__SESSION_ID__", session_id)
        )
        with open(WORKER_SCRIPT_PATH, "w", encoding="utf-8") as f:
            f.write(rendered)
    except Exception as e:
        with _lock:
            if _state["session_id"] == session_id:
                _state["status"] = "stopped"
                _state["cmd"] = "stop"
                _state["session_id"] = ""
        _stop_control_plane()
        return f"Kaggle start failed: {e}"

    threading.Thread(target=_push_worker, args=(session_id,), daemon=True).start()
    return "Starting Kaggle 27B GPU server in background (~3 min). Using fallback model until ready."


def stop_kaggle_worker() -> str:
    with _lock:
        prev_status = _state["status"]
        url = _state["url"]
        has_tunnel = _control.get("tunnel_proc") is not None
        _state["status"] = "stopped"
        _state["cmd"] = "stop"
        _state["url"] = ""
        _state["session_id"] = ""

    _write_env_url("")

    if prev_status == "stopped" and not has_tunnel:
        return ""

    if url:
        base = url[:-3] if url.endswith("/v1") else url
        try:
            requests.post(f"{base}/shutdown", timeout=3)
        except Exception:
            pass

    # Keep local callback server alive briefly so the 3s heartbeat receives {"cmd": "stop"}
    time.sleep(3.2)
    _stop_control_plane()
    return "Stopped Kaggle 27B GPU server and cleared KAGGLE_BASE_URL."


def _handle_exit_signal(signum, frame):
    stop_kaggle_worker()
    raise SystemExit(0)


atexit.register(stop_kaggle_worker)
for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    try:
        signal.signal(_sig, _handle_exit_signal)
    except Exception:
        pass