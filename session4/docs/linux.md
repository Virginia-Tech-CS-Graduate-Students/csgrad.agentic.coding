# Linux setup

The initial desktop target is Ubuntu 24.04 x64. Python 3.12 is the documented baseline; Python 3.14 is also supported by the dependency set. A desktop session is needed for the GUI; headless mode and offscreen framework tests do not need one.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m four_agent_workbench --mock
```

If `venv` is unavailable, install your distribution's matching Python venv package. On minimal Ubuntu installations, Qt may also need `libegl1`, `libgl1`, `libxkbcommon-x11-0`, and `libxcb-cursor0`. Install missing system libraries through the distribution package manager; do not set `QT_QPA_PLATFORM=offscreen` for ordinary interactive use.

## Docker execution

Install Docker Engine using the [official Linux instructions](https://docs.docker.com/engine/install/). Make the daemon accessible to your account using your chosen rootless setup or documented Docker-group setup. The application never calls `sudo` itself.

```bash
docker info --format '{{.OSType}}'
docker build -t four-agent-runner:0.1 docker
.venv/bin/python -m four_agent_workbench --mock --real-tests
```

Use a local Docker context and a local workspace path without commas. A working directory and Python virtual environment alone are not a sandbox; generated code executes only through the Docker runner.

For live models, configure `.env` without replacing an existing file and launch without `--mock`. `--check` performs no provider call.

## Headless operation and tests

```bash
.venv/bin/python -m four_agent_workbench --mock --headless --cycles 10
.venv/bin/python -m four_agent_workbench --mock --headless --continuous
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
FAW_DOCKER_TESTS=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Ctrl+C cancels headless processing. Independent container deadlines bound orphaned work after a process crash; startup recovery removes labelled resources before the next real run.

Some restricted execution sandboxes prohibit even the local socket used by `asyncio.call_soon_threadsafe`. In such an environment, desktop worker-thread tests must be run with local IPC enabled. This is distinct from the application's model-network access and Docker test-network policy.
