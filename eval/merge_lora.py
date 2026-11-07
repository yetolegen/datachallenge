"""Merge the trained LoRA into the original 16-bit Qwen3-8B, shard by shard (low RAM), for `ollama create`.

C:/sft/venv/Scripts/python eval/merge_lora.py --base C:/sft/qwen3-8b-16 --lora C:/sft/lora --out C:/sft/merged

Ollama 0.40 dropped ADAPTER support and Unsloth won't merge into the nf4 training base, so we merge into the
official bf16 weights ourselves: W += (alpha / r) * B @ A for every adapted Linear. Then:
  ollama create qwen3-cqa --quantize q4_K_M -f Modelfile   (FROM C:/sft/merged + qwen3:8b's TEMPLATE/PARAMETERs)
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="C:/sft/qwen3-8b-16")
    ap.add_argument("--lora", default="C:/sft/lora")
    ap.add_argument("--out", default="C:/sft/merged")
    a = ap.parse_args()
    base, lora, out = Path(a.base), Path(a.lora), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    cfg = json.loads((lora / "adapter_config.json").read_text(encoding="utf-8"))
    assert not cfg.get("use_dora") and not cfg.get("use_rslora") and not cfg.get("fan_in_fan_out"), cfg
    scale = cfg["lora_alpha"] / cfg["r"]

    pairs = {}
    with safe_open(lora / "adapter_model.safetensors", "pt") as f:
        for k in f.keys():
            # base_model.model.model.layers.0.mlp.down_proj.lora_A.weight -> model.layers.0.mlp.down_proj.weight
            name, part = k.removeprefix("base_model.model.").rsplit(".lora_", 1)
            pairs.setdefault(name + ".weight", {})[part.split(".")[0]] = f.get_tensor(k)
    print(f"{len(pairs)} adapted weights, scale {scale}")

    done = set()
    for shard in sorted(base.glob("model-*.safetensors")):
        tensors = {}
        with safe_open(shard, "pt") as f:
            meta = f.metadata()
            for k in f.keys():
                t = f.get_tensor(k)
                if k in pairs:
                    A, B = pairs[k]["A"].float(), pairs[k]["B"].float()
                    assert (B.shape[0], A.shape[1]) == tuple(t.shape), (k, t.shape, A.shape, B.shape)
                    t = (t.float() + scale * (B @ A)).to(t.dtype)
                    done.add(k)
                tensors[k] = t.contiguous()
        save_file(tensors, out / shard.name, metadata=meta or {"format": "pt"})
        print("merged", shard.name, len(tensors), "tensors")
        del tensors

    missing = set(pairs) - done
    assert not missing, f"adapter weights not found in base: {sorted(missing)[:5]}"
    for f in base.iterdir():  # config, tokenizer, index: unchanged
        if f.is_file() and not f.name.startswith("model-") and f.suffix in (".json", ".txt", ".model", ".jinja"):
            shutil.copy2(f, out / f.name)
    print(f"done: {len(done)} weights merged into {out}")


if __name__ == "__main__":
    main()
