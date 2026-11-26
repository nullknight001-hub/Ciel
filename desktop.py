import json
import socket
import os
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import ciel
import config
from ciel import (
    MODELS,
    build_system_prompt,
    get_client_and_model,
    load_memories,
    run_turn,
    set_confirm_handler,
)


class CielDesktopGUI:
    def on_close(self):
        if self.current_alias == "kaggle":
            config.switch_model("auto")
        try:
            os.unlink("/tmp/ciel_dropdown.sock")
        except OSError:
            pass
        self.root.destroy()
    def __init__(self, root):
        self.root = root
        self.root.title("CIEL Dropdown")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.drop_w = min(1080, max(780, int(sw * 0.68)))
        self.drop_h = min(540, max(420, int(sh * 0.48)))
        self.drop_x = max(0, (sw - self.drop_w) // 2)
        self.is_visible = True
        self._animating = False
        self.pinned = False
        self.root.geometry(f"{self.drop_w}x48+{self.drop_x}+0")
        self.root.minsize(680, 48)
        self.root.wm_attributes("-topmost", True)
        

        self.bg_dark = "#121212"
        self.panel_dark = "#1e1e1e"
        self.border_dark = "#2d2d2d"
        self.text_light = "#e0e0e0"
        self.text_muted = "#aaaaaa"
        self.accent_color = "#007acc"
        self.root.configure(bg=self.bg_dark)

        self.current_alias = "auto"
        self.current_effort = "low"
        _, self.active_model = get_client_and_model(self.current_alias)
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self.is_processing = False

        set_confirm_handler(self.confirm_dangerous_command)
        self.create_widgets()
        self.append_system_msg(
            f"CIEL Online [{self.current_alias} -> {self.active_model}] ({len(load_memories())} memories loaded)."
        )
        _boot_prompt = os.environ.pop("CIEL_INITIAL_PROMPT", "").strip()
        if _boot_prompt:
            self.input_box.insert("1.0", _boot_prompt)
            self.input_box.focus_set()
        self.root.bind("<Escape>", lambda e: self.hide_dropdown())
        self.root.bind("<FocusOut>", self._on_focus_out)
        self._start_dropdown_ipc()
        self.root.after(15, self.show_dropdown)

    def confirm_dangerous_command(self, cmd: str) -> bool:
        """Thread-safe modal confirmation when a worker thread triggers a guarded shell command."""
        decision = {"allow": False}
        done = threading.Event()

        def ask():
            decision["allow"] = messagebox.askyesno(
                "CIEL Approval Required",
                f"CIEL wants to run a potentially destructive command:\n\n{cmd}\n\nAllow execution?",
                parent=self.root,
            )
            done.set()

        self.root.after(0, ask)
        done.wait()
        return decision["allow"]

    def create_widgets(self):
        # Ciel Cyan HUD Palette
        self.bg_dark = "#0d1117"
        self.panel_dark = "#141922"
        self.pill_dark = "#1c2331"
        self.border_dark = "#26303d"
        self.text_light = "#e6edf3"
        self.text_muted = "#8b949e"
        self.accent_color = "#38bdf8"
        self.accent_fg = "#081018"
        self.ok_color = "#3fb950"
        self.warn_color = "#f59e0b"

        self.root.configure(bg=self.border_dark)

        # 1px outer rim + 2px Ciel Cyan top glow bar
        self.shell = tk.Frame(self.root, bg=self.bg_dark)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))

        tk.Frame(self.shell, bg=self.accent_color, height=2).pack(fill=tk.X)

        # --- Compact 34px HUD Header ---
        header = tk.Frame(self.shell, bg=self.panel_dark, height=34)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left_hud = tk.Frame(header, bg=self.panel_dark)
        left_hud.pack(side=tk.LEFT, padx=10)

        tk.Label(
            left_hud, text="●", bg=self.panel_dark, fg=self.accent_color,
            font=("Sans", 9, "bold")
        ).pack(side=tk.LEFT)
        tk.Label(
            left_hud, text=" CIEL", bg=self.panel_dark, fg=self.text_light,
            font=("Sans", 9, "bold")
        ).pack(side=tk.LEFT, padx=(0, 8))

        # Model & Effort Pills
        self.model_var = tk.StringVar(value=getattr(self, "current_alias", "auto"))
        if hasattr(self, "on_model_changed"):
            model_menu = tk.OptionMenu(
                left_hud, self.model_var, "auto", "fast", "smart", "local",
                command=lambda _v: self.on_model_changed()
            )
            model_menu.config(
                bg=self.pill_dark, fg=self.text_light, activebackground=self.border_dark,
                activeforeground=self.accent_color, highlightthickness=0, bd=0,
                font=("Monospace", 8), indicatoron=False, padx=7, pady=1, cursor="hand2"
            )
            model_menu["menu"].config(
                bg=self.panel_dark, fg=self.text_light, activebackground=self.accent_color,
                activeforeground=self.accent_fg, bd=0, font=("Monospace", 8)
            )
            model_menu.pack(side=tk.LEFT, padx=(0, 5))

        self.effort_var = tk.StringVar(value=getattr(self, "current_effort", "low"))
        effort_menu = tk.OptionMenu(
            left_hud, self.effort_var, "low", "medium", "high",
            command=lambda _v: self.on_effort_changed()
        )
        effort_menu.config(
            bg=self.pill_dark, fg=self.text_muted, activebackground=self.border_dark,
            activeforeground=self.text_light, highlightthickness=0, bd=0,
            font=("Monospace", 8), indicatoron=False, padx=7, pady=1, cursor="hand2"
        )
        effort_menu["menu"].config(
            bg=self.panel_dark, fg=self.text_light, activebackground=self.accent_color,
            activeforeground=self.accent_fg, bd=0, font=("Monospace", 8)
        )
        effort_menu.pack(side=tk.LEFT, padx=(0, 6))

        short_model = str(getattr(self, "active_model", "")).split("/")[-1]
        self.model_pill = tk.Label(
            left_hud, text=short_model, bg=self.panel_dark, fg=self.text_muted,
            font=("Monospace", 8)
        )
        self.model_pill.pack(side=tk.LEFT)

        # Right HUD Action Controls
        right_hud = tk.Frame(header, bg=self.panel_dark)
        right_hud.pack(side=tk.RIGHT, padx=8)

        def _hud_btn(parent, label, cmd, fg=self.text_muted, bg=self.pill_dark):
            btn = tk.Button(
                parent, text=label, command=cmd, bg=bg, fg=fg,
                activebackground=self.border_dark, activeforeground=self.text_light,
                bd=0, highlightthickness=0, font=("Monospace", 8, "bold"),
                padx=7, pady=1, cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=2)
            return btn

        is_plan = bool(getattr(ciel, "plan_mode", False))
        self.plan_btn = _hud_btn(
            right_hud,
            "PLAN" if is_plan else "EXEC",
            self._toggle_hud_plan,
            fg=self.warn_color if is_plan else self.ok_color,
        )
        self.pin_btn = _hud_btn(right_hud, "PIN", self._toggle_hud_pin, fg=self.text_muted)
        _hud_btn(right_hud, "CLEAR", self._clear_hud_chat, fg=self.text_muted)
        _hud_btn(right_hud, "ESC", self.hide_dropdown, fg=self.text_muted)

        tk.Frame(self.shell, bg=self.border_dark, height=1).pack(fill=tk.X)

        # --- Scrollbar-Free Chat Stream ---
        chat_wrap = tk.Frame(self.shell, bg=self.bg_dark)
        chat_wrap.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 4))

        self.chat_area = tk.Text(
            chat_wrap,
            wrap=tk.WORD,
            bg=self.bg_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1f3a52",
            selectforeground=self.text_light,
            font=("Sans", 10),
            bd=0,
            highlightthickness=0,
            padx=4,
            pady=4,
            spacing1=2,
            spacing3=3,
            state=tk.DISABLED,
        )
        self.chat_area.pack(fill=tk.BOTH, expand=True)

        # Smooth Linux XWayland/Wayland wheel scrolling without visible scrollbar
        self.chat_area.bind("<Button-4>", lambda e: self.chat_area.yview_scroll(-3, "units"))
        self.chat_area.bind("<Button-5>", lambda e: self.chat_area.yview_scroll(3, "units"))

        self.chat_area.tag_config("user_header", foreground="#7dd3fc", font=("Sans", 9, "bold"), spacing1=6)
        self.chat_area.tag_config("user_body", foreground="#f0f6fc", font=("Sans", 10), lmargin1=10, lmargin2=10)
        self.chat_area.tag_config("ciel_header", foreground=self.accent_color, font=("Sans", 9, "bold"), spacing1=6)
        self.chat_area.tag_config("ciel_body", foreground="#d0d7de", font=("Sans", 10), lmargin1=10, lmargin2=10)
        self.chat_area.tag_config("tool_msg", foreground=self.warn_color, font=("Monospace", 8), lmargin1=12, lmargin2=12)
        self.chat_area.tag_config("system_msg", foreground="#6e7681", font=("Monospace", 8))

        # --- Floating Composer Card ---
        composer_outer = tk.Frame(self.shell, bg=self.bg_dark)
        composer_outer.pack(fill=tk.X, padx=14, pady=(4, 10))

        composer_rim = tk.Frame(composer_outer, bg=self.border_dark, padx=1, pady=1)
        composer_rim.pack(fill=tk.X)

        composer_box = tk.Frame(composer_rim, bg=self.panel_dark)
        composer_box.pack(fill=tk.X)

        self.input_box = tk.Text(
            composer_box,
            height=2,
            wrap=tk.WORD,
            bg=self.panel_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1f3a52",
            font=("Sans", 10),
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=8,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.input_box.bind("<Return>", self.handle_return_key)
        self.input_box.bind("<Shift-Tab>", lambda e: (self._toggle_hud_plan(), "break")[1])
        self.input_box.bind("<ISO_Left_Tab>", lambda e: (self._toggle_hud_plan(), "break")[1])

        self.send_btn = tk.Button(
            composer_box,
            text="Send ↵",
            command=self.send_message,
            bg=self.accent_color,
            fg=self.accent_fg,
            activebackground="#7dd3fc",
            activeforeground=self.accent_fg,
            disabledforeground="#1e293b",
            font=("Sans", 9, "bold"),
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=5,
            cursor="hand2",
        )
        self.send_btn.pack(side=tk.RIGHT, padx=8, pady=6)

        tk.Label(
            composer_outer,
            text="Enter send  ·  Shift+Enter newline  ·  Shift+Tab plan mode  ·  Esc hide",
            bg=self.bg_dark,
            fg="#56606d",
            font=("Monospace", 7),
            anchor="w",
        ).pack(fill=tk.X, padx=4, pady=(3, 0))

    def _toggle_hud_pin(self):
        self.pinned = not getattr(self, "pinned", False)
        if self.pinned:
            self.pin_btn.config(bg=self.accent_color, fg=self.accent_fg)
        else:
            self.pin_btn.config(bg=self.pill_dark, fg=self.text_muted)

    def _toggle_hud_plan(self):
        cur = bool(getattr(ciel, "plan_mode", False))
        new_state = not cur
        if hasattr(ciel, "plan_mode"):
            ciel.plan_mode = new_state
        self.plan_btn.config(
            text="PLAN" if new_state else "EXEC",
            fg=self.warn_color if new_state else self.ok_color,
        )
        self.append_system_msg(f"Mode switched to {'PLAN (read-only)' if new_state else 'EXEC'}.")

    def _clear_hud_chat(self):
        if getattr(self, "is_processing", False):
            return
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.delete("1.0", tk.END)
        self.chat_area.config(state=tk.DISABLED)
        self.append_system_msg("Session cleared.")


    def clear_chat(self):
        if self.is_processing:
            return
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.delete("1.0", tk.END)
        self.chat_area.config(state=tk.DISABLED)
        self.append_system_msg("Conversation cleared.")
    def on_model_changed(self, event=None):
        choice = self.model_var.get().strip().lower()
        if choice == "auto" or choice in MODELS:
            self.current_alias = choice
            ciel.current_alias = choice
            gen = getattr(self, "_switch_gen", 0) + 1
            self._switch_gen = gen
            self.append_system_msg(f"Switching model to {choice}...")

            def _apply_switch():
                status_msg = config.switch_model(choice)
                if getattr(self, "_switch_gen", 0) != gen:
                    return
                _, active_model = get_client_and_model(choice)
                self.root.after(0, lambda: setattr(self, "active_model", active_model))
                self.root.after(0, self.append_system_msg, status_msg.strip("[]"))

            threading.Thread(target=_apply_switch, daemon=True).start()
    def on_effort_changed(self, event=None):
        choice = self.effort_var.get().strip().lower()
        if choice in ("low", "medium", "high"):
            self.current_effort = choice
            ciel.current_effort = choice
            self.append_system_msg(f"Reasoning effort set to {self.current_effort}")

    def handle_return_key(self, event):
        if event.state & 0x1:
            return
        self.send_message()
        return "break"

    def append_system_msg(self, text):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.insert(tk.END, f"[SYSTEM] {text}\n", "system_msg")
        self.chat_area.see(tk.END)
        self.chat_area.config(state=tk.DISABLED)

    def append_text(self, text, tag):
        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.insert(tk.END, text, tag)
        self.chat_area.see(tk.END)
        self.chat_area.config(state=tk.DISABLED)

    def send_message(self):
        if self.is_processing:
            return
        user_text = self.input_box.get("1.0", tk.END).strip()
        if not user_text:
            return

        self.input_box.delete("1.0", tk.END)
        self.append_text("You:\n", "user_header")
        self.append_text(f"{user_text}\n\n", "user_body")
        self.messages.append({"role": "user", "content": user_text})

        self.is_processing = True
        self.send_btn.config(state=tk.DISABLED, text="...")
        self.append_text("CIEL:\n", "ciel_header")

        def worker():
            run_turn(
                self.messages,
                alias=self.current_alias,
                effort=self.current_effort,
                on_token=lambda tok: self.root.after(0, self.append_text, tok, "ciel_body"),
                on_tool=lambda info: self.root.after(0, self.append_text, f"\n[Tool] {info}\n", "tool_msg"),
            )
            self.root.after(0, self.finish_turn)

        threading.Thread(target=worker, daemon=True).start()

    def finish_turn(self):
        self.append_text("\n\n", "ciel_body")
        self.is_processing = False
        self.send_btn.config(state=tk.NORMAL, text="Send ↵")
        self.input_box.focus_set()


    def _on_focus_out(self, event=None):
        if not self.is_visible or self.pinned or self._animating:
            return
        def _check():
            if self.is_visible and not self.pinned and self.root.focus_displayof() is None:
                self.hide_dropdown()
        self.root.after(120, _check)

    def show_dropdown(self, selection: str = ""):
        if self._animating:
            return
        self._animating = True
        self.is_visible = True
        self.root.deiconify()
        self.root.wm_attributes("-topmost", True)
        steps = 5
        start_h = 48

        def _step(i: int):
            if i <= steps:
                cur_h = int(start_h + (self.drop_h - start_h) * (i / steps))
                self.root.geometry(f"{self.drop_w}x{cur_h}+{self.drop_x}+0")
                self.root.after(10, _step, i + 1)
            else:
                self._animating = False
                self.root.lift()
                self.root.focus_force()
                self.input_box.focus_set()
                if selection:
                    wrapped = f"Inspect/help with this selection from my screen:\n\n```\n{selection}\n```"
                    self.input_box.delete("1.0", "end")
                    self.input_box.insert("1.0", wrapped)

        _step(1)

    def hide_dropdown(self):
        if self._animating or not self.is_visible:
            return
        self._animating = True
        steps = 4
        end_h = 48

        def _step(i: int):
            if i <= steps:
                cur_h = int(self.drop_h - (self.drop_h - end_h) * (i / steps))
                self.root.geometry(f"{self.drop_w}x{cur_h}+{self.drop_x}+0")
                self.root.after(10, _step, i + 1)
            else:
                self._animating = False
                self.is_visible = False
                self.root.withdraw()

        _step(1)

    def toggle_dropdown(self, selection: str = ""):
        if self.is_visible and not selection:
            self.hide_dropdown()
        else:
            self.show_dropdown(selection=selection)

    def _start_dropdown_ipc(self):
        sock_path = "/tmp/ciel_dropdown.sock"
        try:
            os.unlink(sock_path)
        except OSError:
            pass

        def _serve():
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            srv.listen(4)
            while True:
                try:
                    conn, _ = srv.accept()
                    with conn:
                        raw = conn.recv(8192).decode("utf-8", errors="ignore").strip()
                        if not raw:
                            continue
                        payload = json.loads(raw)
                        if payload.get("cmd") == "toggle":
                            sel = payload.get("selection", "")
                            self.root.after(0, self.toggle_dropdown, sel)
                except Exception:
                    pass

        threading.Thread(target=_serve, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk(className="ciel-dropdown")
    app = CielDesktopGUI(root)
    root.mainloop()
