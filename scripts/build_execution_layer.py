"""Compile pinned upstream sources; prefer MSBuild, else workspace-local Roslyn.

The fallback restores versioned NuGet packages and .NET 4.8 reference assemblies
under build/, using the installed .NET Framework runtime. No system installation
and no edits to third_party/ are required.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import config
from app.mcp_client.backend import require_source, extension_hashes

NS = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
NUGET = "https://api.nuget.org/v3-flatcontainer"


def package(name: str, version: str, folder: Path) -> tuple[Path, dict]:
    """Download exact package versions and verify NuGet's SHA-512 before extraction."""
    destination = folder / f"{name}.{version}"
    archive = folder / f"{name}.{version}.nupkg"
    receipt = destination / ".package-sha512"
    if receipt.is_file() and archive.is_file():
        actual = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
        if receipt.read_text().strip() == actual:
            return destination, {"id": name, "version": version, "sha512": actual}
    folder.mkdir(parents=True, exist_ok=True)
    normalized = version + ".0" * max(0, 3 - len(version.split(".")))
    registration = f"https://api.nuget.org/v3/registration5-semver1/{name.lower()}/{normalized}.json"
    print(f"Restoring {name} {version}", flush=True)
    with urllib.request.urlopen(registration, timeout=30) as response:
        metadata = json.load(response)
    url = metadata["packageContent"]
    with urllib.request.urlopen(metadata["catalogEntry"], timeout=30) as response:
        entry = json.load(response)
    if entry.get("packageHashAlgorithm") != "SHA512":
        raise RuntimeError(f"Unsupported NuGet checksum algorithm: {name}")
    expected = entry["packageHash"]
    temporary = archive.with_suffix(".download")
    digest = hashlib.sha512()
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    actual = base64.b64encode(digest.digest()).decode()
    if actual != expected:
        raise RuntimeError(f"NuGet checksum mismatch: {name} {version}")
    os.replace(temporary, archive)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or stat.S_ISLNK(item.external_attr >> 16):
                raise RuntimeError(f"Unsafe NuGet archive path: {item.filename}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as data, target.open("wb") as output:
                    shutil.copyfileobj(data, output)
    receipt.write_text(actual, encoding="ascii")
    return destination, {"id": name, "version": version, "sha512": actual}


def find_msbuild() -> str | None:
    found = shutil.which("MSBuild.exe")
    if found and "Microsoft.NET" not in found:
        return found
    locator = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if locator.is_file():
        result = subprocess.run([str(locator), "-latest", "-products", "*", "-requires", "Microsoft.Component.MSBuild",
                                 "-find", r"MSBuild\**\Bin\MSBuild.exe"], capture_output=True, text=True, check=True)
        if result.stdout.strip():
            return result.stdout.splitlines()[0].strip()
    return None


def portable_build(project: Path, output: Path, packages: Path) -> list[dict]:
    compiler, compiler_receipt = package("Microsoft.Net.Compilers.Toolset", "4.12.0", packages)
    framework, framework_receipt = package("Microsoft.NETFramework.ReferenceAssemblies.net48", "1.0.3", packages)
    receipts = [compiler_receipt, framework_receipt]
    for item in ET.parse(project.parent / "packages.config").getroot():
        _, receipt = package(item.attrib["id"], item.attrib["version"], packages)
        receipts.append(receipt)
    tree = ET.parse(project)
    ref_dir = framework / "build/.NETFramework/v4.8"
    references = list(ref_dir.glob("*.dll")) + list((ref_dir / "Facades").glob("*.dll"))
    if not (ref_dir / "mscorlib.dll").is_file():
        raise RuntimeError(".NET 4.8 reference assemblies were not restored")
    runtime = []
    for ref in tree.findall(".//m:Reference", NS):
        hint = ref.find("m:HintPath", NS)
        if hint is not None:
            path = Path(hint.text)
            if not path.is_absolute():
                path = (project.parent / path).resolve()
            if not path.is_file():
                raise RuntimeError(f"Missing assembly required by upstream project: {path}")
            references.append(path)
            runtime.append(path)
    sources = [(project.parent / item.attrib["Include"]).resolve() for item in tree.findall(".//m:Compile", NS)]
    response = output.parent / "execution-compile.rsp"
    lines = ["/nologo", "/target:exe", "/platform:anycpu", "/langversion:latest", "/optimize+", "/deterministic+", "/nostdlib+",
             f'/out:"{output / "SolidworksExecution.exe"}"']
    lines += [f'/reference:"{path}"' for path in references]
    lines += [f'"{path}"' for path in sources]
    response.write_text("\n".join(lines), encoding="utf-8-sig")
    print(f"Compiling {len(sources)} upstream C# files with workspace-local Roslyn", flush=True)
    subprocess.run([str(compiler / "tasks/net472/csc.exe"), "@" + str(response)], check=True)
    for path in runtime:
        shutil.copy2(path, output / path.name)
    shutil.copy2(project.parent / "app.config", output / "SolidworksExecution.exe.config")
    return receipts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msbuild")
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    source = require_source()
    original = config.SOLIDWORKS_REPO_DIR / "execution/solidworks"
    work = config.BUILD_DIR / "execution-src"
    output = config.BUILD_DIR / "execution-bin"
    shutil.copytree(original, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns("bin", "obj", "packages"))
    output.mkdir(parents=True, exist_ok=True)
    project = work / "SolidworksExecution/SolidworksExecution.csproj"
    # Extend the copied project, preserving the pinned vendor sources verbatim.
    tree = ET.parse(project)
    namespace = NS["m"]
    ET.register_namespace("", namespace)
    group = ET.SubElement(tree.getroot(), f"{{{namespace}}}ItemGroup")
    for extension in extension_hashes():
        target = project.parent / "ApplicationExtensions" / Path(extension).name
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(ROOT / extension, target)
        ET.SubElement(group, f"{{{namespace}}}Compile", Include=str(target.relative_to(project.parent)))
    tree.write(project, encoding="utf-8", xml_declaration=True)
    msbuild = args.msbuild or (None if args.portable else find_msbuild())
    receipts = []
    if msbuild:
        print(f"Building with {msbuild}", flush=True)
        # Reference assemblies are local to the workspace even when the installed
        # VS build tools lack the .NET 4.8 targeting pack.
        framework, receipt = package("Microsoft.NETFramework.ReferenceAssemblies.net48", "1.0.3", work / "packages")
        receipts.append(receipt)
        # Upstream's Owin HintPath uses 1.0, while NuGet normalizes it to 1.0.0.
        # Restore that declared path in the BUILD copy, leaving upstream intact.
        for item in ET.parse(project.parent / "packages.config").getroot():
            _, receipt = package(item.attrib["id"], item.attrib["version"], work / "packages")
            receipts.append(receipt)
        subprocess.run([msbuild, str(work / "SolidworksExecution.sln"), "-restore", "-p:RestorePackagesConfig=true", "-t:Build",
                        "-p:Configuration=Release", f"-p:TargetFrameworkRootPath={framework / 'build'}{os.sep}",
                        f"-p:OutputPath={output}{os.sep}", "-nologo", "-v:minimal"], check=True)
    else:
        receipts = portable_build(project, output, work / "packages")
    exe = output / "SolidworksExecution.exe"
    if not exe.is_file():
        raise RuntimeError("Build did not produce SolidworksExecution.exe")
    # Some upstream interop references have Private=False; carry all three next
    # to our executable rather than relying on machine-specific GAC entries.
    for ref in ET.parse(project).findall(".//m:Reference", NS):
        if ref.attrib["Include"].startswith("SolidWorks.Interop."):
            hint = ref.find("m:HintPath", NS)
            shutil.copy2(Path(hint.text), output / Path(hint.text).name)
    manifest = {"version": 1, "source_revision": source["revision"], "source_url": source["url"],
                "extensions": extension_hashes(),
                "mode": "msbuild" if msbuild else "portable_roslyn", "packages": receipts,
                "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Built: {exe}", flush=True)
    return 0


if __name__ == "__main__":
    main()
