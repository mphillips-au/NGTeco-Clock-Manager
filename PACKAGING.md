# Windows Packaging, Installation & Release Guide

This document describes the Windows packaging architecture, installation lifecycles, configuration locations, network/firewall settings, versioning strategy, and release procedure for **NGTeco Clock Manager** (ClockBridge).

---

## 1. Overview & Architecture

NGTeco Clock Manager is packaged as a standalone, zero-dependency Windows desktop application.
End-user machines require **no developer tools, no Python installation, and no C++ runtime compilers**.

The packaging system uses:
- **PyInstaller**: Bundles the application, Python interpreter runtime, PySide6 GUI binaries, SQLite database drivers, and timezone data into a standalone directory (`onedir`).
- **Inno Setup 6**: Generates a standard, signed-ready Windows installer (`NGTecoClockManager-Setup-<version>.exe`) providing desktop shortcuts, Start Menu integration, optional automatic Windows startup, and clean uninstallation.

### Dual Build Targets

| Target | Build Command | Binary Path | Characteristics |
|---|---|---|---|
| **Development Build** | `python packaging/build.py --dev` | `dist\clockmanager-dev\clockmanager-dev.exe` | Console window enabled for real-time stdout/stderr debug logs, diagnostic inspections, and rapid testing. |
| **Release Build** | `python packaging/build.py --release` | `dist\clockmanager\clockmanager.exe` | Windows GUI subsystem (no console window pop-up), production brand icons, embedded Windows PE version metadata resource. |
| **Installer** | `python packaging/build.py --installer` | `dist\installer\NGTecoClockManager-Setup-<version>.exe` | Production installer executable bundling the release distribution. |

---

## 2. Directory Layout & Data Isolation

To ensure safety during upgrades and uninstalls, the application strictly separates application executable binaries from mutable user data:

### Application Program Files (Managed by Installer)
- **Default Path**: `%LOCALAPPDATA%\Programs\NGTeco Clock Manager` (e.g. `C:\Users\<User>\AppData\Local\Programs\NGTeco Clock Manager\`)
- **Contents**: `clockmanager.exe`, PySide6 libraries, Python runtime DLLs, application code modules, assets.
- **Elevation**: Non-elevated (`PrivilegesRequired=lowest`). Standard users without Windows Administrator rights can install, run, and update the software seamlessly.
- **Uninstall Behavior**: Cleanly wiped by the uninstaller.

### Application User Data (Managed by Application `AppPaths`)
- **Default Path**: `%LOCALAPPDATA%\NGTecoClockManager` (e.g. `C:\Users\<User>\AppData\Local\NGTecoClockManager\`)
- **Contents**:
  - `clockmanager.sqlite3` — Primary SQLite relational database.
  - `config.json` — User preferences and application configuration.
  - `logs\clockmanager.log` — Timestamped diagnostic and operational logs.
  - `backups\` — Database backups and export files.
- **Upgrade & Uninstall Guarantee**: **This directory is never deleted or modified by the uninstaller or installer.** Upgrading to a new release or uninstalling/reinstalling preserves all employee records, attendance punches, and device settings with zero data loss.

---

## 3. Database Schema Migrations & Upgrade Support

When a new version of the application launches:
1. `initialise_database()` examines the `schema_info` table inside `clockmanager.sqlite3`.
2. Any unapplied schema migrations (e.g., v1 through v7) are executed in strictly ordered SQLite transactions.
3. If the database was created by a newer version than the current executable, launch is safely halted with an informative error rather than risking silent database corruption or accidental downgrade.

---

## 4. Windows Startup & Registry Integration

The application supports optional automatic launch on Windows user login.

### Registry Specification
- **Hive**: `HKEY_CURRENT_USER` (HKCU)
- **Key**: `Software\Microsoft\Windows\CurrentVersion\Run`
- **Value Name**: `NGTecoClockManager`
- **Value Data**: `"<Path-To-clockmanager.exe>"`

### Configuration Methods
1. **During Installation**: Users can select the optional *"Start NGTeco Clock Manager on Windows startup"* checkbox in the Inno Setup wizard.
2. **Command Line (Headless / Scripted)**:
   ```powershell
   # Enable startup launch
   clockmanager.exe --enable-startup

   # Disable startup launch
   clockmanager.exe --disable-startup

   # Check current status
   clockmanager.exe --status-startup
   ```
3. **Application Settings UI**: Toggleable within the GUI Preferences dialog.

---

## 5. Windows Firewall & Network Considerations

NGTeco time clocks (such as the NG-MB1, MB20, and K40) communicate over the local area network using the ZK protocol on port **4370**.

### Port Requirements

| Port | Protocol | Direction | Purpose | Network Behavior |
|---|---|---|---|---|
| **4370** | **TCP** | Outbound | Direct device communication | Used for connecting to the clock, device commands, user synchronization, and attendance punch download. |
| **4370** | **UDP** | Outbound / Broadcast | Device discovery | Broadcasts to the local subnet (e.g. `255.255.255.255:4370`) to discover unconfigured clocks. |

### Network Considerations
- **Same Subnet / Non-NAT**: Clocks should be on the same local subnet as the PC or on a routed intranet without Network Address Translation (NAT).
- **VLAN Routing**: If clocks reside on a dedicated IoT or Security VLAN, ensure router/firewall access control lists allow bidirectional TCP 4370 traffic between the workstation and the clock IP.
- **Broadcast Boundaries**: UDP discovery does not cross subnet boundaries or routers. For cross-subnet clocks, configure the device directly by static IP.

### Automated Firewall Setup Commands

To view firewall recommendations or generate PowerShell commands directly from the application:
```powershell
clockmanager.exe --firewall-info
```

To configure Windows Defender Firewall via PowerShell (run as Administrator):
```powershell
# Allow outbound communication to clocks on TCP 4370
New-NetFirewallRule -DisplayName 'NGTeco Clock Manager Outbound TCP 4370' -Direction Outbound -Protocol TCP -RemotePort 4370 -Action Allow

# Allow device discovery on UDP 4370
New-NetFirewallRule -DisplayName 'NGTeco Clock Manager Discovery UDP 4370' -Direction Outbound -Protocol UDP -RemotePort 4370 -Action Allow
```

---

## 6. Versioning Strategy

The project adheres strictly to [Semantic Versioning 2.0.0](https://semver.org/) (`MAJOR.MINOR.PATCH`):
- **MAJOR**: Incompatible architectural changes or major database schema breaks requiring manual migration.
- **MINOR**: New business features, device support, or backward-compatible schema enhancements (e.g., `0.13.0` for Phase 13 Packaging).
- **PATCH**: Bug fixes, security patches, and minor UI improvements.

### Manifest Synchronization
When bumping the version, update:
1. `pyproject.toml` (`version = "X.Y.Z"`)
2. `src/clockmanager/__init__.py` (`__version__ = "X.Y.Z"`)
3. `packaging/installer.iss` (`#define MyAppVersion "X.Y.Z"`)
4. `packaging/version_info.txt` (`StringStruct('ProductVersion', 'X.Y.Z')` and `(X, Y, Z, 0)`)

Consistency is validated automatically by `tests/unit/test_packaging.py`.

---

## 7. Build Automation Script (`packaging/build.py`)

A unified script manages the entire build lifecycle:

```powershell
# Generate application icon assets (.ico and .png)
python packaging/build.py --assets

# Build development executable
python packaging/build.py --dev

# Build release executable
python packaging/build.py --release

# Compile Inno Setup installer
python packaging/build.py --installer

# Run smoke verification on the compiled release executable
python packaging/build.py --verify

# Clean build caches and dist/
python packaging/build.py --clean

# Complete end-to-end build (assets -> release -> installer -> verify)
python packaging/build.py --all
```

---

## 8. Release Procedure (GitHub Releases)

Releases are built by GitHub Actions (`.github/workflows/release.yml`) on a
clean Windows runner, not on a developer machine. Pushing a version tag is
the whole release:

1. **Bump the version** in the four manifests listed in section 6. The tag
   must match `clockmanager.__version__`; the workflow refuses otherwise.
2. **Update `CHANGELOG.md`** (entries under the version heading) and
   **`STATUS.md`**.
3. **Merge to `main`** through a pull request as usual.
4. **Tag and push** from an up-to-date `main`:
   ```powershell
   git checkout main
   git pull
   git tag -a v0.14.0 -m "Release v0.14.0"
   git push origin v0.14.0
   ```
5. **Wait for the "Release" workflow** (Actions tab, roughly 10 minutes). It
   runs `ruff`, `mypy` and the full test suite, builds the release
   executable and the Inno Setup installer, runs the `--verify` smoke test
   against the built executable, and publishes a GitHub Release named after
   the tag with two files attached:
   - `NGTecoClockManager-Setup-<version>.exe`
   - `NGTecoClockManager-Setup-<version>.exe.sha256`
6. **Share the release page:**
   `https://github.com/mphillips-au/NGTeco-Clock-Manager/releases/latest`

If any step fails, nothing is published. Fix the problem, delete the tag
(`git push origin :refs/tags/v0.14.0`, then `git tag -d v0.14.0`) and tag
again.

**Test build without publishing:** Actions tab > *Release* > *Run workflow*
builds the same installer and attaches it to the workflow run as an
artifact. No tag, no release.

**Local build** (no GitHub involved): `python packaging/build.py --all`
leaves the installer in `dist\installer\`. Needs Inno Setup 6 and the `dev`
extra (PyInstaller). `--all` now exits non-zero if the smoke test fails.

### Code signing

The installer is **not code-signed**. Windows SmartScreen shows "Windows
protected your PC" on first download; the user chooses *More info* >
*Run anyway*. The release notes say so. Signing (Azure Trusted Signing, or an
OV/EV certificate) removes the warning and is a later step.

---

## 9. Running in the Background (Notification Area)

Closing the main window hides it in the Windows notification area (next to
the clock) instead of quitting. Live capture and the periodic background
sync keep running.

- **Restore:** click the tray icon, choose *Open NGTeco Clock Manager* from
  its right-click menu, or launch the application again from any shortcut.
- **Quit:** right-click the tray icon > *Quit*, or *File > Exit* in the
  window. Either stops live capture and background sync cleanly.
- **Logout** ends the session as before and returns to the login dialog; the
  tray icon stays.
- **Preference:** *Settings > General > Running in the background*. Turning
  it off makes the close button quit, as in earlier versions. Stored per
  Windows user in Qt `QSettings`, like the theme.
- **Windows logoff/shutdown** always closes the application; hiding never
  blocks it.

### One running copy per data folder

Launching the application while it is already running brings the running
window forward instead of starting a second copy, which would run a second
sync loop and a second live-capture session against the same clock. The lock
is a per-session Windows named mutex derived from the data folder; the
running copy is reached through a named pipe restricted to the same Windows
account, and it acknowledges the request so a busy window is not missed.
`--data-dir` gives a separate, independent instance.

### Upgrading while it is running

The running application holds the mutex `NGTecoClockManagerRunning`, and the
installer's `AppMutex` names the same mutex. Setup and the uninstaller stop
and ask the user to quit it from the notification area before replacing any
files.
