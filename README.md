# CIEL — Cognitive Interface & Execution Layer

**CIEL** is a resident Wayland/KDE Plasma top-edge dropdown HUD, multimodal voice assistant, and Linux system/coding agent engineered for low-latency hybrid inference and backed by a custom **81.9B parameter QLoRA fine-tuned model (`Qwen/Qwen3-Coder-Next`)**.

## System Architecture & Highlights
- **Bidirectional Gemini Live Voice (`live_voice.py`):** Real-time WebSocket audio streaming (`16 kHz` PCM capture -> Gemini Multimodal Live -> `24 kHz` PCM playback) with live input/output transcriptions, RMS plasma-orb animation, echo-gated barge-in, and multi-turn context window compression.
- **Voice-Triggered Desktop & System Tools:** Native function-calling bridge inside the Gemini Live stream (`open_url`, `search_youtube`, `open_app`, and `engine.tool_map` system/file/media tools) with call-ID deduplication for instant OS control.
- **Resident Dropdown HUD & Unified Engine (`desktop.py` & `engine.py`):** Spring-animated top-edge capsule/drawer interface (`Meta+Alt+C` toggle) sharing `run_turn()` across cloud APIs and local models, with POSIX `fcntl.flock` memory persistence and destructive-command guardrails.
- **Custom 81.9B MoE Fine-Tuning (`training/`):** Trained a 4-bit NF4 QLoRA adapter (`r=16`, 2.22B trainable parameters across 512 MoE experts) on an 80GB NVIDIA H100 using a 7,500-example tool-calling dataset.

## `CIEL-80B` QLoRA Benchmark Results
Summary of evaluation metrics from `training/eval_report_tools.json`:

| Metric | Base `Qwen3-Coder-Next` (80B) | Fine-Tuned `CIEL-80B` (QLoRA) | Delta |
| :--- | :---: | :---: | :---: |
| **Tool Selection Accuracy** | 100.0% (5/5) | **100.0% (5/5)** | Zero catastrophic forgetting |
| **Workspace Path Grounding** | Relative path (`ciel.py`) | **Exact absolute path resolution** | Grounded to local project tree |
| **Multi-Constraint Shell Synthesis** | Partial (`nvidia-smi` only) | **Complete (`nvidia-smi && free -h`)** | Satisfied compound constraints |
| **Total Evaluation Latency (5 Tasks)** | 69.73s (13.95s/task) | **31.89s (6.38s/task)** | **2.19x faster (-54.3%)** |

## Quick Start
```bash
git clone https://github.com/nullknight001-hub/Ciel.git
cd Ciel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Add GEMINI_API_KEY (and optional Groq/OpenRouter keys)
python desktop.py      # Launch the top-edge dropdown HUD (or python ciel.py for CLI)
```

### Resident Systemd User Service (Arch Linux / KDE Plasma Wayland)
```bash
systemctl --user enable --now ciel-hud.service
```
Click the `🎙` pill or glowing orb in the capsule (or press `Meta+Alt+C`) to start a live voice session, launch apps/URLs by voice, or say `"stop listening"` to exit voice mode.

## License
MIT License — see [LICENSE](LICENSE) for details.
