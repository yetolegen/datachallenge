# Setup for the RTX 4060 laptop (local model)

The 4060 has 8 GB VRAM, which fits an 8B model at 4-bit with room for long transcripts.
Recommended: run the **whole app** on this laptop (it becomes the demo machine). Option B at the
bottom is for running only the model here and the app on another laptop.

## 1. Install (once, ~15 min)

PowerShell:

```powershell
winget install Git.Git
winget install Python.Python.3.12
winget install Ollama.Ollama
```

Close and reopen PowerShell after installing, so the new commands are on PATH.

## 2. Get the model and check it works

```powershell
ollama pull qwen3:8b
ollama run qwen3:8b "Сәлеметсіз бе! Қысқаша жауап беріңізші: сіз кімсіз?" --verbose
```

- It should answer in Kazakh. `--verbose` prints `eval rate`; on a 4060 expect roughly 35–50 tokens/s.
- Check the GPU is used: `ollama ps` should show `100% GPU`. If it says CPU, update the NVIDIA driver.

### Kazakh model: Sherkala (used for all KZ dialogs)

**Fast route (~5 GB, ~15 min, no Hugging Face account).** A ready 4-bit GGUF exists on Hugging Face,
so Ollama pulls it directly. It's a community conversion (`chambette/sherkala`), not an official build.

1. Pull it:
   ```powershell
   ollama pull hf.co/chambette/sherkala:Q4_K_M
   ```
2. That GGUF ships with a bare `{{ .Prompt }}` template, which **drops the system prompt and roles**: the
   grading rules never reach the model. Add the chat format from the official model card and name it `sherkala`:
   ```powershell
   @'
   FROM hf.co/chambette/sherkala:Q4_K_M
   TEMPLATE """{{- range .Messages }}<|start_header_id|>{{ .Role }}<|end_header_id|>

   {{ .Content }}<|eot_id|>{{ end }}<|start_header_id|>assistant<|end_header_id|>

   """
   PARAMETER stop "<|eot_id|>"
   PARAMETER stop "<|end_of_text|>"
   PARAMETER stop "<|start_header_id|>"
   '@ | Out-File -Encoding ascii Modelfile.sherkala
   ollama create sherkala -f Modelfile.sherkala
   ```
3. Check: `ollama show sherkala --template` must start with `{{- range .Messages }}`. Then
   `ollama run sherkala "Сәлеметсіз бе! Қысқаша өзіңізді таныстырыңыз."` should answer in fluent Kazakh
   (~45 tokens/s on the 4060).
4. The app already sends KZ dialogs to `sherkala` (Настройки → "Казахские диалоги"). Check with
   **Проверить подключение** — it warns if the model isn't installed.

**Official route (only if the fast route breaks): 33 GB download.** The repo moved to
https://huggingface.co/inception42/Llama-3.1-Sherkala-8B-Chat (gated, auto-approved; non-commercial license —
fine for the hackathon). The weights are full precision, so it's **~33 GB**, not 16.

```powershell
pip install -U huggingface_hub
hf auth login          # paste a "Read" token at the prompt, never on the command line
hf download inception42/Llama-3.1-Sherkala-8B-Chat --local-dir C:\models\sherkala
```

Then use the Modelfile from step 2 with `FROM C:\models\sherkala` and run
`ollama create sherkala --quantize q4_K_M -f Modelfile.sherkala`. The official files have no chat template
either, so the `TEMPLATE` block is needed here too. Afterwards delete `C:\models\sherkala` to free 33 GB, and
revoke the token.

If the answers look broken, tell the team and use the fallback: clear the KZ model field in Настройки, so KZ
dialogs use qwen3 too.

## 3. Get the code

The repo is private: ask the repo owner to add your GitHub account as a collaborator first.

```powershell
git clone https://github.com/yetolegen/datachallenge.git
cd datachallenge
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

In `.env`:
- `CQA_API_URL_TEMPLATE=` paste the DEV link the team sends you **in a private message** (never commit it),
  with `group_id=DEV` at the end replaced by `group_id={group_id}`.
- `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen3:8b`, `OLLAMA_THINK=false`.

## 4. Run

```powershell
.venv\Scripts\activate
python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000 → **Наборы** → type `DEV` → **Загрузить** → **Оценить**.

- First, open **Настройки** → **Проверить подключение**: it must list `qwen3:8b`.
- A full DEV run is 65 dialogs × 8 criteria = 520 model calls. Leave the laptop plugged in.
  Progress is on the Наборы page; you can close the browser, the run continues on the server.
- If it's slow: in Settings set "Параллельных запросов" to 2 **and** run `setx OLLAMA_NUM_PARALLEL 2`
  (then restart Ollama from the tray). Check that VRAM still fits (`ollama ps`).

## 5. Report back to the team

From **Настройки → Стоимость и надёжность**: latency p50/p95, invalid-answer %, "требует проверки" %,
and how long the full DEV run took. Those numbers go into the README and the pitch.

---

## Option B: only the model on this laptop

On the 4060 laptop:

```powershell
setx OLLAMA_HOST 0.0.0.0
```

Quit Ollama from the system tray and start it again. Allow port 11434 when Windows Firewall asks
(private network only). Find the IP with `ipconfig` (IPv4 address, e.g. 192.168.1.23).

On the app laptop: **Настройки** → "Адрес Ollama" = `http://192.168.1.23:11434` → **Сохранить** →
**Проверить подключение**. Both laptops must be on the same Wi-Fi; uni/hotel Wi-Fi often blocks
device-to-device traffic, in that case use a phone hotspot or Option A.
