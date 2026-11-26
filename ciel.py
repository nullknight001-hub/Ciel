import os
import subprocess
import sys
import config
from config import BASE_DIR, MODELS, alias_available, get_client_and_model
from engine import build_system_prompt, run_turn
from memory import load_memories
from tools import see_screen, set_confirm_handler
from config import BASE_DIR, MODELS, alias_available, get_client_and_model, switch_model

__all__ = [
    "BASE_DIR",
    "MODELS",
    "build_system_prompt",
    "get_client_and_model",
    "load_memories",
    "run_turn",
    "see_screen",
    "set_confirm_handler",
]


def main():
    _, active_model = get_client_and_model(config.current_alias)
    messages = [{"role": "system", "content": build_system_prompt()}]

    print(
        f"CIEL Online [{config.current_alias} -> {active_model}] ({len(load_memories())} memories). "
        "Commands: '/model', '/effort', '/live', 'quit'"
    )

    while True:
        try:
            user_input = input(f"\nYou ({config.current_alias}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break

        if user_input.lower() == "/live":
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, "live_voice.py")])
            except KeyboardInterrupt:
                pass
            print(f"\nCIEL Online [{config.current_alias} -> {active_model}] (Resumed text chat)")
            continue

        if user_input.startswith("/model"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                print(f"\nActive: {config.current_alias} ({active_model})")
                for k, v in MODELS.items():
                    marker = "*" if k == config.current_alias else " "
                    status = "ready" if alias_available(k) else "missing key"
                    print(f" {marker} /model {k:<10} -> {v['provider']}: {v['model']} [{status}]")
            else:
                print(switch_model(parts[1]))
                _, active_model = get_client_and_model(config.current_alias)
            continue

        if cmd_lower.startswith('/plan') if 'cmd_lower' in locals() else user_input.strip().lower().startswith('/plan'):
            import engine as _eng
            arg = user_input.strip()[5:].strip().lower()
            state = _eng.set_plan_mode(True) if arg == 'on' else (_eng.set_plan_mode(False) if arg == 'off' else _eng.toggle_plan_mode())
            msg = f"[Plan Mode: {'ON (read-only)' if state else 'OFF (edits enabled)'}]"
            print(msg)
            continue
        if user_input.strip().lower() == '/undo':
            from tools.files import undo_last_edit as _undo
            msg = f"[Undo] {_undo()}"
            print(msg)
            continue
        if user_input.startswith("/effort"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                print(f"\nActive reasoning effort: {config.current_effort}")
                for level in ("low", "medium", "high"):
                    marker = "*" if level == config.current_effort else " "
                    print(f" {marker} /effort {level}")
            else:
                choice = parts[1].strip().lower()
                if choice in ("low", "medium", "high"):
                    config.current_effort = choice
                    print(f"[Reasoning effort set to '{config.current_effort}']")
                else:
                    print(f"Unknown effort level '{choice}'. Options: low, medium, high.")
            continue

        messages.append({"role": "user", "content": user_input})
        started_reply = False

        def cli_on_token(tok: str):
            nonlocal started_reply
            if not started_reply:
                print("\nCIEL: ", end="", flush=True)
                started_reply = True
            print(tok, end="", flush=True)

        run_turn(
            messages,
            alias=config.current_alias,
            effort=config.current_effort,
            on_token=cli_on_token,
        )
        if started_reply:
            print()


if __name__ == "__main__":
    main()