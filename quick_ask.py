#!/home/ilham/Projects/Ciel_V3/venv/bin/python
import os
import shutil
import subprocess
import sys

def grab_selection() -> str:
    for cmd in (
        ["wl-paste", "--primary", "--no-newline"],
        ["wl-paste", "--no-newline"],
        ["xclip", "-o", "-selection", "primary"],
        ["xclip", "-o", "-selection", "clipboard"],
    ):
        if shutil.which(cmd[0]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0).stdout.strip()
                if out:
                    return out[:2500]
            except Exception:
                pass
    return ""

def main():
    clip = grab_selection()
    env = os.environ.copy()
    if clip:
        env["CIEL_INITIAL_PROMPT"] = f"Inspect/help with this selection from my screen:\n\n```\n{clip}\n```"
    subprocess.Popen(
        ["/home/ilham/Projects/Ciel_V3/venv/bin/python", "/home/ilham/Projects/Ciel_V3/desktop.py"],
        env=env,
        start_new_session=True,
    )

if __name__ == "__main__":
    main()
