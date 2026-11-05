"""Launch unmodified upstream MCP, redirecting its file log outside the checkout.

Executed as a script in the child process. Do not import app.config here: doing so
would reload the application's .env after the parent stripped provider secrets.
"""
import os
import sys
from pathlib import Path


def main():
    sys.dont_write_bytecode = True
    repo = Path(os.environ["SOLIDWORKS_MCP_REPO"]).resolve()
    adapter = repo / "adapters" / "claude"
    sys.path.insert(0, str(adapter))
    import adapter_log
    adapter_log.LOG_PATH = os.environ["SOLIDWORKS_ADAPTER_LOG"]
    import server
    # Load by path: importing app.config would reload provider secrets from .env.
    import importlib.util
    spec = importlib.util.spec_from_file_location("assembly_tools", Path(__file__).with_name("assembly_tools.py"))
    extension = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extension)
    extension.register(server.mcp)
    server.mcp.run()


if __name__ == "__main__":
    main()
