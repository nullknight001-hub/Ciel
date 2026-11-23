import json
import os
import time
import modal

os.environ["HF_HOME"] = "/vol/hf_cache"
APP_NAME = "ciel-80b-hard-eval"
VOL_NAME = "ciel-80b-vol"
BASE_MODEL = "Qwen/Qwen3-Coder-Next"

app = modal.App(APP_NAME)
vol = modal.Volume.from_name(VOL_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "unsloth",
        "torch>=2.4.0",
        "transformers>=4.48.0",
        "accelerate>=1.1.0",
        "peft>=0.13.0",
        "bitsandbytes>=0.44.1",
    )
)

CIEL_TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Reads lines from a file.", "parameters": {"type": "object", "properties": {"filepath": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["filepath"]}}},
    {"type": "function", "function": {"name": "grep_code", "description": "Searches regex pattern across codebase files.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "run_terminal", "description": "Executes a bash command on Linux.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "save_memory", "description": "Saves a permanent memory fact.", "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}}},
    {"type": "function", "function": {"name": "see_screen", "description": "Captures and analyzes the user's desktop screen.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}},
]

SYS_PROMPT = "You are CIEL, a surgical coding and Linux system agent. Call tools ONLY when external files, system commands, web info, or memory are needed. For pure conceptual questions, answer directly without calling any tool."

HARD_SUITE = [
    # Category A: Ambiguous Workspace & Path Grounding (5 tasks)
    {"cat": "ambiguous", "messages": [{"role": "user", "content": "Where in my project do we cap per-turn token usage?"}], "expect_type": "tool", "expect_tool": "grep_code", "expect_arg": "MAX_TURN_TOKENS"},
    {"cat": "ambiguous", "messages": [{"role": "user", "content": "Inspect the top of my dark-mode GUI script to see how it imports the core engine."}], "expect_type": "tool", "expect_tool": "read_file", "expect_arg": "/home/ilham/Projects/Ciel_V3/desktop.py"},
    {"cat": "ambiguous", "messages": [{"role": "user", "content": "Verify whether my core agent script compiles without syntax errors right now."}], "expect_type": "tool", "expect_tool": "run_terminal", "expect_arg": "py_compile"},
    {"cat": "ambiguous", "messages": [{"role": "user", "content": "Store a note so you never forget I'm applying to KAIST with this project."}], "expect_type": "tool", "expect_tool": "save_memory", "expect_arg": "KAIST"},
    {"cat": "ambiguous", "messages": [{"role": "user", "content": "Check what error popup is currently visible on my desktop."}], "expect_type": "tool", "expect_tool": "see_screen", "expect_arg": ""},

    # Category B: Multi-Turn Error Recovery (5 tasks)
    {
        "cat": "recovery",
        "messages": [
            {"role": "user", "content": "Compile ciel.py and fix any indentation error."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "run_terminal", "arguments": {"command": "python -m py_compile /home/ilham/Projects/Ciel_V3/ciel.py"}}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "File \"/home/ilham/Projects/Ciel_V3/ciel.py\", line 142\n    IndentationError: unexpected indent"},
        ],
        "expect_type": "tool",
        "expect_tool": "read_file",
        "expect_arg": "ciel.py",
    },
    {
        "cat": "recovery",
        "messages": [
            {"role": "user", "content": "Find where `choose_routed_alias` is called."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "grep_code", "arguments": {"pattern": "choose_routed_alias", "directory": "/home/ilham/Projects/Ciel_V3/src"}}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "Error: Directory /home/ilham/Projects/Ciel_V3/src does not exist."},
        ],
        "expect_type": "tool",
        "expect_tool": "grep_code",
        "expect_arg": "/home/ilham/Projects/Ciel_V3",
    },
    {
        "cat": "recovery",
        "messages": [
            {"role": "user", "content": "Check if my RTX 3050 VRAM is full."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "run_terminal", "arguments": {"command": "nvida-smi"}}}]},
            {"role": "tool", "tool_call_id": "c3", "content": "bash: nvida-smi: command not found"},
        ],
        "expect_type": "tool",
        "expect_tool": "run_terminal",
        "expect_arg": "nvidia-smi",
    },
    {
        "cat": "recovery",
        "messages": [
            {"role": "user", "content": "Read lines 1-40 of live_voice.py."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "read_file", "arguments": {"filepath": "live_voice.py", "start_line": 1, "end_line": 40}}}]},
            {"role": "tool", "tool_call_id": "c4", "content": "FileNotFoundError: [Errno 2] No such file or directory: 'live_voice.py'"},
        ],
        "expect_type": "tool",
        "expect_tool": "read_file",
        "expect_arg": "/home/ilham/Projects/Ciel_V3/live_voice.py",
    },
    {
        "cat": "recovery",
        "messages": [
            {"role": "user", "content": "Search for `fcntl` usage in the codebase."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c5", "type": "function", "function": {"name": "grep_code", "arguments": {"pattern": "fcntl_lock"}}}]},
            {"role": "tool", "tool_call_id": "c5", "content": "No matches found for 'fcntl_lock'."},
        ],
        "expect_type": "tool",
        "expect_tool": "grep_code",
        "expect_arg": "fcntl",
    },

    # Category C: Negative "No-Tool" Discipline (5 tasks)
    {"cat": "no_tool", "messages": [{"role": "user", "content": "Explain why 4-bit NF4 QLoRA reduces 80B VRAM from ~160GB down to ~50GB."}], "expect_type": "no_tool"},
    {"cat": "no_tool", "messages": [{"role": "user", "content": "What is the difference between fcntl.flock and os.replace when saving JSON?"}], "expect_type": "no_tool"},
    {"cat": "no_tool", "messages": [{"role": "user", "content": "How does Flash Attention cut HBM memory accesses from O(N^2) to O(N)?"}], "expect_type": "no_tool"},
    {"cat": "no_tool", "messages": [{"role": "user", "content": "When should I use threading instead of asyncio in a Python GUI?"}], "expect_type": "no_tool"},
    {"cat": "no_tool", "messages": [{"role": "user", "content": "Give me a quick 3-bullet summary of how LoRA low-rank matrices work."}], "expect_type": "no_tool"},
]


def run_hard_suite(model, tokenizer):
    import torch
    model.eval()
    results = []
    for item in HARD_SUITE:
        msgs = [{"role": "system", "content": SYS_PROMPT}] + item["messages"]
        text = tokenizer.apply_chat_template(msgs, tools=CIEL_TOOLS, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to("cuda")
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=160, do_sample=False, output_router_logits=False)
        dt = time.perf_counter() - t0
        gen = tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        called_tool = "<tool_call>" in gen
        if item["expect_type"] == "no_tool":
            passed = (not called_tool) and (len(gen) > 40)
        else:
            arg_ok = (item["expect_arg"] in gen) if item["expect_arg"] else True
            passed = called_tool and (item["expect_tool"] in gen) and arg_ok
        results.append({"cat": item["cat"], "passed": passed, "latency_s": round(dt, 2), "called_tool": called_tool, "sample": gen[:180]})

    by_cat = {}
    for c in ("ambiguous", "recovery", "no_tool"):
        sub = [r for r in results if r["cat"] == c]
        by_cat[c] = round(100.0 * sum(1 for r in sub if r["passed"]) / len(sub), 1)
    overall = round(100.0 * sum(1 for r in results if r["passed"]) / len(results), 1)
    total_s = round(sum(r["latency_s"] for r in results), 2)
    return {"overall_accuracy": overall, "by_category": by_cat, "total_latency_s": total_s, "cases": results}


@app.function(image=image, gpu="H100", timeout=1800, volumes={"/vol": vol})
def run_benchmark():
    from unsloth import FastLanguageModel
    from peft import PeftModel

    model, tok = FastLanguageModel.from_pretrained(model_name=BASE_MODEL, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    base_res = run_hard_suite(model, tok)
    print("BASE HARD:", json.dumps({k: v for k, v in base_res.items() if k != "cases"}, indent=2))

    ft_model = PeftModel.from_pretrained(model, "/vol/ciel_80b_adapter_v2")
    FastLanguageModel.for_inference(ft_model)
    ft_res = run_hard_suite(ft_model, tok)
    print("FINETUNED HARD:", json.dumps({k: v for k, v in ft_res.items() if k != "cases"}, indent=2))

    with open("/vol/eval_hard_report.json", "w") as f:
        json.dump({"base": base_res, "finetuned": ft_res}, f, indent=2)
    vol.commit()


@app.local_entrypoint()
def main():
    run_benchmark.remote()
