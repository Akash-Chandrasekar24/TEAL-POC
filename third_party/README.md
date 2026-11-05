# third_party

Code from other people. We do not edit or commit the checkout. We commit its
dependency lock (`solidworks.lock.json`) and setup scripts so it can be reproduced.

## mcp-server-solidworks-main/

- **What:** SolidPilot, an MCP server for SolidWorks (repo name `mcp-server-solidworks`).
- **Author / copyright:** Çağatay Bakan, 2025-2026.
- **Licence:** GNU AGPL-3.0 (see `LICENSE` inside). A separate commercial licence is offered by the author.
- **Current source:** cloned from upstream and pinned to `506ef87a5c3b772e177b412367e7361a3937e79a`.
- **Integrity:** revision and normalized source checksums in `solidworks.lock.json`.
- **Historical source:** the previous setup used a Downloads zip with SHA-256
  `42986DD1A2E91FEECBF999E7E43112682A1DD8AC29C73DA13B6936A15116D30B`; that archive is not required now.
- **Upstream URL:** https://github.com/eyfel/mcp-server-solidworks (clone it, or download the zip, into `third_party\mcp-server-solidworks-main\`)
- **Targets:** SolidWorks 2026 SP3 (this machine has 2026 SP4.1).

## Rules

- Never edit files inside `mcp-server-solidworks-main/`.
- Never run `mcp-server-solidworks-main/execution/solidworks/SolidworksExecution/bin/Debug/SolidworksExecution.exe`
  (a prebuilt binary that came with the zip). We build the C# layer from a copy of the source into this project's `build\execution-bin\`
  and point `EXECUTION_EXE_PATH` at that build.

Setup: `python scripts/setup_solidworks_backend.py --fetch --build --probe`.
See [the integration guide](../docs/SOLIDWORKS_BACKEND.md). MCP discovery and custom
feature validation work offline; actual CAD execution requires a licensed SolidWorks installation.
