from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from ..config import ExecutionSettings
from ..domain import CleanupError, ExecutionEvidence, WorkbenchError, digest, new_id
from ..security import CancellationGate, Redactor, StreamRedactor, is_link, json_bytes, secure_path
from .common import read_packages, validate_packages


class DockerRunner:
    simulated = False

    def __init__(self, root: Path, settings: ExecutionSettings, redactor: Redactor, run_id=""):
        self.root, self.settings, self.redactor, self.run_id = root, settings, redactor, run_id
        self.workspace = digest(str(root.resolve()).encode())[:20]
        self.active: set[str] = set()
        self.volumes: set[str] = set()
        self.image = settings.image
        self._cleanup_lock = asyncio.Lock()

    async def _command(self, args: list[str], *, timeout=15, preview=None) -> tuple[int, str]:
        environment = {
            k: v
            for k, v in os.environ.items()
            if k
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "HOME",
                "USERPROFILE",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
                "XDG_RUNTIME_DIR",
            }
        }
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=environment,
            )
        except (OSError, NotImplementedError) as exc:
            raise WorkbenchError(
                "Cannot launch Docker; install Docker and use a subprocess-capable Python event loop"
            ) from exc
        output = ""
        stream_redactor = StreamRedactor(self.redactor)
        try:
            async with asyncio.timeout(timeout):
                while chunk := await process.stdout.read(4096):
                    text = stream_redactor.feed(chunk.decode("utf-8", errors="replace"))
                    output = (output + text)[-262144:]
                    if preview and text:
                        preview(text)
                final = stream_redactor.feed("", final=True)
                output = (output + final)[-262144:]
                if preview and final:
                    preview(final)
                return await process.wait(), output
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def preflight(self):
        code, _ = await self._command(["info", "--format", "{{.OSType}}"])
        if code:
            raise WorkbenchError(
                "Docker is unavailable. Start Docker, select Linux containers, and check daemon access"
            )
        code, output = await self._command(
            ["image", "inspect", self.settings.image, "--format", "{{.Id}} {{.Os}}"]
        )
        if code:
            raise WorkbenchError(
                "Runner image is missing. Run: docker build -t four-agent-runner:0.1 docker"
            )
        parts = output.strip().split()
        if len(parts) != 2 or parts[1] != "linux":
            raise WorkbenchError("The execution runner requires a Linux Docker image")
        self.image = parts[0]  # Pin this run to the inspected immutable image ID.
        return self.image

    async def recover(self):
        code, output = await self._command(
            ["ps", "-aq", "--filter", f"label=faw.workspace={self.workspace}"]
        )
        if code:
            raise CleanupError(
                "Cannot check for interrupted Docker containers; restore daemon access"
            )
        self.active.update(output.split())
        code, output = await self._command(
            ["volume", "ls", "-q", "--filter", f"label=faw.workspace={self.workspace}"]
        )
        if code:
            raise CleanupError("Cannot inspect interrupted dependency volumes")
        self.volumes.update(output.split())
        await self.cleanup()

    async def _remove(self, name: str):
        code, output = await self._command(["rm", "-f", name])
        if code and "No such container" not in output:
            raise CleanupError(
                "Docker container cleanup failed; restore daemon access and retry cleanup"
            )
        self.active.discard(name)

    async def cleanup(self):
        async with self._cleanup_lock:
            failures = []
            for name in tuple(self.active):
                try:
                    await self._remove(name)
                except (WorkbenchError, TimeoutError) as exc:
                    failures.append(exc)
            for name in tuple(self.volumes):
                code, output = await self._command(["volume", "rm", "-f", name])
                if code and "no such volume" not in output.lower():
                    failures.append(CleanupError("Dependency-volume cleanup failed"))
                else:
                    self.volumes.discard(name)
            if failures:
                raise CleanupError(
                    "Cleanup blocked: Docker resources may still be active. Restore Docker and use Retry Cleanup."
                )

    async def _container(
        self,
        spec: dict,
        *,
        scratch: Path,
        mounts: list[tuple[Path, str]],
        volume: str | None,
        gate: CancellationGate,
        preview,
    ) -> dict:
        name = f"faw-{self.workspace[:8]}-{new_id('task')[-20:]}"
        output = scratch / new_id("output")
        output.mkdir()
        output.chmod(0o777)  # Only disposable execution output is writable by container UID 1000.
        specification = scratch / f"{name}.json"
        specification.write_bytes(json_bytes(spec))
        specification.chmod(0o644)
        install = spec["mode"] == "install"
        args = [
            "create",
            "--name",
            name,
            "--label",
            f"faw.workspace={self.workspace}",
            "--label",
            f"faw.run={self.run_id}",
            "--network",
            "bridge" if install else "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--cpus",
            str(self.settings.cpus),
            "--memory",
            f"{self.settings.memory_mb}m",
            "--pids-limit",
            str(self.settings.pids_limit),
            "--user",
            "0:0" if install else "1000:1000",
            "--tmpfs",
            "/tmp:rw,nosuid,size=128m",
            "--mount",
            f"type=bind,src={specification.resolve()},dst=/job.json,readonly",
            "--mount",
            f"type=bind,src={output.resolve()},dst=/out",
        ]
        for source, destination in mounts:
            # Commas have special meaning in Docker's --mount parser; fail rather than misparse.
            if "," in str(source):
                raise WorkbenchError("Docker workspace paths cannot contain commas")
            args += ["--mount", f"type=bind,src={source.resolve()},dst={destination},readonly"]
        if "," in str(scratch):
            raise WorkbenchError("Docker workspace paths cannot contain commas")
        if volume:
            args += [
                "--mount",
                f"type=volume,src={volume},dst=/deps" + ("" if install else ",readonly"),
            ]
        args += [self.image]
        gate.check()
        # Register before creation; cancellation cannot lose the name of an in-flight create.
        self.active.add(name)
        try:
            code, _ = await self._command(args, timeout=30)
            if code:
                raise WorkbenchError(
                    "Docker could not create the execution container; check mounts and image"
                )
            gate.check()
            code, _ = await self._command(
                ["start", "--attach", name], timeout=spec["timeout"] + 15, preview=preview
            )
            gate.check()
            result_path = secure_path(output, "result.json")
            if not result_path.exists() or result_path.stat().st_size > 1048576:
                raise WorkbenchError(
                    f"Execution container returned no valid evidence (exit {code})"
                )
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise WorkbenchError("Execution container returned malformed evidence") from exc
            return result
        finally:
            # The controller also drains this registry, including when this coroutine is cancelled.
            await asyncio.shield(self._remove(name))

    async def _dependencies(self, packages, scratch, gate, preview) -> tuple[str | None, list[str]]:
        packages = validate_packages(packages)
        if not packages:
            return None, []
        gate.check()
        volume = f"faw-{self.workspace[:8]}-{new_id('deps')[-20:]}"
        self.volumes.add(volume)
        code, _ = await self._command(
            ["volume", "create", "--label", f"faw.workspace={self.workspace}", volume]
        )
        if code:
            raise WorkbenchError("Cannot create the isolated dependency volume")
        gate.check()
        result = await self._container(
            {
                "mode": "install",
                "packages": packages,
                "index": self.settings.package_index,
                "timeout": self.settings.dependency_timeout_seconds,
            },
            scratch=scratch,
            mounts=[],
            volume=volume,
            gate=gate,
            preview=preview,
        )
        if result.get("exit_code") != 0:
            raise WorkbenchError(
                "Wheel dependency installation failed; inspect activity output. Source builds are disabled"
            )
        return volume, result.get("packages", [])

    def _scratch(self, invocation_id: str, role_folder: str) -> Path:
        parent = secure_path(self.root, f"{role_folder}/.staging")
        parent.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=f"{invocation_id}-exec-", dir=parent))

    async def provision(self, packages, *, invocation_id, gate, preview, role_folder="src/product"):
        scratch = self._scratch(invocation_id, role_folder)
        volume = None
        try:
            volume, resolved = await self._dependencies(packages, scratch, gate, preview)
            return resolved
        finally:
            if volume:
                await self._drop_volume(volume)
            shutil.rmtree(scratch, ignore_errors=True)

    async def _drop_volume(self, volume):
        code, _ = await self._command(["volume", "rm", "-f", volume])
        if code:
            raise CleanupError(
                "Cannot remove dependency volume; cleanup must finish before restarting"
            )
        self.volumes.discard(volume)

    async def _snapshot(self, source: Path, destination: Path, gate: CancellationGate):
        """Private, read-only container inputs independent of the host's UID and umask."""
        destination.mkdir(mode=0o755)
        destination.chmod(0o755)
        for path in sorted(source.rglob("*")):
            gate.check()
            if is_link(path):
                raise WorkbenchError("Execution inputs cannot contain symlinks or junctions")
            relative = path.relative_to(source).as_posix()
            if relative == "_manifest.json":
                continue
            target = secure_path(destination, relative, artifact=True)
            if path.is_dir():
                target.mkdir(exist_ok=True)
                target.chmod(0o755)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                target.chmod(0o644)
            else:
                raise WorkbenchError("Execution inputs must be regular text files")
            await asyncio.sleep(0)

    async def execute(
        self,
        *,
        mode,
        product: Path,
        tests: Path,
        product_revision,
        test_hash,
        invocation_id,
        gate,
        preview,
        args=None,
        role_folder="src/tests",
    ):
        scratch = self._scratch(invocation_id, role_folder)
        volume = None
        try:
            product_copy = scratch / "product"
            await self._snapshot(product, product_copy, gate)
            if product == tests:
                tests_copy = product_copy
            else:
                tests_copy = scratch / "tests"
                await self._snapshot(tests, tests_copy, gate)
            product, tests = product_copy, tests_copy
            packages = read_packages(product, tests) if product != tests else read_packages(product)
            volume, resolved = await self._dependencies(packages, scratch, gate, preview)
            gate.check()
            result = await self._container(
                {
                    "mode": mode,
                    "args": args or [],
                    "timeout": self.settings.test_timeout_seconds,
                },
                scratch=scratch,
                mounts=[(product, "/product"), (tests, "/tests")],
                volume=volume,
                gate=gate,
                preview=preview,
            )
            evidence = ExecutionEvidence(
                kind="real",
                product_revision=product_revision,
                test_hash=test_hash,
                image=self.image,
                packages=resolved,
                **self.redactor.clean(result),
            )
            if evidence.outcome in {"execution_error", "timeout", "no_tests"}:
                raise WorkbenchError(
                    f"Test/check execution did not complete: {evidence.outcome} (exit {evidence.exit_code})"
                )
            return evidence
        finally:
            if volume:
                await self._drop_volume(volume)
            shutil.rmtree(scratch, ignore_errors=True)
