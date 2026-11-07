# Eval / SFT changelog

## 2026-11-07 — LoRA fine-tune of the RU/EN judge (branch `sft`, experiment)

**Decision: keep prompted `qwen3:8b` in Настройки for the demo.** The trained model passes the agreed rule on
paper (+3.1 pts, swap test passes), but it can't be switched in through Настройки alone on this machine,
and the margin is 2 labels. Details and what it would take are below; the team decides.

### Holdout (same 8 dialogs everywhere, checklist v4, KZ always on `sherkala`)

`p1-ru-001 p1-ru-013 p1-ru-028 p1-ru-024 p1-en-011 p1-en-015 p1-kz-007 p1-kz-014`, 64 labels.

| Judge for RU/EN | Runtime | Holdout | RU | EN | KZ | Kappa | Total MAE | Invalid 1st answers |
|---|---|---|---|---|---|---|---|---|
| `qwen3:8b` prompted (current) | Ollama, Q4_K_M, JSON grammar | **76.6%** | 81.2% | 75.0% | 68.8% | 0.704 | 15.3 | 0 |
| Qwen3-8B untrained (control) | Python server, bnb 4-bit, no grammar | 65.6% | 68.8% | 56.2% | 68.8% | 0.566 | 27.1 | 9 of 57 |
| **`qwen3-cqa-hf` (base + LoRA)** | Python server, bnb 4-bit, no grammar | **79.7%** | **87.5%** | 75.0% | 68.8% | **0.743** | **10.4** | **0 of 48** |

- Trained vs control in the **same runtime: +14.1 pts** → the gain comes from training, not the runtime.
- Trained vs current production: **+3.1 pts = 51 vs 49 of 64 labels correct.** Clears the ≥ 3-pt bar by 0.1;
  2 labels is within noise on a 64-label holdout. Holdout is also easier than the rest of gold (76.6% vs ~61%).
- Per criterion (trained vs baseline): better on consultation 62.5→75, empathy 87.5→100, closing 75→87.5,
  dialog management 37.5→50; worse on contact 100→87.5 and needs 75→62.5.
- Latency: ~9.7 s per call (baseline ~10 s on Ollama).

**Criterion-swap test: pass.** Temporary checklist v5 rewrote *closing* as "any polite goodbye = Да" and was applied
to the 6 RU/EN holdout dialogs (42 cache hits + 6 new calls). The trained model flipped all 4 of its v4 "Нет"
verdicts to "Да" and quoted the new wording in the explanations. Checklist v4 was re-activated afterwards.

### Why not switch tonight

1. **Not a Настройки-only switch.** Ollama 0.40 on Windows can't load it: `ADAPTER` → "LoRA adapters are no longer
   supported"; importing merged safetensors needs the MLX runtime (unavailable on Windows), and `--quantize`
   only offers int4/int8/fp4 via that path. Serving it today needs `eval/serve_sft.py` (separate venv
   `C:/sft/venv`, port 8001) plus the app restarted with an `OPENAI_API_KEY` and provider = openai.
2. **GPU sharing.** The Python server holds ~6.2 GB; `sherkala` for KZ needs ~5 GB more on an 8 GB card.
3. **Margin.** +2 labels.

**To make it a real Настройки switch:** convert `C:/sft/merged` (bf16, LoRA already merged) to GGUF with
llama.cpp's `convert_hf_to_gguf.py` + `llama-quantize Q4_K_M`, then `ollama create qwen3-cqa -f Modelfile`
(FROM the .gguf + qwen3:8b's TEMPLATE/PARAMETERs, see `C:/sft/Modelfile.qwen3-cqa`). That needs approval to run
llama.cpp tooling on the demo laptop. Then re-run the holdout **in Ollama with the JSON grammar** before deciding.

### Data

- `eval/gold/silver_labels.csv`: 38 remaining DEV chats (31 RU/EN + 7 KZ), 304 labels, `annotator=agent`,
  labelled with checklist v4 rules + OPEN_QUESTIONS. Check: the 3 disclosure dialogs later added to gold
  (`p1-ru-009`, `p1-en-014`, `p1-kz-015`) agree 24/24 with silver.
- `eval/make_sft.py` → `eval/sft/{train,val}.jsonl`: inputs built exactly like the app (SYSTEM + transcript_block +
  criterion_block from the active checklist v4). Excludes the holdout, KZ (stays on sherkala) and calls.
  **288 train records (36 dialogs: 8 gold + 28 silver), 24 val (3 silver dialogs).** Targets: 195 reuse the app's own
  evidence/explanation where its verdict matched the label, 117 short templates.
- Leak guard: `seed.json` examples quote holdout dialogs `p1-ru-001` (contact, needs, closing) and `p1-ru-028`
  (dialog_management). They are dropped from training inputs; zero holdout client/operator lines in train/val.
  Evaluation stays fair (the judge never shows a dialog its own example), but avoid adding more holdout examples.

### Training (`eval/train_sft.py`, RTX 4060 Laptop 8 GB)

- QLoRA via Unsloth on `unsloth/Qwen3-8B-bnb-4bit`; r=16, alpha=16, all attention + MLP projections; lr 1e-4,
  batch 1 × grad-accum 8, 2 epochs (72 steps), max length 3072 (longest example 2924 tokens), bf16, adamw_8bit.
- Prompt rendered exactly like Ollama's qwen3 template with `think=false`; loss on the JSON answer only.
- **46 min**, peak VRAM 5.8 GB. Eval loss 0.599 (untrained) → 0.333 → … → **0.226**, falling at every check (no overfit).
- Merge: `eval/merge_lora.py` adds the LoRA to the official bf16 `Qwen/Qwen3-8B` shard by shard → `C:/sft/merged`.

### Reports

`eval/reports/` (gitignored, sent directly): holdout baseline 20261107-1919, trained 20261107-2042,
control 20261107-2052; full gold v4 baseline (26 dialogs) 20261107-2031 = 65.9%.
