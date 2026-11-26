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
        self.root.destroy()
    def __init__(self, root):
        self.root = root
        self.root.title("CIEL - Cognitive Interface & Execution Layer")
        self.root.geometry("900x700")
        self.root.minsize(700, 500)
        

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
        _boot_prompt = os.environ.pop("CIEL_INITIAL_PROMPT", "").strip()
        if _boot_prompt and hasattr(self, "input_box"):
            self.input_box.insert("1.0", _boot_prompt)
            self.root.after(150, self.send_message)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.append_system_msg(
            f"CIEL Online [{self.current_alias} -> {self.active_model}] ({len(load_memories())} memories loaded)."
        )

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
        main_frame = tk.Frame(self.root, bg=self.bg_dark)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        top_frame = tk.Frame(main_frame, bg=self.panel_dark, bd=1, relief=tk.SOLID)
        top_frame.pack(fill=tk.X, pady=(0, 8), ipady=6, ipadx=6)

        tk.Label(
            top_frame,
            text="CIEL",
            font=("Segoe UI", 12, "bold"),
            fg=self.text_light,
            bg=self.panel_dark,
        ).pack(side=tk.LEFT, padx=8)

        controls_frame = tk.Frame(top_frame, bg=self.panel_dark)
        controls_frame.pack(side=tk.RIGHT, padx=8)

        tk.Button(
            controls_frame,
            text="Clear",
            font=("Segoe UI", 9),
            bg="#2d2d2d",
            fg=self.text_light,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.clear_chat,
        ).pack(side=tk.RIGHT, padx=(10, 0))

        tk.Label(
            controls_frame,
            text="Effort:",
            font=("Segoe UI", 10),
            fg=self.text_muted,
            bg=self.panel_dark,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.effort_var = tk.StringVar(value=self.current_effort)
        self.effort_dropdown = ttk.Combobox(
            controls_frame,
            textvariable=self.effort_var,
            values=["low", "medium", "high"],
            state="readonly",
            width=8,
        )
        self.effort_dropdown.pack(side=tk.LEFT, padx=(0, 10))
        self.effort_dropdown.bind("<<ComboboxSelected>>", self.on_effort_changed)

        tk.Label(
            controls_frame,
            text="Model:",
            font=("Segoe UI", 10),
            fg=self.text_muted,
            bg=self.panel_dark,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.model_var = tk.StringVar(value=self.current_alias)
        model_choices = ["auto"] + list(MODELS.keys())
        self.model_dropdown = ttk.Combobox(
            controls_frame,
            textvariable=self.model_var,
            values=model_choices,
            state="readonly",
            width=12,
        )
        self.model_dropdown.pack(side=tk.LEFT)
        self.model_dropdown.bind("<<ComboboxSelected>>", self.on_model_changed)

        chat_container = tk.Frame(main_frame, bg=self.border_dark)
        chat_container.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self.chat_area = scrolledtext.ScrolledText(
            chat_container,
            wrap=tk.WORD,
            bg=self.bg_dark,
            fg=self.text_light,
            font=("Segoe UI", 11),
            insertbackground="white",
            bd=0,
            highlightthickness=0,
        )
        self.chat_area.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.chat_area.config(state=tk.DISABLED)

        self.chat_area.tag_config("user_header", font=("Segoe UI", 10, "bold"), foreground="#4ec9b0", spacing1=8)
        self.chat_area.tag_config("user_body", font=("Segoe UI", 11), foreground=self.text_light, lmargin1=15, lmargin2=15, spacing3=6)
        self.chat_area.tag_config("ciel_header", font=("Segoe UI", 10, "bold"), foreground="#9cdcfe", spacing1=8)
        self.chat_area.tag_config("ciel_body", font=("Segoe UI", 11), foreground=self.text_light, lmargin1=15, lmargin2=15, spacing3=6)
        self.chat_area.tag_config("system_msg", font=("Segoe UI", 10, "italic"), foreground="#b5cea8", spacing1=4, spacing3=4, lmargin1=10, lmargin2=10)
        self.chat_area.tag_config("tool_msg", font=("Consolas", 9), foreground="#ce9178", spacing1=2, spacing3=2, lmargin1=15, lmargin2=15)

        input_frame = tk.Frame(main_frame, bg=self.panel_dark, bd=1, relief=tk.SOLID)
        input_frame.pack(fill=tk.X, ipady=4, ipadx=4)

        self.input_box = tk.Text(
            input_frame,
            height=3,
            wrap=tk.WORD,
            bg="#181818",
            fg=self.text_light,
            font=("Segoe UI", 11),
            insertbackground="white",
            bd=0,
            highlightthickness=0,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.input_box.bind("<Return>", self.handle_return_key)

        btn_frame = tk.Frame(input_frame, bg=self.panel_dark)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=8, pady=6)

        self.send_btn = tk.Button(
            btn_frame,
            text="Send",
            font=("Segoe UI", 10, "bold"),
            bg=self.accent_color,
            fg="white",
            activebackground="#1c90ff",
            activeforeground="white",
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self.send_message,
        )
        self.send_btn.pack(side=tk.BOTTOM, fill=tk.X)
        self.input_box.focus_set()

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
        self.send_btn.config(state=tk.NORMAL, text="Send")
        self.input_box.focus_set()


if __name__ == "__main__":
    root = tk.Tk()
    app = CielDesktopGUI(root)
    root.mainloop()