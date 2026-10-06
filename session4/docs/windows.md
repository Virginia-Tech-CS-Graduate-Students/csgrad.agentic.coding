# Windows setup

The initial desktop target is Windows 11 x64. Install Python 3.12 or newer and use a local project folder. Windows file names, path traversal, case collisions and junctions are checked by the artifact store.

In PowerShell, from the project directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m four_agent_workbench --mock
```

These commands do not require PowerShell activation scripts or execution-policy changes. PySide6 Essentials includes the Qt modules used by the desktop interface; no separate Qt SDK is needed.

## Real execution

Install Docker Desktop, enable its Linux-container backend, and start it. Its WSL 2 backend requires WSL/virtualization setup, even though the workbench itself runs as a native Windows Python application. See the official [Docker Desktop Windows installation instructions](https://docs.docker.com/desktop/setup/install/windows-install/) and [WSL backend documentation](https://docs.docker.com/desktop/features/wsl/).

```powershell
docker info --format '{{.OSType}}'
docker build -t four-agent-runner:0.1 docker
.\.venv\Scripts\python.exe -m four_agent_workbench --mock --real-tests
```

The Docker host must be local. Do not use a remote Docker context: bind-mounted artifact paths must exist on the same host/filesystem integration as the application. Avoid commas in workspace paths because Docker's mount syntax uses commas as separators.

## Live model and verification

Create `.env` from `.env.example` only if `.env` does not already exist. Fill in the complete endpoint, key and model name. Existing legacy variable names remain supported.

```powershell
.\.venv\Scripts\python.exe -m four_agent_workbench --check
.\.venv\Scripts\python.exe -m four_agent_workbench
```

For framework tests:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
$env:FAW_DOCKER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -q -m docker
```

The asyncio worker uses the native Python event loop so subprocess support remains available on Windows. Test processes run inside Linux containers; force-removing a container terminates its process tree without relying on Windows process-group emulation.

If Docker becomes unavailable during Stop, restore it and select Retry Cleanup. New starts remain disabled until cleanup succeeds.
