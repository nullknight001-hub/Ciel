import math
from live_voice import LiveVoiceEngine
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

        self.pill_w = 468
        self.pill_h = 68
        self.full_w = min(920, max(680, int(sw * 0.58)))
        self.full_h = min(460, max(340, int(sh * 0.42)))

        self.drop_w = self.pill_w
        self.drop_h = self.pill_h
        self.drop_x = max(0, (sw - self.drop_w) // 2)
        self.drop_y = 18

        self.is_visible = True
        self._animating = False
        self.pinned = False
        self.drawer_open = False
        self.auto_hide_after_speak = True
        self._pointer_inside = False
        self._auto_hide_id = None
        self._slide_job_id = None

        self._orb_phase = 0.0
        self._orb_level = 0.0
        self._smooth_lvl = 0.0
        self._orb_state = "IDLE"
        self._anim_idx = 0
        self._think_after_id = None

        self.bg_dark = "#0b0f17"
        self.panel_dark = "#101622"
        self.card_dark = "#151d2d"
        self.pill_dark = "#182133"
        self.pill_hover = "#222f47"
        self.border_dark = "#253248"
        self.text_light = "#f1f5f9"
        self.text_muted = "#8a96ab"
        self.accent_color = "#38bdf8"
        self.violet_accent = "#a78bfa"
        self.accent_fg = "#061019"
        self.ok_color = "#34d399"
        self.warn_color = "#fbbf24"

        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        try:
            self.root.wm_attributes("-alpha", 0.94)
        except Exception:
            pass
        self.root.geometry(f"{self.drop_w}x{self.drop_h}+{self.drop_x}+{self.drop_y}")
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
        self.root.bind("<Enter>", self._on_pointer_enter)
        self.root.bind("<Leave>", self._on_pointer_leave)
        self._start_dropdown_ipc()
        self.root.after(15, self.show_dropdown)

    def _draw_rounded_rect(self, canvas, x1, y1, x2, y2, r, **kwargs):
        r = max(2, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
        pts = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1, x2, y1 + r,
            x2, y2 - r,
            x2, y2, x2 - r, y2,
            x1 + r, y2,
            x1, y2, x1, y2 - r,
            x1, y1 + r,
            x1, y1, x1 + r, y1,
        ]
        return canvas.create_polygon(pts, smooth=True, **kwargs)

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

        self.pill_canvas = tk.Canvas(
            self.root,
            width=self.drop_w,
            height=self.drop_h,
            bg=self.bg_dark,
            bd=0,
            highlightthickness=0,
        )
        self.pill_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.pill_canvas.bind("<Configure>", lambda _e: self._redraw_pill_shell())

        self.top_capsule = tk.Frame(self.root, bg=self.panel_dark)
        self.top_capsule.place(x=12, y=6, width=self.pill_w - 24, height=self.pill_h - 12)

        self.orb_canvas = tk.Canvas(
            self.top_capsule,
            width=50,
            height=50,
            bg=self.panel_dark,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.orb_canvas.pack(side=tk.LEFT, padx=(4, 10), pady=1)
        self.orb_canvas.bind("<Button-1>", lambda _e: self._toggle_live_voice())

        text_col = tk.Frame(self.top_capsule, bg=self.panel_dark)
        text_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=5)

        self.siri_subtitle = tk.Label(
            text_col,
            text="Idle",
            bg=self.panel_dark,
            fg=self.text_light,
            font=(self.ui_font, 12, "bold"),
            anchor="w",
        )
        self.siri_subtitle.pack(fill=tk.X)

        self.siri_substatus = tk.Label(
            text_col,
            text="Alt+Space • Ask Ciel or press V",
            bg=self.panel_dark,
            fg=self.text_muted,
            font=(self.ui_font, 9),
            anchor="w",
        )
        self.siri_substatus.pack(fill=tk.X)

        right_actions = tk.Frame(self.top_capsule, bg=self.panel_dark)
        right_actions.pack(side=tk.RIGHT, padx=(4, 8))

        self.live_pill = tk.Label(
            right_actions,
            text="🎙",
            bg=self.panel_dark,
            fg=self.text_muted,
            font=(self.ui_font, 12),
            padx=6,
            pady=2,
            cursor="hand2",
        )
        self.live_pill.pack(side=tk.LEFT, padx=(0, 6))
        self.live_pill.bind("<Button-1>", lambda _e: self._toggle_live_voice())
        self.live_pill.bind("<Enter>", lambda _e: self.live_pill.config(fg=self.text_light))
        self.live_pill.bind("<Leave>", lambda _e: self.live_pill.config(
            fg=self.ok_color if getattr(getattr(self, "live_voice", None), "running", False) else self.text_muted
        ))

        self.chevron_btn = tk.Label(
            right_actions,
            text="⌄",
            bg=self.panel_dark,
            fg=self.text_muted,
            font=(self.ui_font, 14, "bold"),
            padx=6,
            pady=0,
            cursor="hand2",
        )
        self.chevron_btn.pack(side=tk.LEFT)
        self.chevron_btn.bind("<Button-1>", lambda _e: self._toggle_drawer())
        self.chevron_btn.bind("<Enter>", lambda _e: self.chevron_btn.config(fg=self.text_light))
        self.chevron_btn.bind("<Leave>", lambda _e: self.chevron_btn.config(
            fg=self.accent_color if getattr(self, "drawer_open", False) else self.text_muted
        ))

        self.drawer_frame = tk.Frame(self.root, bg=self.panel_dark)

        def _make_pill(parent, text, cmd, fg=self.text_light, bg=self.pill_dark):
            lbl = tk.Label(
                parent, text=text, bg=bg, fg=fg,
                font=(self.mono_font, 8, "bold"), padx=7, pady=2, cursor="hand2"
            )
            lbl.pack(side=tk.LEFT, padx=2)
            lbl.bind("<Button-1>", lambda _e: cmd())
            lbl.bind("<Enter>", lambda _e: lbl.config(bg=self.pill_hover))
            lbl.bind("<Leave>", lambda _e: lbl.config(bg=getattr(lbl, "_base_bg", bg)))
            lbl._base_bg = bg
            return lbl

        self.dev_bar = tk.Frame(self.drawer_frame, bg=self.panel_dark)
        self.dev_bar.pack(fill=tk.X, padx=14, pady=(6, 4))

        self.model_var = tk.StringVar(value=getattr(self, "current_alias", "auto"))
        self.effort_var = tk.StringVar(value=getattr(self, "current_effort", "low"))

        self.alias_pill = _make_pill(self.dev_bar, f"⚡ {self.model_var.get()}", self._cycle_hud_model, fg="#7dd3fc")
        self.effort_pill = _make_pill(self.dev_bar, f"◈ {self.effort_var.get()}", self._cycle_hud_effort, fg=self.violet_accent)

        short_model = str(getattr(self, "active_model", "")).split("/")[-1]
        self.model_pill = tk.Label(
            self.dev_bar, text=short_model, bg=self.panel_dark, fg="#64748b",
            font=(self.mono_font, 8)
        )
        self.model_pill.pack(side=tk.LEFT, padx=(6, 0))

        is_plan = bool(getattr(ciel, "plan_mode", False))
        self.plan_btn = _make_pill(
            self.dev_bar, "PLAN" if is_plan else "EXEC", self._toggle_hud_plan,
            fg=self.warn_color if is_plan else self.ok_color
        )
        self.pin_btn = _make_pill(self.dev_bar, "PIN", self._toggle_hud_pin, fg=self.text_muted)
        _make_pill(self.dev_bar, "+SEL", self._insert_screen_selection, fg="#7dd3fc")
        _make_pill(self.dev_bar, "CLEAR", self._clear_hud_chat, fg=self.text_muted)

        composer_rim = tk.Frame(self.drawer_frame, bg=self.border_dark, padx=1, pady=1)
        composer_rim.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(4, 12))
        composer_box = tk.Frame(composer_rim, bg=self.card_dark)
        composer_box.pack(fill=tk.X)

        self.input_box = tk.Text(
            composer_box,
            height=1,
            wrap=tk.WORD,
            bg=self.card_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1e3a5f",
            font=(self.ui_font, 10),
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=6,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.input_box.bind("<Return>", self.handle_return_key)
        self.input_box.bind("<KeyRelease>", self._auto_resize_input)
        self.input_box.bind("<FocusIn>", lambda _e: composer_rim.config(bg=self.accent_color))
        self.input_box.bind("<FocusOut>", lambda _e: composer_rim.config(bg=self.border_dark))
        self.input_box.bind("<Shift-Tab>", lambda _e: (self._toggle_hud_plan(), "break")[1])
        self.input_box.bind("<ISO_Left_Tab>", lambda _e: (self._toggle_hud_plan(), "break")[1])

        self.send_btn = tk.Button(
            composer_box,
            text="↑",
            command=self.send_message,
            bg=self.accent_color,
            fg=self.accent_fg,
            activebackground="#7dd3fc",
            activeforeground=self.accent_fg,
            font=(self.ui_font, 10, "bold"),
            bd=0,
            highlightthickness=0,
            padx=10,
            pady=3,
            cursor="hand2",
        )
        self.send_btn.pack(side=tk.RIGHT, padx=6, pady=4)

        chat_wrap = tk.Frame(self.drawer_frame, bg=self.panel_dark)
        chat_wrap.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=14, pady=(4, 4))

        self.chat_area = tk.Text(
            chat_wrap,
            height=1,
            wrap=tk.WORD,
            bg=self.panel_dark,
            fg=self.text_light,
            insertbackground=self.accent_color,
            selectbackground="#1e3a5f",
            selectforeground=self.text_light,
            font=(self.ui_font, 10),
            bd=0,
            highlightthickness=0,
            padx=4,
            pady=2,
            spacing1=2,
            spacing3=4,
            state=tk.DISABLED,
        )
        self.chat_area.pack(fill=tk.BOTH, expand=True)
        self.chat_area.bind("<Button-4>", lambda _e: self.chat_area.yview_scroll(-3, "units"))
        self.chat_area.bind("<Button-5>", lambda _e: self.chat_area.yview_scroll(3, "units"))

        self.chat_area.tag_config("user_header", foreground="#93c5fd", font=(self.ui_font, 8, "bold"), spacing1=4)
        self.chat_area.tag_config("user_body", foreground="#e2e8f0", font=(self.ui_font, 9), lmargin1=8, lmargin2=8)
        self.chat_area.tag_config("ciel_header", foreground=self.accent_color, font=(self.ui_font, 8, "bold"), spacing1=4)
        self.chat_area.tag_config("ciel_body", foreground="#f8fafc", font=(self.ui_font, 10), lmargin1=8, lmargin2=8)
        self.chat_area.tag_config(
            "tool_msg", foreground=self.warn_color, background="#101725",
            font=(self.mono_font, 8), lmargin1=10, lmargin2=10
        )
        self.chat_area.tag_config("system_msg", foreground="#64748b", font=(self.mono_font, 8))
        self.chat_area.tag_config(
            "code_block", foreground="#e2e8f0", background="#0f1520",
            font=(self.mono_font, 9), lmargin1=10, lmargin2=10, rmargin=10,
            spacing1=3, spacing3=4
        )
        self.chat_area.tag_config(
            "inline_code", foreground="#7dd3fc", background="#131c2b",
            font=(self.mono_font, 9)
        )

        self._redraw_pill_shell()
        self._tick_siri_orb()

    def _redraw_pill_shell(self):
        if not hasattr(self, "pill_canvas") or not self.pill_canvas.winfo_exists():
            return
        w = max(120, self.root.winfo_width())
        h = max(48, self.root.winfo_height())
        c = self.pill_canvas
        c.delete("all")
        radius = 28 if not getattr(self, "drawer_open", False) else 22
        self._draw_rounded_rect(
            c, 2, 2, w - 2, h - 2, radius,
            fill=self.panel_dark, outline=self.border_dark, width=1
        )
        top_h = min(h - 10, self.pill_h - 10)
        self.top_capsule.place(x=14, y=5, width=max(100, w - 28), height=max(40, top_h))
        if getattr(self, "drawer_open", False):
            self.drawer_frame.place(x=8, y=self.pill_h, width=max(100, w - 16), height=max(60, h - self.pill_h - 8))
        else:
            self.drawer_frame.place_forget()

    def _tick_siri_orb(self):
        if not hasattr(self, "orb_canvas") or not self.orb_canvas.winfo_exists():
            return
        c = self.orb_canvas
        c.delete("all")
        cx, cy = 25.0, 25.0
        state = getattr(self, "_orb_state", "IDLE")

        target_lvl = min(1.0, max(0.0, getattr(self, "_orb_level", 0.0) * 10.0))
        prev_lvl = getattr(self, "_smooth_lvl", 0.0)
        lvl = prev_lvl + (target_lvl - prev_lvl) * 0.25
        self._smooth_lvl = lvl

        speed = 0.20 if state in ("THINKING", "SPEAKING") else (0.14 if state == "LISTENING" else 0.05)
        self._orb_phase = (getattr(self, "_orb_phase", 0.0) + speed) % (2.0 * math.pi)
        p = self._orb_phase

        core_r = 12.5 + math.sin(p * 0.9) * 0.4 + lvl * 1.4
        c.create_oval(
            cx - core_r, cy - core_r, cx + core_r, cy + core_r,
            fill="#070b12", outline=""
        )

        ring_r = 15.8 + math.sin(p) * 0.8 + lvl * 2.5
        arcs = [
            (15.0, 150.0, "#38bdf8", 3.2),
            (150.0, 125.0, "#a78bfa", 3.2),
            (260.0, 115.0, "#60a5fa", 2.8),
        ]
        rot = math.degrees(p * (1.8 if state in ("THINKING", "SPEAKING") else 0.6))
        for start_deg, extent_deg, col, w in arcs:
            c.create_arc(
                cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                start=(start_deg + rot) % 360.0,
                extent=extent_deg,
                style=tk.ARC,
                outline=col,
                width=w,
            )

        if state in ("LISTENING", "THINKING", "SPEAKING") or lvl > 0.08:
            glow_r = ring_r + 3.0 + lvl * 2.2
            glow_col = "#34d399" if state == "LISTENING" else "#38bdf8"
            c.create_oval(
                cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r,
                outline=glow_col, width=1.0
            )

        self.root.after(33, self._tick_siri_orb)

    def _toggle_drawer(self, force_open: bool | None = None):
        target_open = (not getattr(self, "drawer_open", False)) if force_open is None else bool(force_open)
        self.drawer_open = target_open
        sw = self.root.winfo_screenwidth()

        if self.drawer_open:
            self.chevron_btn.config(text="⌃", fg=self.accent_color)
            self.drop_w = self.full_w
            self.drop_h = self.full_h
            self.drop_x = max(0, (sw - self.drop_w) // 2)
            self.root.geometry(f"{self.drop_w}x{self.drop_h}+{self.drop_x}+{self.drop_y}")
            self._redraw_pill_shell()
            self.input_box.focus_set()
        else:
            self.chevron_btn.config(text="⌄", fg=self.text_muted)
            self.drop_w = self.pill_w
            self.drop_h = self.pill_h
            self.drop_x = max(0, (sw - self.drop_w) // 2)
            self.root.geometry(f"{self.drop_w}x{self.drop_h}+{self.drop_x}+{self.drop_y}")
            self._redraw_pill_shell()

    def _auto_resize_input(self, _event=None):
        if not hasattr(self, "input_box"):
            return
        lines_cnt = int(self.input_box.index("end-1c").split(".")[0])
        self.input_box.config(height=max(1, min(4, lines_cnt)))

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
                        self._toggle_drawer(force_open=True)
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
            if hasattr(self, "_cancel_auto_hide"):
                self._cancel_auto_hide()
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
        if hasattr(self, "siri_subtitle"):
            self.siri_subtitle.config(text="Idle")
            self.siri_substatus.config(text="Alt+Space • Ask Ciel or press V", fg=self.text_muted)
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
        self._orb_state = "THINKING"
        if hasattr(self, "siri_subtitle"):
            short_q = user_text.splitlines()[0][:44]
            self.siri_subtitle.config(text=short_q)
            self.siri_substatus.config(text="Thinking...", fg=self.warn_color)
        if getattr(self, "siri_mode", True) and hasattr(self, "_step_slide_height"):
            self.drop_h = self.siri_reply_h
            self._step_slide_height(self.root.winfo_height(), self.drop_h)
            self._apply_siri_compact_view()
        self.send_btn.config(state=tk.DISABLED, text="·")
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
        self._orb_state = "IDLE"
        self._stop_thinking_anim()
        self._speak_latest_turn()
        self._format_turn_code_blocks()
        if getattr(self, "siri_mode", True):
            self._apply_siri_compact_view()
        if hasattr(self, "siri_substatus"):
            self.siri_substatus.config(text="Tap orb or type below · Esc to dismiss", fg=self.text_muted)
        self.send_btn.config(state=tk.NORMAL, text="↑")
        self.input_box.focus_set()


    def _on_pointer_enter(self, _event=None):
        self._pointer_inside = True
        if hasattr(self, "_cancel_auto_hide"):
            self._cancel_auto_hide()

    def _on_pointer_leave(self, _event=None):
        self._pointer_inside = False
        if (
            getattr(self, "is_visible", False)
            and not getattr(self, "pinned", False)
            and not getattr(self, "is_processing", False)
            and getattr(self, "auto_hide_after_speak", False)
            and hasattr(self, "_schedule_auto_hide")
        ):
            self._schedule_auto_hide(delay_ms=2600)

    def _cancel_auto_hide(self):
        job_id = getattr(self, "_auto_hide_id", None)
        if job_id:
            try:
                self.root.after_cancel(job_id)
            except Exception:
                pass
            self._auto_hide_id = None

    def _schedule_auto_hide(self, delay_ms: int = 4500):
        self._cancel_auto_hide()
        if not getattr(self, "auto_hide_after_speak", False) or getattr(self, "pinned", False):
            return

        def _maybe_hide():
            if (
                getattr(self, "is_visible", False)
                and not getattr(self, "pinned", False)
                and not getattr(self, "is_processing", False)
                and not getattr(self, "_pointer_inside", False)
                and not getattr(getattr(self, "live_voice", None), "speaking", False)
            ):
                self.hide_dropdown()

        self._auto_hide_id = self.root.after(delay_ms, _maybe_hide)

    def _on_focus_out(self, event=None):
        if not self.is_visible or self.pinned or self._animating:
            return
        def _check():
            if self.is_visible and not self.pinned and self.root.focus_displayof() is None:
                self.hide_dropdown()
        self.root.after(120, _check)

    def show_dropdown(self, selection: str = ""):
        self.is_visible = True
        self.root.deiconify()
        self.root.geometry(f"{self.drop_w}x{self.drop_h}+{self.drop_x}+0")
        self.root.wm_attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()
        self.input_box.focus_set()
        if selection:
            wrapped = f"Inspect/help with this selection from my screen:\n\n```\n{selection}\n```"
            self.input_box.delete("1.0", "end")
            self.input_box.insert("1.0", wrapped)

    def hide_dropdown(self):
        self.is_visible = False
        self.root.withdraw()

    def toggle_dropdown(self, selection: str = ""):
        if getattr(self, "is_visible", False) and not selection:
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
        btn_frames = ("·", "··", "···")
        idx = getattr(self, "_anim_idx", 0)
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

    def _on_voice_level(self, rms: float):
        self._orb_level = rms

    def _on_live_voice_state(self, state: str):
        def _ui():
            self._orb_state = state
            if not hasattr(self, "siri_substatus"):
                return
            if state == "LISTENING":
                self.siri_subtitle.config(text="Listening...")
                self.siri_substatus.config(text="Speak naturally to CIEL", fg=self.ok_color)
            elif state == "SPEAKING":
                self.siri_substatus.config(text="Speaking...", fg=self.accent_color)
            elif state in ("TRANSCRIBING", "LOADING_STT"):
                self.siri_substatus.config(text="Transcribing...", fg=self.warn_color)
            elif state.startswith("MIC_ERR"):
                self.siri_substatus.config(text=state, fg="#f87171")
                self.append_system_msg(state)
            else:
                self.siri_substatus.config(text="Tap orb or type below · Esc to dismiss", fg=self.text_muted)
        self.root.after(0, _ui)

    def _on_live_transcript(self, text: str):
        def _apply():
            if text.startswith("__LIVE_USER__:"):
                utext = text.split(":", 1)[1].strip()
                if utext:
                    self.append_text("YOU (Voice):\n", "user_header")
                    self.append_text(utext + "\n\n", "user_body")
                    self.messages.append({"role": "user", "content": utext})
                    if hasattr(self, "siri_substatus"):
                        self.siri_substatus.config(text=utext[:56], fg=self.ok_color)
                return
            if text.startswith("__LIVE_CIEL__:"):
                ctext = text.split(":", 1)[1].strip()
                if ctext:
                    self.append_text("CIEL (Live):\n", "ciel_header")
                    self.append_text(ctext + "\n\n", "ciel_body")
                    self.messages.append({"role": "assistant", "content": ctext})
                return
            if getattr(self, "is_processing", False):
                return
            self.input_box.delete("1.0", "end")
            self.input_box.insert("1.0", text)
            self.send_message()
        self.root.after(0, _apply)

    def _toggle_live_voice(self):
        engine = getattr(self, "live_voice", None)
        if engine is None:
            engine = LiveVoiceEngine(
                on_transcript=self._on_live_transcript,
                on_state=self._on_live_voice_state,
                is_busy=lambda: getattr(self, "is_processing", False),
                on_level=self._on_voice_level,
            )
            self.live_voice = engine

        if engine.running:
            engine.stop()
            self.live_pill._base_bg = self.pill_dark
            self.live_pill.config(bg=self.pill_dark, fg=self.ok_color, text="🎙")
            self.append_system_msg("Live Voice stopped.")
        else:
            if not getattr(self, "is_visible", False):
                self.show_dropdown()
            engine.start()
            self.live_pill._base_bg = self.accent_color
            self.live_pill.config(bg=self.accent_color, fg=self.accent_fg, text="🎙")
            self.append_system_msg("Live Voice active — speak naturally (say 'stop listening' to exit).")

    def _speak_latest_turn(self):
        engine = getattr(self, "live_voice", None)
        if not engine or getattr(engine, "speaking", False):
            return
        try:
            if "resp_start" in self.chat_area.mark_names():
                reply_text = self.chat_area.get("resp_start", "end").strip()
            else:
                full = self.chat_area.get("1.0", "end").strip()
                parts = re.split(r"(?:^|\n)(?:◈\s*)?CIEL\b[^\n]*\n?", full)
                reply_text = parts[-1].strip() if len(parts) > 1 else ""
            if reply_text:
                engine.speak(reply_text)
        except Exception:
            pass



if __name__ == "__main__":
    root = tk.Tk(className="ciel-dropdown")
    app = CielDesktopGUI(root)
    root.mainloop()
