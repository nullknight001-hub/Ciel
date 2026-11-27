#!/usr/bin/env bash
SOCK="/tmp/ciel_dropdown.sock"
if [ ! -S "$SOCK" ]; then
    systemctl --user start ciel-hud.service
    sleep 0.6
fi
/home/ilham/Projects/Ciel_V3/venv/bin/python -c 'import json, socket; s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(0.5); s.connect("/tmp/ciel_dropdown.sock"); s.sendall(json.dumps({"cmd": "live_voice"}).encode("utf-8")); s.close()'
