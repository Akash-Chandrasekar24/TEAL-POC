"""Paths and settings. Reads .env from the project root (a missing .env is fine)."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")  # does not override variables already set in the environment

# LangChain / LangGraph can send traces of every run (drawings, lists, model replies) to the LangSmith cloud service. This project never wants that:
# switch it off unless a value is set on purpose (setdefault never overrides an explicit setting).
for _name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ.setdefault(_name, "false")

THIRD_PARTY_DIR = PROJECT_ROOT / "third_party"
SOLIDWORKS_REPO_DIR = Path(os.getenv("SOLIDWORKS_MCP_REPO") or THIRD_PARTY_DIR / "mcp-server-solidworks-main")
_REPO_DIR = SOLIDWORKS_REPO_DIR
SOLIDWORKS_LOCK_PATH = THIRD_PARTY_DIR / "solidworks.lock.json"

# The MCP adapter we launch (read-only third-party code).
MCP_SERVER_PATH = _REPO_DIR / "adapters" / "claude" / "server.py"
MCP_HOST_PATH = PROJECT_ROOT / "app" / "mcp_client" / "adapter_host.py"

# The exe that ships inside the repo. We NEVER run it. Only used to recognise (and refuse) a copy of it.
SHIPPED_EXE_PATH = _REPO_DIR / "execution" / "solidworks" / "SolidworksExecution" / "bin" / "Debug" / "SolidworksExecution.exe"

BUILD_DIR = PROJECT_ROOT / "build"
LOG_DIR = BUILD_DIR / "logs"

# Our own build of the C# execution server (outside third_party/).
DEFAULT_EXECUTION_EXE = BUILD_DIR / "execution-bin" / "SolidworksExecution.exe"
EXECUTION_EXE_PATH = Path(os.getenv("EXECUTION_EXE_PATH") or DEFAULT_EXECUTION_EXE)

# Used by the wrapper to start the MCP adapter: the interpreter that is running this program (so the adapter always runs in the same
# virtual environment, wherever it lives). PYTHON_EXE in .env overrides it.
PYTHON_EXE = Path(os.getenv("PYTHON_EXE") or sys.executable)
SW_HTTP_TIMEOUT = float(os.getenv("SW_HTTP_TIMEOUT", "120"))
SW_ENSURE_TIMEOUT = float(os.getenv("SW_ENSURE_TIMEOUT", "180"))

# --- LLM (the API key is read from the environment only when a client is created; never stored here)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "medium").lower()  # low | medium | high
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "domo").strip().lower()        # both workers use Domo by default; explicit overrides remain supported
WORKER_LLM_PROVIDER = os.getenv("WORKER_LLM_PROVIDER", "domo").strip().lower() or "domo"
DOMO_INSTANCE = os.getenv("DOMO_INSTANCE", "").strip()                  # e.g. "acme" or "acme.domo.com"
DOMO_MODEL = os.getenv("DOMO_MODEL", "").strip() or "domo.openai.gpt-5.6-sol"
DOMO_REASONING_EFFORT = os.getenv("DOMO_REASONING_EFFORT", "high").strip().lower() or "high"
EXTRACTION_REASONING_EFFORT = os.getenv("EXTRACTION_REASONING_EFFORT", "medium").strip().lower() or "medium"
CAD_REASONING_EFFORT = os.getenv("CAD_REASONING_EFFORT", "high").strip().lower() or "high"
# R1/R2 only, Domo only: pages whose local OCR finds MIN..MAX text lines are "simple" and are read at SIMPLE_EFFORT instead of
# EXTRACTION_REASONING_EFFORT. Measured on 2 sheets (plate 10 lines, support block 17): same exact-text recall, 18-29 s vs 39-62 s.
# The dense clamp (25 lines) and pages with little or no OCR text keep the normal effort. MAX=0 switches this off.
EXTRACTION_SIMPLE_EFFORT = os.getenv("EXTRACTION_SIMPLE_EFFORT", "none").strip().lower() or "none"
EXTRACTION_SIMPLE_MIN_OCR_LINES = int(os.getenv("EXTRACTION_SIMPLE_MIN_OCR_LINES", "8"))
EXTRACTION_SIMPLE_MAX_OCR_LINES = int(os.getenv("EXTRACTION_SIMPLE_MAX_OCR_LINES", "20"))
DOMO_MAX_IN_FLIGHT = max(1, int(os.getenv("DOMO_MAX_IN_FLIGHT", "2")))  # Domo calls at once per process: ~6 stalled for 10 min (measured 2026-11-03)


def domo_token() -> str:
    """Read only when a Domo client is created; never stored in a module variable."""
    value = os.getenv("DOMO_DEVELOPER_TOKEN", "").strip()
    return "" if value.startswith("<") else value                       # "<your token>" copied from the example is not a token


LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "600"))     # a read takes 145-465 s; Google ends the request at this deadline (X-Server-Timeout), a 180 s default gave 504s

# --- Cache (docs/System_Architecture.docx 4.7): repeated model calls are answered from storage/cache. Never skips a human pause.
CACHE_DIR = Path(os.getenv("CACHE_DIR") or PROJECT_ROOT / "storage" / "cache")
LLM_CACHE = os.getenv("LLM_CACHE", "on").strip().lower() not in ("off", "0", "false", "no")   # run_job.py --no-cache turns it off per command
CACHE_MAX_MB = float(os.getenv("CACHE_MAX_MB", "5000"))                  # per tenant; least recently used entries go first. 0 = no limit
CACHE_RETENTION_DAYS = float(os.getenv("CACHE_RETENTION_DAYS", "0"))     # 0 = keep until evicted by size
TRACE_FILES = os.getenv("TRACE_FILES", "on").strip().lower() not in ("off", "0", "false", "no")   # keep what the model was shown / answered in storage/runs/<id>/files
TENANT = os.getenv("TENANT", "default").strip() or "default"             # the client namespace of every cache entry
# The predicted bounding box (where the items' material can be, worked out offline) is compared with the measured box of the finished part.
# warn (default): a violation is reported, nothing stops. stop: a violation stops the job at the review step. Tolerance in mm.
PREDICTED_BBOX_MODE = os.getenv("PREDICTED_BBOX_MODE", "warn").lower()
PREDICTED_BBOX_TOL_MM = float(os.getenv("PREDICTED_BBOX_TOL_MM", "0.2"))

# --- SolidWorks part templates. They decide the units of the saved/exported file (analyze_model is always metres).
# The default SolidWorks template Part.PRTDOT is in inches (IPS) and SolidWorks ships NO plain millimetre (MMGS) part template.
# templates/Part_MMDS.PRTDOT in this project is a millimetre template; SW_TEMPLATE_MM can point to another one.
STD_PART_TEMPLATE = Path(r"C:\ProgramData\SolidWorks\SOLIDWORKS 2026\templates\Part.PRTDOT")
_DEFAULT_MM_TEMPLATE = PROJECT_ROOT / "templates" / "Part_MMDS.PRTDOT"
SW_TEMPLATE_MM = os.getenv("SW_TEMPLATE_MM", "").strip() or (str(_DEFAULT_MM_TEMPLATE) if _DEFAULT_MM_TEMPLATE.is_file() else "")
SW_TEMPLATE_INCH = os.getenv("SW_TEMPLATE_INCH", "").strip() or str(STD_PART_TEMPLATE)
