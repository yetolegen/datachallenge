import os
import sys
import tempfile
from pathlib import Path

# isolated DB + fake model before app modules read config
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["LLM_PROVIDER"] = "fake"
os.environ["LLM_MODEL"] = "fake"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
