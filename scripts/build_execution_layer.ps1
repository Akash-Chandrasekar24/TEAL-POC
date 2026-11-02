<#
  Builds the SolidWorks execution server (C#, .NET Framework 4.8) from a COPY of the vendored source, into build\execution-bin\.
  Needs: Visual Studio 2022 Build Tools (MSBuild) and the .NET Framework 4.8 targeting pack, SolidWorks installed (its interop DLLs).
  The shipped executable inside third_party\ is NEVER run; the program refuses to start it. Run this once per machine, or after the source changes.

  powershell -ExecutionPolicy Bypass -File scripts\build_execution_layer.ps1
#>
param(
    [string]$MSBuild = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "third_party\mcp-server-solidworks-main\execution\solidworks"
$work = Join-Path $root "build\execution-src"
$out = Join-Path $root "build\execution-bin\"

if (-not (Test-Path $MSBuild)) { throw "MSBuild not found at $MSBuild (pass -MSBuild <path>)" }
if (-not (Test-Path $src)) { throw "source not found: $src" }

# the build happens in a copy, so nothing is written inside third_party\
robocopy $src $work /E /XD bin obj | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }

# -p:RestorePackagesConfig=true is required: without it the NuGet restore silently does nothing and the build fails
& $MSBuild "$work\SolidworksExecution.sln" -restore -p:RestorePackagesConfig=true -t:Build -p:Configuration=Debug "-p:OutputPath=$out" -nologo -v:minimal
if ($LASTEXITCODE -ne 0) { throw "MSBuild failed ($LASTEXITCODE)" }
Write-Host "built: $out"
