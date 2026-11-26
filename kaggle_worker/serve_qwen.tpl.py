from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CONTROL_URL = "__CONTROL_URL__"
SESSION_ID = "__SESSION_ID__"

stop_event = threading.Event()
server = None
tunnel = None


def _force_exit():
    for proc in (tunnel, server):
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
    os._exit(0)


def _control_heartbeat():
    if not CONTROL_URL:
        return
    fail_count = 0
    while not stop_event.is_set():
        time.sleep(3)
        try:
            q = urllib.parse.urlencode({"session": SESSION_ID})
            req = urllib.request.Request(
                f"{CONTROL_URL}/heartbeat?{q}",
                headers={"User-Agent": "ciel-kaggle-worker"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                fail_count = 0
                if data.get("cmd") == "stop":
                    print("[CIEL] Stop signal received. Exiting immediately.", flush=True)
                    stop_event.set()
                    _force_exit()
        except urllib.error.HTTPError as e:
            # HTTP 530 means the local Cloudflare tunnel closed because CIEL exited
            fail_count += 1
            if e.code in (404, 410, 502, 530) and fail_count >= 2:
                print(f"[CIEL] Control tunnel closed (HTTP {e.code}). Exiting.", flush=True)
                stop_event.set()
                _force_exit()
        except Exception:
            fail_count += 1
            if fail_count >= 5:
                print("[CIEL] Lost connection to CIEL host. Exiting.", flush=True)
                stop_event.set()
                _force_exit()


threading.Thread(target=_control_heartbeat, daemon=True).start()


def run(cmd: str):
    print(f"[RUN] {cmd}", flush=True)
    subprocess.run(cmd, shell=True, check=True)


# 1. Install precompiled CUDA wheel for llama-cpp-python OpenAI server
run(
    f"{sys.executable} -m pip install -q huggingface_hub "
    "'llama-cpp-python[server]' "
    "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
)

# 2. Download Qwen3.8-27B GGUF
from huggingface_hub import HfApi, hf_hub_download

REPO_ID = "unsloth/Qwen3.8-27B-GGUF"
files = HfApi().list_repo_files(REPO_ID)
gguf_file = next(
    (f for f in files if f.lower().endswith(("q4_k_m.gguf", "ud-q4_k_xl.gguf"))),
    next(f for f in files if f.lower().endswith(".gguf")),
)
print(f"[DOWNLOAD] {REPO_ID} -> {gguf_file}", flush=True)
model_path = hf_hub_download(repo_id=REPO_ID, filename=gguf_file, local_dir="/tmp/model")

# 3. Download cloudflared
run(
    "wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
    "-O /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared"
)

# 4. Launch OpenAI-compatible server across both T4 GPUs
server = subprocess.Popen(
    [
        sys.executable, "-m", "llama_cpp.server",
        "--model", model_path,
        "--n_gpu_layers", "-1",
        "--tensor_split", "1", "1",
        "--n_ctx", "16384",
        "--host", "127.0.0.1",
        "--port", "8081",
    ],
    stdout=open("/tmp/llama.log", "w"),
    stderr=subprocess.STDOUT,
)

for _ in range(90):
    try:
        urllib.request.urlopen("http://127.0.0.1:8081/v1/models", timeout=2)
        break
    except Exception:
        time.sleep(2)
else:
    raise RuntimeError(open("/tmp/llama.log", errors="ignore").read()[-2000:])


class ProxyHandler(BaseHTTPRequestHandler):
    def _forward(self):
        if self.path.rstrip("/").endswith("/shutdown"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"shutting down")
            self.wfile.flush()
            stop_event.set()
            threading.Thread(target=lambda: (time.sleep(0.3), _force_exit()), daemon=True).start()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
        req = urllib.request.Request(
            f"http://127.0.0.1:8081{self.path}",
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(k, v)
                self.end_headers()
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    do_GET = _forward
    do_POST = _forward

    def log_message(self, *args):
        pass


proxy = ThreadingHTTPServer(("127.0.0.1", 8080), ProxyHandler)
threading.Thread(target=proxy.serve_forever, daemon=True).start()

tunnel = subprocess.Popen(
    ["cloudflared", "tunnel", "--url", "http://127.0.0.1:8080"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)


def _register_with_host(tunnel_url: str):
    if not CONTROL_URL:
        return
    payload = json.dumps({"session_id": SESSION_ID, "url": tunnel_url}).encode("utf-8")
    for _ in range(5):
        try:
            req = urllib.request.Request(
                f"{CONTROL_URL}/register",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "ciel-kaggle-worker"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=8)
            return
        except Exception:
            time.sleep(2)


def _drain_tunnel_logs():
    published = False
    for line in tunnel.stdout:
        if not published:
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                tunnel_url = f"{m.group(0)}/v1"
                print(f"CIEL_TUNNEL_URL={tunnel_url}", flush=True)
                threading.Thread(target=_register_with_host, args=(tunnel_url,), daemon=True).start()
                published = True


threading.Thread(target=_drain_tunnel_logs, daemon=True).start()

stop_event.wait(timeout=4 * 3600)
_force_exit()
