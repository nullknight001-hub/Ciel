#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

CMD="\"$PYTHON_BIN\" \"$SCRIPT_DIR/live_voice.py\"; exec bash"

if command -v konsole &> /dev/null; then
    konsole -e bash -c "$CMD"
elif command -v alacritty &> /dev/null; then
    alacritty -e bash -c "$CMD"
elif command -v kitty &> /dev/null; then
    kitty bash -c "$CMD"
else
    xterm -e bash -c "$CMD"
fi