"""QLoRA fine-tune of the RU/EN judge (Qwen3-8B 4-bit) on eval/sft/*.jsonl. Experiment only (branch sft).

Run in the separate training venv, not the app venv:
  C:/sft/venv/Scripts/python eval/train_sft.py --base C:/sft/qwen3-8b-bnb4 --out C:/sft/lora

Prompts are rendered exactly like Ollama's qwen3 template with think=false, so the adapter sees at
training time what it gets in the app. Loss is on the assistant JSON only. Eval loss on the val slice
is checked every few steps; training stops early if it stops improving and the best adapter is kept.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "0")
from unsloth import FastLanguageModel  # noqa: E402  (must be imported before transformers)
import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from transformers import (DataCollatorForSeq2Seq, EarlyStoppingCallback, Trainer,  # noqa: E402
                          TrainingArguments)

ROOT = Path(__file__).resolve().parent.parent


def render(msgs):
    system, user, answer = (m["content"] for m in msgs)
    prompt = (f"<|im_start|>system\n\n{system}<|im_end|>\n<|im_start|>user\n{user} /no_think<|im_end|>\n"
              "<|im_start|>assistant\n<think>\n\n</think>\n\n")
    return prompt, answer + "<|im_end|>"


def load(path, tok, max_len):
    rows, skipped = [], []
    for line in open(path, encoding="utf-8"):
        rec = json.loads(line)
        p, c = render(rec["messages"])
        ip = tok(p, add_special_tokens=False)["input_ids"]
        ic = tok(c, add_special_tokens=False)["input_ids"]
        if len(ip) + len(ic) > max_len:
            skipped.append((rec["id"], len(ip) + len(ic)))
            continue
        rows.append({"input_ids": ip + ic, "attention_mask": [1] * (len(ip) + len(ic)),
                     "labels": [-100] * len(ip) + ic})
    return rows, skipped


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="C:/sft/qwen3-8b-bnb4")
    ap.add_argument("--out", default="C:/sft/lora")
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--accum", type=int, default=8)
    ap.add_argument("--eval-steps", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true", help="load + tokenize + 2 steps, then exit")
    args = ap.parse_args()

    t0 = time.time()
    model, tok = FastLanguageModel.from_pretrained(args.base, max_seq_length=args.max_len, load_in_4bit=True,
                                                   dtype=None)
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank, lora_alpha=args.rank, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=7)

    train, sk_t = load(ROOT / "eval" / "sft" / "train.jsonl", tok, args.max_len)
    val, sk_v = load(ROOT / "eval" / "sft" / "val.jsonl", tok, args.max_len)
    lens = sorted(len(r["input_ids"]) for r in train)
    print(f"train {len(train)} val {len(val)}; tokens p50 {lens[len(lens)//2]} max {lens[-1]}; skipped {sk_t + sk_v}")

    targs = TrainingArguments(
        output_dir=str(Path(args.out).parent / "checkpoints"), per_device_train_batch_size=1,
        per_device_eval_batch_size=1, gradient_accumulation_steps=args.accum, learning_rate=args.lr,
        num_train_epochs=args.epochs, max_steps=2 if args.dry_run else -1, warmup_steps=3,
        lr_scheduler_type="linear", logging_steps=1, eval_strategy="steps", eval_steps=args.eval_steps,
        save_strategy="steps", save_steps=args.eval_steps, save_total_limit=2, load_best_model_at_end=True,
        metric_for_best_model="eval_loss", greater_is_better=False, bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(), optim="adamw_8bit", weight_decay=0.0, seed=7,
        dataloader_num_workers=0, report_to="none", remove_unused_columns=False)
    trainer = Trainer(model=model, args=targs, train_dataset=Dataset.from_list(train),
                      eval_dataset=Dataset.from_list(val),
                      data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100),
                      callbacks=[EarlyStoppingCallback(early_stopping_patience=2)])
    print(f"GPU before train: {torch.cuda.max_memory_allocated() / 2**30:.2f} GB")
    res = trainer.train()
    peak = torch.cuda.max_memory_allocated() / 2**30
    if args.dry_run:
        print(f"dry run ok, peak VRAM {peak:.2f} GB, {time.time() - t0:.0f}s")
        return
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    log = {"train_records": len(train), "val_records": len(val), "skipped": sk_t + sk_v,
           "tokens_p50": lens[len(lens) // 2], "tokens_max": lens[-1], "minutes": round((time.time() - t0) / 60, 1),
           "peak_vram_gb": round(peak, 2), "best_eval_loss": trainer.state.best_metric,
           "best_checkpoint": trainer.state.best_model_checkpoint, "train_loss": res.training_loss,
           "args": vars(args), "history": trainer.state.log_history}
    (Path(args.out).parent / "train_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in log.items() if k != "history"}, indent=2))


if __name__ == "__main__":
    main()
