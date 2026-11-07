"""Serve the SFT judge (4-bit Qwen3-8B + LoRA, exactly as trained) behind a minimal OpenAI-compatible API,
so the app's `openai` provider can run it. Evaluation only: Ollama 0.40 on Windows can't import the merged
model (no LoRA adapters, safetensors import needs MLX), so this is how we compare it on the holdout.

C:/sft/venv/Scripts/python eval/serve_sft.py              # trained (base + LoRA)  -> model name qwen3-cqa-hf
C:/sft/venv/Scripts/python eval/serve_sft.py --no-adapter # control (same base, same runtime, no LoRA)

App: Настройки → провайдер openai, OPENAI_BASE_URL http://localhost:8001/v1, any OPENAI_API_KEY.
Prompts are rendered like Ollama's qwen3 template with think=false (same as training). Greedy decoding.
There is no grammar-constrained JSON here; the app's validation + one retry still apply.
"""
import argparse
import os
import sys
import threading
import time

os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "0")
from unsloth import FastLanguageModel  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Body, FastAPI  # noqa: E402

app = FastAPI()
S = {}
LOCK = threading.Lock()  # one generation at a time on the 8 GB GPU


def render(messages):
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    user = [m["content"] for m in messages if m["role"] == "user"][-1]
    head = f"<|im_start|>system\n\n{system}<|im_end|>\n" if system else ""
    return head + f"<|im_start|>user\n{user} /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": S["name"], "object": "model"}]}


@app.post("/v1/chat/completions")
def chat(body: dict = Body(...)):
    tok, model = S["tok"], S["model"]
    ids = tok(render(body["messages"]), return_tensors="pt", add_special_tokens=False).to("cuda")
    t0 = time.time()
    with LOCK, torch.inference_mode():
        out = model.generate(**ids, max_new_tokens=int(body.get("max_tokens") or 1500), do_sample=False,
                             eos_token_id=tok.convert_tokens_to_ids("<|im_end|>"), pad_token_id=tok.pad_token_id)
    gen = out[0, ids["input_ids"].shape[1]:]
    text = tok.decode(gen, skip_special_tokens=True).strip()
    print(f"{len(ids['input_ids'][0])} in, {len(gen)} out, {time.time() - t0:.1f}s", flush=True)
    return {"id": "sft", "object": "chat.completion", "model": S["name"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": int(ids["input_ids"].shape[1]), "completion_tokens": int(len(gen))}}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--lora", default="C:/sft/lora")
    ap.add_argument("--base", default="C:/sft/qwen3-8b-bnb4")
    ap.add_argument("--no-adapter", action="store_true")
    ap.add_argument("--port", type=int, default=8001)
    a = ap.parse_args()
    path = a.base if a.no_adapter else a.lora
    model, tok = FastLanguageModel.from_pretrained(path, max_seq_length=8192, load_in_4bit=True)  # calls run to ~7.7k
    FastLanguageModel.for_inference(model)
    S.update(model=model, tok=tok, name="qwen3-base-hf" if a.no_adapter else "qwen3-cqa-hf")
    print(f"serving {S['name']} from {path} on :{a.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
