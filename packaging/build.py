"""Build automation script for NGTeco Clock Manager.

Usage:
    python packaging/build.py --assets     # Generate icons
    python packaging/build.py --dev        # Build development executable
    python packaging/build.py --release    # Build release executable
    python packaging/build.py --installer  # Compile Inno Setup installer
    python packaging/build.py --all        # Build release executable & installer
    python packaging/build.py --verify     # Verify the built executables
    python packaging/build.py --clean      # Clean build artifacts
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGING_DIR = PROJECT_ROOT / "packaging"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = PACKAGING_DIR / "clockmanager.spec"
ISS_FILE = PACKAGING_DIR / "installer.iss"


def get_version() -> str:
    """Read version from clockmanager.__init__."""
    init_py = PROJECT_ROOT / "src" / "clockmanager" / "__init__.py"
    for line in init_py.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"').strip("'")
    raise SystemExit(f"__version__ not found in {init_py}")


def find_iscc() -> Path | None:
    """Locate Inno Setup compiler ISCC.exe."""
    candidate = shutil.which("ISCC.exe")
    if candidate:
        return Path(candidate)

    # Standard Windows install locations
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES(X86)", os.environ.get("PROGRAMFILES", ""))

    common_paths = [
        Path(local_app_data) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(program_files) / "Inno Setup 6" / "ISCC.exe",
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    ]
    for p in common_paths:
        if p.is_file():
            return p
    return None


def clean_build_artifacts() -> None:
    """Remove previous build and dist directories."""
    print("Cleaning build and dist directories...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR, ignore_errors=True)
    print("Clean completed.")


def ensure_assets() -> None:
    """Ensure icon assets are generated."""
    ico = PACKAGING_DIR / "assets" / "clockmanager.ico"
    png = PACKAGING_DIR / "assets" / "clockmanager.png"
    if not ico.is_file() or not png.is_file():
        print("Generating application icon assets...")
        from packaging.generate_assets import generate_assets

        generate_assets()
        print("Assets generated.")


def build_pyinstaller(mode: str) -> Path:
    """Build the application using PyInstaller in the specified mode ('dev' or 'release')."""
    ensure_assets()
    print(f"Building PyInstaller bundle in '{mode}' mode...")

    env = os.environ.copy()
    env["CLOCKMANAGER_BUILD_MODE"] = mode

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        str(SPEC_FILE),
    ]

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=False)
    if result.returncode != 0:
        print(f"PyInstaller build failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    target_dir = DIST_DIR / ("clockmanager-dev" if mode == "dev" else "clockmanager")
    target_exe = target_dir / ("clockmanager-dev.exe" if mode == "dev" else "clockmanager.exe")

    if not target_exe.is_file():
        print(f"Expected executable not found at {target_exe}", file=sys.stderr)
        sys.exit(1)

    print(f"Build succeeded: {target_exe}")
    return target_exe


def build_installer() -> Path:
    """Compile the Inno Setup installer."""
    iscc = find_iscc()
    if not iscc:
        print(
            "Inno Setup Compiler (ISCC.exe) not found. Please install Inno Setup 6.",
            file=sys.stderr,
        )
        sys.exit(1)

    release_exe = DIST_DIR / "clockmanager" / "clockmanager.exe"
    if not release_exe.is_file():
        print("Release executable not found. Building release executable first...")
        build_pyinstaller("release")

    version = get_version()
    print(f"Compiling Inno Setup installer for version {version} using {iscc}...")

    cmd = [
        str(iscc),
        f"/DMyAppVersion={version}",
        str(ISS_FILE),
    ]

    result = subprocess.run(cmd, cwd=str(PACKAGING_DIR), check=False)
    if result.returncode != 0:
        print(f"Inno Setup compilation failed with exit code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

    installer_exe = DIST_DIR / "installer" / f"NGTecoClockManager-Setup-{version}.exe"
    if not installer_exe.is_file():
        print(f"Installer not found at {installer_exe}", file=sys.stderr)
        sys.exit(1)

    print(f"Installer built successfully: {installer_exe} ({installer_exe.stat().st_size:,} bytes)")
    return installer_exe


def _run_built_exe(exe_path: Path, flag: str) -> subprocess.CompletedProcess[str]:
    """Run the built executable with one flag and capture its output.

    The frozen executable writes in the console code page, while this
    script may be decoding as UTF-8 (``PYTHONUTF8=1``); its messages contain
    an em dash, which is 0x97 in cp1252 and invalid UTF-8. Undecodable bytes
    are replaced so a mismatch can never crash the smoke test. Every check
    below looks for ASCII text only.
    """
    return subprocess.run(
        [str(exe_path), flag],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )


def verify_build(exe_path: Path | None = None) -> bool:
    """Run verification checks against the compiled executable."""
    if exe_path is None:
        exe_path = DIST_DIR / "clockmanager" / "clockmanager.exe"

    if not exe_path.is_file():
        print(f"Executable to verify not found: {exe_path}", file=sys.stderr)
        return False

    print(f"Verifying {exe_path}...")

    # 1. Test --version
    print("Testing --version flag...")
    res = _run_built_exe(exe_path, "--version")
    if res.returncode != 0 or "NGTeco Clock Manager" not in res.stdout:
        print(f"--version check failed: {res.stdout} {res.stderr}", file=sys.stderr)
        return False
    print(f"  Version output: {res.stdout.strip()}")

    # 2. Test --firewall-info
    print("Testing --firewall-info flag...")
    res = _run_built_exe(exe_path, "--firewall-info")
    if res.returncode != 0 or "4370" not in res.stdout:
        print(f"--firewall-info check failed: {res.stdout} {res.stderr}", file=sys.stderr)
        return False
    print("  Firewall info verified.")

    # 3. Test --headless bootstrap
    print("Testing --headless bootstrap mode...")
    res = _run_built_exe(exe_path, "--headless")
    if res.returncode != 0:
        print(f"--headless bootstrap failed: {res.stdout} {res.stderr}", file=sys.stderr)
        return False
    print("  Headless bootstrap output:")
    for line in res.stdout.strip().splitlines():
        print(f"    {line}")

    print("Verification passed successfully!")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Build automation for NGTeco Clock Manager.")
    parser.add_argument("--assets", action="store_true", help="Generate icon assets.")
    parser.add_argument("--dev", action="store_true", help="Build development executable.")
    parser.add_argument("--release", action="store_true", help="Build release executable.")
    parser.add_argument("--installer", action="store_true", help="Build Inno Setup installer.")
    parser.add_argument(
        "--all", action="store_true", help="Build release executable and installer."
    )
    parser.add_argument(
        "--verify", action="store_true", help="Run verification on built executable."
    )
    parser.add_argument("--clean", action="store_true", help="Clean build artifacts.")

    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        return

    if args.clean:
        clean_build_artifacts()

    if args.assets:
        ensure_assets()

    if args.dev:
        build_pyinstaller("dev")

    if args.release:
        build_pyinstaller("release")

    if args.installer:
        build_installer()

    if args.all:
        clean_build_artifacts()
        ensure_assets()
        exe = build_pyinstaller("release")
        build_installer()
        if not verify_build(exe):
            sys.exit(1)

    if args.verify and not verify_build():
        sys.exit(1)


if __name__ == "__main__":
    main()
