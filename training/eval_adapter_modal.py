import json
import os
import time
import modal

os.environ["HF_HOME"] = "/vol/hf_cache"
APP_NAME = "ciel-80b-eval"
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

EVAL_SUITE = [
    {"prompt": "Read ciel.py lines 1 to 80 and find any syntax bugs.", "expected_tool": "read_file"},
    {"prompt": "Search the codebase for OPEN_ROUTER_API_KEY.", "expected_tool": "grep_code"},
    {"prompt": "Check GPU VRAM and system memory using terminal.", "expected_tool": "run_terminal"},
    {"prompt": "Save a permanent memory that I use Arch Linux.", "expected_tool": "save_memory"},
    {"prompt": "Look at my screen and tell me what error is visible.", "expected_tool": "see_screen"},
]


def run_suite(model, tokenizer):
    import torch
    model.eval()
    cases = []
    for p in EVAL_SUITE:
        msgs = [
            {"role": "system", "content": "You are CIEL, a surgical coding and Linux system agent. Call tools only when external files, system commands, web info, or memory are needed."},
            {"role": "user", "content": p["prompt"]},
        ]
        text = tokenizer.apply_chat_template(msgs, tools=CIEL_TOOLS, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to("cuda")
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=160, do_sample=False, output_router_logits=False)
        dt = time.perf_counter() - t0
        gen = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        matched = (p["expected_tool"] in gen) and ("<tool_call>" in gen or "{" in gen)
        cases.append({"prompt": p["prompt"], "expected": p["expected_tool"], "matched": matched, "latency_s": round(dt, 2), "output": gen.strip()})
    acc = round(100.0 * sum(1 for c in cases if c["matched"]) / len(cases), 1)
    return {"accuracy": acc, "cases": cases}


@app.function(image=image, gpu="H100", timeout=1800, volumes={"/vol": vol})
def benchmark():
    from unsloth import FastLanguageModel

    from peft import PeftModel
    print("1) Evaluating BASE Qwen/Qwen3-Coder-Next (with CIEL tools)...")
    model, tok = FastLanguageModel.from_pretrained(model_name=BASE_MODEL, max_seq_length=2048, load_in_4bit=True)
    FastLanguageModel.for_inference(model)
    base_res = run_suite(model, tok)
    print("BASE (WITH TOOLS):", json.dumps(base_res, indent=2))

    print("\n2) Attaching FINE-TUNED CIEL-80B LoRA Adapter in-place (with CIEL tools)...")
    ft_model = PeftModel.from_pretrained(model, "/vol/ciel_80b_adapter")
    FastLanguageModel.for_inference(ft_model)
    ft_res = run_suite(ft_model, tok)
    print("FINE-TUNED (WITH TOOLS):", json.dumps(ft_res, indent=2))

    report = {"base_with_tools": base_res, "finetuned_with_tools": ft_res}
    with open("/vol/eval_report_tools.json", "w") as f:
        json.dump(report, f, indent=2)
    vol.commit()


@app.local_entrypoint()
def main():
    benchmark.remote()
