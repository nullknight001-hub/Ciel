# CIEL — Cognitive Interface & Execution Layer

**CIEL** is a lightweight, multi-model CLI and dark-mode Desktop AI coding & Linux system agent engineered for hybrid inference and backed by a custom **81.9B parameter QLoRA fine-tuned model (`Qwen/Qwen3-Coder-Next`)**.

## System Architecture & Highlights
- **Unified Engine (`ciel.py` & `desktop.py`):** Shared `run_turn()` loop supporting live streaming, `503` backoff, and local/cloud fallback.
- **Concurrency & Safety:** POSIX `fcntl.flock` memory persistence alongside destructive shell command guardrails.
- **Custom 81.9B MoE Fine-Tuning:** Trained a 4-bit NF4 QLoRA adapter (`r=16`, 2.22B trainable parameters across 512 MoE experts) on an 80GB NVIDIA H100 using a 7,500-example tool-calling dataset (`training/`).

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
cp .env.example .env
python ciel.py # or python desktop.py
```

## License
MIT License — see [LICENSE](LICENSE) for details.
