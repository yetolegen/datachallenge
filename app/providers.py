"""LLM adapters. Each returns LLMResult; parsing/validation lives in judge.py."""
import asyncio
import json
import random
import time
from dataclasses import dataclass

import httpx

from app import config


@dataclass
class LLMResult:
    text: str
    in_tokens: int
    out_tokens: int
    latency_ms: int
    model: str
    provider: str


class ProviderError(Exception):
    pass


async def _post(url, body, headers, timeout):
    # retry transport errors, 429 and 5xx with backoff + jitter; other 4xx fail fast
    last = None
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=timeout) as cli:
                r = await cli.post(url, json=body, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                last = ProviderError(f"{r.status_code}: {r.text[:300]}")
            elif r.status_code >= 400:
                raise ProviderError(f"{r.status_code}: {r.text[:500]}")
            else:
                return r.json()
        except httpx.TransportError as e:
            last = ProviderError(f"нет связи с моделью ({url}): {e!r}")
        await asyncio.sleep(min(30, 2 ** attempt) + random.random())
    raise last


async def ollama(s, system, user, schema, meta):
    body = {"model": s["model"], "stream": False, "format": schema, "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": float(s.get("temperature", 0)), "num_ctx": int(s["ollama_num_ctx"]),
                        "num_predict": 1500}}
    think = str(s.get("ollama_think", "")).lower()
    if think in ("true", "false"):  # only for thinking models (qwen3); leave empty for others
        body["think"] = think == "true"
    t0 = time.perf_counter()
    data = await _post(s["ollama_url"].rstrip("/") + "/api/chat", body, {}, float(s["timeout_s"]))
    return LLMResult(data.get("message", {}).get("content", ""), data.get("prompt_eval_count", 0),
                     data.get("eval_count", 0), int((time.perf_counter() - t0) * 1000), s["model"], "ollama")


async def openai_compat(s, system, user, schema, meta):
    # works for OpenAI, Gemini's OpenAI endpoint, OpenRouter, LM Studio, vLLM...
    if not config.OPENAI_API_KEY:
        raise ProviderError("OPENAI_API_KEY не задан в .env")
    body = {"model": s["model"], "temperature": float(s.get("temperature", 0)), "max_tokens": 1500,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "verdict", "schema": schema}}}
    t0 = time.perf_counter()
    url = s["openai_base_url"].rstrip("/") + "/chat/completions"
    hdr = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    try:
        data = await _post(url, body, hdr, float(s["timeout_s"]))
    except ProviderError as e:
        if not str(e).startswith("400"):
            raise
        body["response_format"] = {"type": "json_object"}  # endpoint without json_schema support
        data = await _post(url, body, hdr, float(s["timeout_s"]))
    u = data.get("usage") or {}
    return LLMResult(data["choices"][0]["message"]["content"] or "", u.get("prompt_tokens", 0),
                     u.get("completion_tokens", 0), int((time.perf_counter() - t0) * 1000), s["model"], "openai")


async def fake(s, system, user, schema, meta):
    # deterministic stand-in for tests and offline UI work
    c, utts = meta["criterion"], meta["utterances"]
    ops = [u["uid"] for u in utts if u["speaker"] == "operator"] or [utts[0]["uid"]]
    out = {"reasoning": "Тестовый ответ FakeProvider.", "evidence_ids": ops[:1],
           "checked_range": {"from": utts[0]["uid"], "to": utts[-1]["uid"]}, "na_reason": "",
           "explanation": f"{c['title']} — тестовая оценка.", "confidence": "high"}
    if c["answer_type"] == "scale":
        out.update(verdict="scale", value=max(st["value"] for st in c["scale"]["steps"]))
    elif c.get("is_critical"):
        out["verdict"] = "no"
    elif c.get("na_allowed") and c["id"] == "identification":
        out.update(verdict="na", na_reason="Запрос не требовал идентификации.")
    else:
        out["verdict"] = "yes"
    return LLMResult(json.dumps(out, ensure_ascii=False), len(user) // 4, 120, 1, "fake", "fake")


PROVIDERS = {"ollama": ollama, "openai": openai_compat, "fake": fake}


async def complete(settings, system, user, schema, meta):
    fn = PROVIDERS.get(settings["provider"])
    if not fn:
        raise ProviderError(f"Неизвестный провайдер «{settings['provider']}»")
    return await fn(settings, system, user, schema, meta)
