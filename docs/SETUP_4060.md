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

Optional second candidate for the Kazakh bake-off (bigger, may spill to CPU and be slower):
`ollama pull gemma3:12b`

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
