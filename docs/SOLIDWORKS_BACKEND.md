# SolidPilot MCP and our custom features

The application uses https://github.com/eyfel/mcp-server-solidworks.git as its
SolidWorks backend. The dependency is pinned to
`506ef87a5c3b772e177b412367e7361a3937e79a` in
`third_party/solidworks.lock.json`. The checkout is fetched locally rather than
committed into this repository. Its license stays with the upstream source.

## Layers

MCP is an automation interface, not a SolidWorks license. Without an activated
SolidWorks installation, use source/build checks, `--probe`, drawing reading,
planning and offline tests. Do not run `--ready` or build jobs expecting native
CAD output. MCP discovery checks do not certify a live licensed CAD session;
no successful live CAD build is claimed by this integration.

1. Our drawing/planning and approval workflow produces a validated build list.
2. `app/menu/` adapts approved items to our nine custom feature builders in
   `app/features/`. Their geometry, measurements and position checks remain ours.
3. `SolidWorksClient` connects over MCP to the unmodified SolidPilot adapter.
   The 47 native tools are available through the typed wrapper and `call()`.
4. SolidPilot's deterministic compiler and C# execution server operate SolidWorks.

Our features use native tools and Feature Graph IR; they are not a replacement
server or an alternate COM implementation. Native tools/IR operations can be used
when implementing more custom feature recipes. They are not automatically added
to the reader's approved menu merely because the backend exposes them. For
example, native loft/sweep capability does not by itself supply our missing
parameter interpretation, placement and per-feature verification.

## Reproduce setup

```powershell
.venv\Scripts\python.exe scripts\setup_solidworks_backend.py --fetch
.venv\Scripts\python.exe scripts\setup_solidworks_backend.py --build
.venv\Scripts\python.exe scripts\setup_solidworks_backend.py --probe --catalog storage\solidworks-tools.json
```

- Fetch checks out the exact revision. An existing checkout with different or
  edited source is refused rather than reset or overwritten.
- Build copies C# sources to `build/execution-src` and compiles into
  `build/execution-bin`. MSBuild is discovered with `vswhere` or PATH. The .NET 4.8
  reference assemblies and pinned upstream NuGet dependencies are restored under
  the workspace, with SHA-512 verification against NuGet catalog metadata.
- On this machine, MSBuild 18 was discovered; the missing system targeting pack
  is supplied by the workspace package. No Visual Studio installation was needed.
- If MSBuild is absent, the builder has an explicit/local Roslyn fallback. Run
  `scripts/build_execution_layer.py --portable` to select it. This still requires
  the installed .NET Framework runtime and SolidWorks interop assemblies.
- The existing PowerShell build script remains a wrapper around this builder;
  `-MSBuild`, `-PythonExe` and `-Portable` override discovery.
- Probe starts only the Python MCP adapter. It checks the real tool names and
  parameter schemas, reads the feature-graph schema resource, and validates our
  graph-based menu examples. It does not start SolidWorks or create documents.

To attach/start the native server and SolidWorks without editing documents:

```powershell
.venv\Scripts\python.exe scripts\setup_solidworks_backend.py --ready
```

Then run the existing approved build-list workflow normally. It uses this backend
automatically; there is no separate model API key or MCP client configuration.
The native server listens on `http://localhost:5000`; the browser still uses 8765.

## Integration boundaries

- `app/mcp_client/adapter_host.py` launches upstream and redirects its adapter log
  to `build/logs/adapter.log`. Bytecode writes are disabled. No vendor source is
  patched, and the application's `.env` is not reloaded in the child process.
- Provider secrets are removed from the child environment. The execution path is
  always explicit; the bundled upstream executable is never launched.
- Source fingerprints, Git revision and tracked-file cleanliness are checked.
  The compiled executable is bound to the revision by `build-manifest.json` and
  its SHA-256. Custom executable deployments must carry this manifest as well.
- The existing generated wrappers are compared with live MCP schemas on every
  connection. Missing tools or changed parameter names fail before CAD operations.
- Custom Feature Graph submissions also run upstream's plan-time IR validator
  before the MCP call. Runtime geometry checks still run after execution.
- Source location may be overridden with `SOLIDWORKS_MCP_REPO`; the lock still
  applies. Keep changes to our integration and features outside that checkout.

## Extending capabilities

The application now adds `inspect_assembly` alongside the 47 upstream tools.
Its owned C# endpoint is compiled into the build copy, fingerprinted in the build
manifest, and registered by our adapter host. Vendor sources and generated tool
wrappers remain unchanged. See [Assembly workflow](ASSEMBLY_WORKFLOW.md).

Add a custom recipe by defining its parameter model, menu adapter, native MCP/IR
operations and geometric expectations. Add offline preparation tests, verify its
IR against the pinned compiler, then run representative live SolidWorks cases.
Only then expose the recipe to the drawing planner. Retain approval, source
provenance, repair guards and measured verification.

Upstream updates are deliberate: fetch the new source, review compatibility,
update the lock/checksums, rebuild, run probe and tests, then repeat live cases.
Do not silently `git pull` the execution backend at application startup.
