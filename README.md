# CIEL — Fast Multi-Model CLI & Desktop AI Agent

**CIEL** is a lightweight, zero-bloat Python AI coding and system assistant featuring both a **terminal CLI (`ciel.py`)** and a **dark-mode Desktop GUI (`desktop.py`)**.

## Features
- **Smart Multi-Model Routing (`auto`):** Automatically routes casual chat to ultra-fast inference (`groq` / `glm`) with live token streaming, and routes coding/system tasks to strong tool-calling models (`flash` / `pro`).
- **Built-in System & Code Tools:** Read, edit, write, and grep files, run terminal commands, search the web, capture screen context, and persist user memory.
- **Desktop GUI (`desktop.py`) & CLI (`ciel.py`):** Use CIEL from your terminal or launch the standalone dark-mode `tkinter` desktop window.
- **Extensible Local Models (`local_models.json`):** Drop custom Ollama or self-hosted endpoints into a git-ignored `local_models.json` file without modifying source code.

## Quick Start

```bash
git clone https://github.com
cd ciel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the project root with your API key(s):
```env
GEMINI_API_KEY="your_gemini_key"
OPEN_ROUTER_API_KEY="your_openrouter_key"
GROQ_API_KEY="your_groq_key"
```

### Run the Terminal CLI
```bash
python ciel.py
```

### Run the Desktop GUI
```bash
python desktop.py
```
