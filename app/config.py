import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    # tiny .env reader so we don't need python-dotenv; real env vars win
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(key, default=""):
    return os.environ.get(key, default)


API_URL_TEMPLATE = env("CQA_API_URL_TEMPLATE")
DB_PATH = Path(env("DB_PATH", str(ROOT / "data" / "cqa.db")))
DATA_DIR = DB_PATH.parent
CORS_ORIGINS = [o for o in env("CORS_ORIGINS").split(",") if o]
EXPLANATION_LANG = env("EXPLANATION_LANG", "ru")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Defaults for the LLM settings. The Settings page can override these at runtime
# (stored in the DB), so switching provider/model never needs a code change.
LLM_DEFAULTS = {
    "provider": env("LLM_PROVIDER", "ollama"),  # ollama | openai | fake
    "model": env("LLM_MODEL", "qwen3:8b"),
    "ollama_url": env("OLLAMA_URL", "http://localhost:11434"),
    "ollama_num_ctx": env("OLLAMA_NUM_CTX", "12288"),
    "ollama_think": env("OLLAMA_THINK", "false"),
    # Kazakh dialogs go to a Kazakh-specialised model with its own provider, so the main model can be
    # cloud while KZ stays local. Empty model_kz = KZ dialogs use the main model.
    "model_kz": env("LLM_MODEL_KZ", "sherkala"),
    "provider_kz": env("LLM_PROVIDER_KZ", "ollama"),
    "ollama_think_kz": env("OLLAMA_THINK_KZ", ""),
    "openai_base_url": env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "concurrency": env("LLM_CONCURRENCY", "1"),
    "timeout_s": env("LLM_TIMEOUT_S", "300"),
}
OPENAI_API_KEY = env("OPENAI_API_KEY")  # key stays in env only, never in DB/UI
