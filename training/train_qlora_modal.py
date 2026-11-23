import json
import os
import time
import modal
os.environ["HF_HOME"] = "/vol/hf_cache"
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
os.environ["UNSLOTH_COMPILE_DISABLE"] = "1"

APP_NAME = "ciel-80b-qlora"
VOL_NAME = "ciel-80b-vol"
BASE_MODEL = "Qwen/Qwen3-Coder-Next"

app = modal.App(APP_NAME)
vol = modal.Volume.from_name(VOL_NAME, create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "unsloth",
        "torch>=2.4.0",
        "transformers>=4.48.0",
        "datasets>=3.0.0",
        "accelerate>=1.1.0",
        "peft>=0.13.0",
        "bitsandbytes>=0.44.1",
        "trl>=0.12.0",
    )
)


def evaluate_prompts(model, tokenizer, prompts):
    import torch

    results = []
    model.eval()
    for p in prompts:
        msgs = [
            {"role": "system", "content": "You are CIEL, a surgical coding and Linux system agent. Call tools only when external files, system commands, web info, or memory are needed."},
            {"role": "user", "content": p["prompt"]},
        ]
        text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to("cuda")
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=160, do_sample=False, output_router_logits=False)
        dt = time.perf_counter() - t0
        gen = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        matched = p["expected_tool"] in gen
        results.append({"prompt": p["prompt"], "expected": p["expected_tool"], "matched": matched, "latency_s": round(dt, 2), "sample": gen[:220]})
    acc = sum(1 for r in results if r["matched"]) / max(len(results), 1)
    return {"accuracy": round(acc * 100, 1), "cases": results}


@app.function(
    image=image,
    gpu="H100",
    timeout=18000,
    volumes={"/vol": vol},
)
def train_qlora(max_steps: int = 196):
    os.environ["HF_HOME"] = "/vol/hf_cache"
    from unsloth import FastLanguageModel
    from datasets import load_dataset
    from trl import SFTTrainer, SFTConfig

    data_path = "/vol/ciel_gold.jsonl" if os.path.exists("/vol/ciel_gold.jsonl") else "/vol/ciel_train_60k.jsonl"
    out_dir = "/vol/ciel_80b_adapter"
    assert os.path.exists(data_path), f"Dataset not found at {data_path}"

    eval_suite = [
        {"prompt": "Read ciel.py lines 1 to 80 and find any syntax bugs.", "expected_tool": "read_file"},
        {"prompt": "Search the codebase for OPEN_ROUTER_API_KEY.", "expected_tool": "grep_code"},
        {"prompt": "Check GPU VRAM and system memory using terminal.", "expected_tool": "run_terminal"},
        {"prompt": "Save a permanent memory that I use Arch Linux.", "expected_tool": "save_memory"},
        {"prompt": "Look at my screen and tell me what error is visible.", "expected_tool": "see_screen"},
    ]

    print(f"Loading {BASE_MODEL} via Unsloth FastLanguageModel...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=2048,
        load_in_4bit=True,
    )

    FastLanguageModel.for_inference(model)
    print("Running baseline (before fine-tuning) evaluation...")
    before_metrics = evaluate_prompts(model, tokenizer, eval_suite)
    print("BEFORE:", json.dumps(before_metrics, indent=2))

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=32,
        lora_dropout=0,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    raw_ds = load_dataset("json", data_files=data_path, split="train")

    def format_chat(ex):
        msgs = []
        for m in ex["messages"]:
            m = dict(m)
            if m.get("tool_calls"):
                fixed_calls = []
                for tc in m["tool_calls"]:
                    tc = dict(tc)
                    fn = dict(tc.get("function", {}))
                    if isinstance(fn.get("arguments"), str):
                        try:
                            fn["arguments"] = json.loads(fn["arguments"])
                        except Exception:
                            fn["arguments"] = {}
                    tc["function"] = fn
                    fixed_calls.append(tc)
                m["tool_calls"] = fixed_calls
            msgs.append(m)
        txt = tokenizer.apply_chat_template(msgs, tools=ex.get("tools") or None, tokenize=False, add_generation_prompt=False)
        return {"text": txt}

    ds = raw_ds.map(format_chat, remove_columns=raw_ds.column_names)

    sft_cfg = SFTConfig(
        output_dir=out_dir,
        max_steps=max_steps,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        bf16=True,
        logging_steps=25,
        save_steps=150,
        save_total_limit=2,
        optim="adamw_8bit",
        max_seq_length=2048,
        packing=True,
        dataset_text_field="text",
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        args=sft_cfg,
    )
    trainer.train()

    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    vol.commit()

    FastLanguageModel.for_inference(model)
    print("Running post-training (after QLoRA) evaluation...")
    after_metrics = evaluate_prompts(model, tokenizer, eval_suite)
    print("AFTER:", json.dumps(after_metrics, indent=2))

    report = {"base_model": BASE_MODEL, "max_steps": max_steps, "before": before_metrics, "after": after_metrics}
    with open("/vol/eval_report.json", "w") as f:
        json.dump(report, f, indent=2)

    vol.commit()
    print("Saved spotless adapter + eval_report.json to Modal Volume!")


@app.local_entrypoint()
def main(max_steps: int = 196):
    train_qlora.remote(max_steps=max_steps)
