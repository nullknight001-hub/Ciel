import re
import tkinter.font as tkfont
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
        self._stop_thinking_anim()
        self._format_turn_code_blocks()

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
        available_fonts = set(tkfont.families())
        self.ui_font = "Inter" if "Inter" in available_fonts else "Sans"
        self.mono_font = "JetBrains Mono" if "JetBrains Mono" in available_fonts else "Monospace"
        # Obsidian & Ciel Glass Palette
        self.bg_dark = "#0b0e14"
        self.panel_dark = "#111622"
        self.card_dark = "#151c2b"
        self.pill_dark = "#192234"
        self.pill_hover = "#222f46"
        self.border_dark = "#1e293b"
        self.text_light = "#e6edf3"
        self.text_muted = "#8b949e"
        self.accent_color = "#38bdf8"
        self.violet_accent = "#a78bfa"
        self.accent_fg = "#061019"
        self.ok_color = "#34d399"
        self.warn_color = "#fbbf24"

        self.root.configure(bg=self.border_dark)

        # Outer 1px rim + 2px luminous Ciel Cyan top edge
        self.shell = tk.Frame(self.root, bg=self.bg_dark)
        self.shell.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
        tk.Frame(self.shell, bg=self.accent_color, height=2).pack(fill=tk.X)

        # --- Sleek 38px Command HUD Header ---
        header = tk.Frame(self.shell, bg=self.panel_dark, height=38)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        left_hud = tk.Frame(header, bg=self.panel_dark)
        left_hud.pack(side=tk.LEFT, padx=14)

        self.status_dot = tk.Label(
            left_hud, text="◈", bg=self.panel_dark, fg=self.accent_color,
            font=(self.ui_font, 10, "bold")
        )
        self.status_dot.pack(side=tk.LEFT)

        tk.Label(
            left_hud, text=" CIEL ", bg=self.panel_dark, fg=self.text_light,
            font=(self.ui_font, 9, "bold")
        ).pack(side=tk.LEFT)

        self.status_badge = tk.Label(
            left_hud, text="READY", bg="#0f293a", fg=self.accent_color,
            font=(self.mono_font, 7, "bold"), padx=6, pady=1
        )
        self.status_badge.pack(side=tk.LEFT, padx=(2, 10))

        self.model_var = tk.StringVar(value=getattr(self, "current_alias", "auto"))
        self.effort_var = tk.StringVar(value=getattr(self, "current_effort", "low"))

        def _make_pill(parent, text, cmd, fg=self.text_light, bg=self.pill_dark):
            lbl = tk.Label(
                parent, text=text, bg=bg, fg=fg,
                font=(self.mono_font, 8, "bold"), padx=8, pady=2, cursor="hand2"
            )
            lbl.pack(side=tk.LEFT, padx=2)
            lbl.bind("<Button-1>", lambda _e: cmd())
            lbl.bind("<Enter>", lambda _e: lbl.config(bg=self.pill_hover))
            lbl.bind("<Leave>", lambda _e: lbl.config(bg=getattr(lbl, "_base_bg", bg)))
            lbl._base_bg = bg
            return lbl

        self.alias_pill = _make_pill(
            left_hud, f"⚡ {self.model_var.get()}", self._cycle_hud_model, fg="#7dd3fc"
        )
        self.effort_pill = _make_pill(
            left_hud, f"◈ {self.effort_var.get()}", self._cycle_hud_effort, fg=self.violet_accent
        )

        short_model = str(getattr(self, "active_model", "")).split("/")[-1]
        self.model_pill = tk.Label(
            left_hud, text=short_model, bg=self.panel_dark, fg="#64748b",
            font=(self.mono_font, 8)
        )
        self.model_pill.pack(side=tk.LEFT, padx=(6, 0))

        # Right Action Pills
        right_hud = tk.Frame(header, bg=self.panel_dark)
        right_hud.pack(side=tk.RIGHT, padx=12)

        _make_pill(right_hud, "+ SEL", self._insert_screen_selection, fg="#7dd3fc")

        is_plan = bool(getattr(ciel, "plan_mode", False))
        self.plan_btn = _make_pill(
            right_hud,
            "PLAN" if is_plan else "EXEC",
            self._toggle_hud_plan,
            fg=self.warn_color if is_plan else self.ok_color,
        )
        self.pin_btn = _make_pill(right_hud, "PIN", self._toggle_hud_pin, fg=self.text_muted)
        _make_pill(right_hud, "CLEAR", self._clear_hud_chat, fg=self.text_muted)
        _make_pill(right_hud, "ESC", self.hide_dropdown, fg=self.text_muted)

        tk.Frame(self.shell, bg=self.border_dark, height=1).pack(fill=tk.X)

        # --- Floating Composer Card (anchored BOTTOM first) ---
        composer_outer = tk.Frame(self.shell, bg=self.bg_dark)
        composer_outer.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(4, 12))

        self.composer_rim = tk.Frame(composer_outer, bg=self.accent_color, padx=1, pady=1)
        self.composer_rim.pack(fill=tk.X)

        composer_box = tk.Frame(self.composer_rim, bg=self.card_dark)
        composer_box.pack(fill=tk.X)

        self.input_box = tk.Text(
            composer_box,
            height=2,
            wrap=tk.WORD,
            bg=self.card_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1e3a5f",
            font=(self.ui_font, 10),
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=9,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.input_box.bind("<Return>", self.handle_return_key)
        self.input_box.bind("<KeyRelease>", self._auto_resize_input)
        self.input_box.bind("<FocusIn>", lambda _e: self.composer_rim.config(bg=self.accent_color))
        self.input_box.bind("<FocusOut>", lambda _e: self.composer_rim.config(bg=self.border_dark))
        self.input_box.bind("<Shift-Tab>", lambda _e: (self._toggle_hud_plan(), "break")[1])
        self.input_box.bind("<ISO_Left_Tab>", lambda _e: (self._toggle_hud_plan(), "break")[1])

        self.send_btn = tk.Button(
            composer_box,
            text="Send ↵",
            command=self.send_message,
            bg=self.accent_color,
            fg=self.accent_fg,
            activebackground="#7dd3fc",
            activeforeground=self.accent_fg,
            disabledforeground="#0f172a",
            font=(self.ui_font, 9, "bold"),
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=6,
            cursor="hand2",
        )
        self.send_btn.pack(side=tk.RIGHT, padx=10, pady=8)

        tk.Label(
            composer_outer,
            text="Enter send · Shift+Enter newline · Shift+Tab plan · +SEL paste screen · Esc hide",
            bg=self.bg_dark,
            fg="#475569",
            font=(self.mono_font, 7),
            anchor="w",
        ).pack(fill=tk.X, padx=4, pady=(4, 0))

        # --- Scrollbar-Free Chat Stream ---
        chat_wrap = tk.Frame(self.shell, bg=self.bg_dark)
        chat_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(10, 4))

        self.chat_area = tk.Text(
            chat_wrap,
            height=1,
            wrap=tk.WORD,
            bg=self.bg_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1e3a5f",
            selectforeground=self.text_light,
            font=(self.ui_font, 10),
            bd=0,
            highlightthickness=0,
            padx=4,
            pady=4,
            spacing1=2,
            spacing3=4,
            state=tk.DISABLED,
        )
        self.chat_area.pack(fill=tk.BOTH, expand=True)
        self.chat_area.bind("<Button-4>", lambda _e: self.chat_area.yview_scroll(-3, "units"))
        self.chat_area.bind("<Button-5>", lambda _e: self.chat_area.yview_scroll(3, "units"))

        self.chat_area.tag_config("user_header", foreground="#93c5fd", font=(self.ui_font, 9, "bold"), spacing1=8)
        self.chat_area.tag_config("user_body", foreground="#f8fafc", font=(self.ui_font, 10), lmargin1=12, lmargin2=12)
        self.chat_area.tag_config("ciel_header", foreground=self.accent_color, font=(self.ui_font, 9, "bold"), spacing1=8)
        self.chat_area.tag_config("ciel_body", foreground="#cbd5e1", font=(self.ui_font, 10), lmargin1=12, lmargin2=12)
        self.chat_area.tag_config(
            "tool_msg", foreground=self.warn_color, background="#101725",
            font=(self.mono_font, 8), lmargin1=14, lmargin2=14
        )
        self.chat_area.tag_config("system_msg", foreground="#64748b", font=(self.mono_font, 8))
        self.chat_area.tag_config(
            "code_block", foreground="#e2e8f0", background="#0f1520",
            font=(self.mono_font, 9), lmargin1=14, lmargin2=14, rmargin=14,
            spacing1=3, spacing3=4
        )
        self.chat_area.tag_config(
            "inline_code", foreground="#7dd3fc", background="#131c2b",
            font=(self.mono_font, 9)
        )

    def _auto_resize_input(self, _event=None):
        lines_cnt = int(self.input_box.index("end-1c").split(".")[0])
        self.input_box.config(height=max(2, min(5, lines_cnt)))

    def _cycle_hud_model(self):
        order = ["auto", "fast", "smart", "local"]
        cur = self.model_var.get()
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "auto"
        self.model_var.set(nxt)
        self.alias_pill.config(text=f"⚡ {nxt}")
        if hasattr(self, "on_model_changed"):
            self.on_model_changed()
            self.root.after(400, lambda: self.model_pill.config(text=str(getattr(self, "active_model", "")).split("/")[-1]))

    def _cycle_hud_effort(self):
        order = ["low", "medium", "high"]
        cur = self.effort_var.get()
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "low"
        self.effort_var.set(nxt)
        self.effort_pill.config(text=f"◈ {nxt}")
        if hasattr(self, "on_effort_changed"):
            self.on_effort_changed()

    def _insert_screen_selection(self):
        import shutil, subprocess
        for cmd in (
            ["wl-paste", "--primary", "--no-newline"],
            ["wl-paste", "--no-newline"],
            ["xclip", "-o", "-selection", "primary"],
        ):
            if shutil.which(cmd[0]):
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True, timeout=0.6).stdout.strip()
                    if out:
                        self.input_box.insert(tk.END, f"\n```\n{out[:2500]}\n```\n")
                        self._auto_resize_input()
                        self.input_box.focus_set()
                        return
                except Exception:
                    pass
        self.append_system_msg("No active screen selection or clipboard text found.")

    def _toggle_hud_pin(self):
        self.pinned = not getattr(self, "pinned", False)
        if self.pinned:
            self.pin_btn._base_bg = self.accent_color
            self.pin_btn.config(bg=self.accent_color, fg=self.accent_fg)
        else:
            self.pin_btn._base_bg = self.pill_dark
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
        self.status_badge.config(text="THINKING", bg="#33270d", fg=self.warn_color)
        self.append_text("CIEL:\n", "ciel_header")
        self.chat_area.mark_set("resp_start", tk.END)
        self.chat_area.mark_gravity("resp_start", tk.LEFT)
        self._start_thinking_anim()

        def worker():
            run_turn(
                self.messages,
                alias=self.current_alias,
                effort=self.current_effort,
                on_token=lambda tok: self.root.after(0, self.append_text, tok, "ciel_body"),
                on_tool=lambda info: self.root.after(0, self._append_tool_line, info),
            )
            self.root.after(0, self.finish_turn)

        threading.Thread(target=worker, daemon=True).start()

    def finish_turn(self):
        self.append_text("\n\n", "ciel_body")
        self.is_processing = False
        self.send_btn.config(state=tk.NORMAL, text="Send ↵")
        self.status_badge.config(text="READY", bg="#0f293a", fg=self.accent_color)
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


    def _append_tool_line(self, info: str):
        self.append_text(f"\n[Tool] {info}\n", "tool_msg")
        self.chat_area.mark_set("resp_start", tk.END)
        self.chat_area.mark_gravity("resp_start", tk.LEFT)

    def _start_thinking_anim(self):
        self._anim_idx = 0
        self._tick_thinking_anim()

    def _tick_thinking_anim(self):
        if not getattr(self, "is_processing", False):
            return
        badge_frames = ("◈ · ·", "· ◈ ·", "· · ◈")
        btn_frames = ("·  ", "·· ", "···")
        idx = getattr(self, "_anim_idx", 0)
        self.status_badge.config(text=badge_frames[idx % 3], bg="#2b210b", fg=self.warn_color)
        self.send_btn.config(text=btn_frames[idx % 3])
        self._anim_idx = idx + 1
        self._think_after_id = self.root.after(260, self._tick_thinking_anim)

    def _stop_thinking_anim(self):
        after_id = getattr(self, "_think_after_id", None)
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._think_after_id = None
        if hasattr(self, "_auto_resize_input"):
            self._auto_resize_input()

    def _copy_code_to_clipboard(self, code_text: str, btn: tk.Label):
        import shutil, subprocess
        copied = False
        if shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy"], input=code_text, text=True, timeout=0.6, check=True)
                copied = True
            except Exception:
                pass
        if not copied:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(code_text)
            except Exception:
                pass
        btn.config(text="Copied ✓", fg=self.ok_color)
        self.root.after(1400, lambda: btn.config(text="Copy", fg=self.accent_color) if btn.winfo_exists() else None)

    def _insert_rich_prose(self, text: str):
        parts = re.split(r"(`[^`\n]+`)", text)
        for part in parts:
            if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
                self.chat_area.insert(tk.END, part[1:-1], "inline_code")
            elif part:
                self.chat_area.insert(tk.END, part, "ciel_body")

    def _format_turn_code_blocks(self):
        try:
            raw = self.chat_area.get("resp_start", tk.END)
        except Exception:
            return
        if "```" not in raw and "`" not in raw:
            return

        fence_re = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
        matches = list(fence_re.finditer(raw))
        if not matches and "`" not in raw:
            return

        self.chat_area.config(state=tk.NORMAL)
        self.chat_area.delete("resp_start", tk.END)

        pos = 0
        for m in matches:
            before = raw[pos:m.start()]
            if before:
                self._insert_rich_prose(before)

            lang = (m.group(1) or "code").strip()
            code = m.group(2).rstrip("\n")

            bar = tk.Frame(self.chat_area, bg="#162032", padx=8, pady=2)
            tk.Label(
                bar, text=lang.upper(), bg="#162032", fg="#64748b",
                font=(self.mono_font, 7, "bold")
            ).pack(side=tk.LEFT, padx=(0, 12))

            copy_lbl = tk.Label(
                bar, text="Copy", bg="#1e2d44", fg=self.accent_color,
                font=(self.mono_font, 7, "bold"), padx=7, pady=1, cursor="hand2"
            )
            copy_lbl.pack(side=tk.RIGHT)
            copy_lbl.bind("<Button-1>", lambda _e, c=code, b=copy_lbl: self._copy_code_to_clipboard(c, b))

            self.chat_area.insert(tk.END, "\n", "ciel_body")
            self.chat_area.window_create(tk.END, window=bar)
            self.chat_area.insert(tk.END, "\n" + code + "\n", "code_block")
            pos = m.end()

        tail = raw[pos:]
        if tail:
            self._insert_rich_prose(tail)

        self.chat_area.see(tk.END)
        self.chat_area.config(state=tk.DISABLED)


if __name__ == "__main__":
    root = tk.Tk(className="ciel-dropdown")
    app = CielDesktopGUI(root)
    root.mainloop()
