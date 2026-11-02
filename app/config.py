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
_REPO_DIR = THIRD_PARTY_DIR / "mcp-server-solidworks-main"

# The MCP adapter we launch (read-only third-party code).
MCP_SERVER_PATH = _REPO_DIR / "adapters" / "claude" / "server.py"

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

# --- LLM (the API key is read from the environment only when a client is created; never stored here)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "medium").lower()  # low | medium | high
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "600"))     # a read takes 145-465 s; Google ends the request at this deadline (X-Server-Timeout), a 180 s default gave 504s
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
