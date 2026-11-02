# third_party

Code from other people. We do not edit it and do not commit it (see the root `.gitignore`).

## mcp-server-solidworks-main/

- **What:** SolidPilot, an MCP server for SolidWorks (repo name `mcp-server-solidworks`).
- **Author / copyright:** Çağatay Bakan, 2025-2026.
- **Licence:** GNU AGPL-3.0 (see `LICENSE` inside). A separate commercial licence is offered by the author.
- **Where we got it:** the file `mcp-server-solidworks-main.zip` from this machine's Downloads folder
  (file date 2026-10-09), unzipped here on 2026-10-11, unchanged.
- **Zip SHA-256:** `42986DD1A2E91FEECBF999E7E43112682A1DD8AC29C73DA13B6936A15116D30B`
- **Upstream URL:** https://github.com/eyfel/mcp-server-solidworks (clone it, or download the zip, into `third_party\mcp-server-solidworks-main\`)
- **Targets:** SolidWorks 2026 SP3 (this machine has 2026 SP4.1).

## Rules

- Never edit files inside `mcp-server-solidworks-main/`.
- Never run `mcp-server-solidworks-main/execution/solidworks/SolidworksExecution/bin/Debug/SolidworksExecution.exe`
  (a prebuilt binary that came with the zip). We build the C# layer from a copy of the source into `D:\solidworks\build\`
  and point `EXECUTION_EXE_PATH` at that build.
