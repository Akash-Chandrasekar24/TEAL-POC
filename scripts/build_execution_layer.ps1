<#
  Builds the SolidWorks execution server (C#, .NET Framework 4.8) from a COPY of the vendored source, into build\execution-bin\.
  Finds Visual Studio MSBuild automatically; .NET 4.8 references are restored inside build/. Without MSBuild, uses workspace-local Roslyn.
  Needs SolidWorks installed (its interop DLLs) and the .NET Framework runtime. Downloads pinned NuGet packages; no global tooling installation.
  The shipped executable inside third_party\ is NEVER run; the program refuses to start it. Run this once per machine, or after the source changes.

  powershell -ExecutionPolicy Bypass -File scripts\build_execution_layer.ps1
#>
param(
    [string]$MSBuild = "",
    [string]$PythonExe = "",
    [switch]$Portable
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonExe) {
    $localPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $PythonExe = if (Test-Path -LiteralPath $localPython) { $localPython } else { "python" }
}
$buildArguments = @((Join-Path $PSScriptRoot "build_execution_layer.py"))
if ($MSBuild) { $buildArguments += @("--msbuild", $MSBuild) }
if ($Portable) { $buildArguments += "--portable" }
& $PythonExe @buildArguments
if ($LASTEXITCODE -ne 0) { throw "SolidWorks execution build failed (exit $LASTEXITCODE)" }
