from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import Settings
from ..domain import ArtifactManifest, ArtifactRef, FileEntry, WorkbenchError, digest, new_id
from ..events import RecordStore
from ..security import CancellationGate, Redactor, atomic_write, is_link, json_bytes, secure_path


class ArtifactStore:
    def __init__(self, root: Path, settings: Settings, records: RecordStore, redactor: Redactor):
        self.root, self.settings, self.records, self.redactor = root, settings, records, redactor
        for agent in settings.agents.values():
            secure_path(root, agent.output_dir).mkdir(parents=True, exist_ok=True)

    def role_root(self, agent: str) -> Path:
        return secure_path(self.root, self.settings.agents[agent].output_dir)

    def pointer(self, agent: str) -> dict:
        path = secure_path(self.role_root(agent), "current.json")
        if not path.exists():
            return {"revision": None, "ports": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if set(value) != {"revision", "ports"} or not isinstance(value["ports"], dict):
                raise ValueError("Invalid pointer fields")
            if value["revision"]:
                ArtifactRef.model_validate(value["revision"])
            for reference in value["ports"].values():
                ArtifactRef.model_validate(reference)
            return value
        except (OSError, ValueError) as exc:
            raise WorkbenchError(
                f"Invalid current.json in {self.settings.agents[agent].output_dir}"
            ) from exc

    def current(self, agent: str, port: str | None = None) -> ArtifactRef | None:
        pointer = self.pointer(agent)
        raw = pointer["ports"].get(port) if port else pointer["revision"]
        reference = ArtifactRef.model_validate(raw) if raw else None
        if reference and reference.agent_id != agent:
            raise WorkbenchError("Artifact pointer has an incorrect role")
        return reference

    def revision_path(self, ref: ArtifactRef) -> Path:
        if ref.agent_id not in self.settings.agents or not ref.revision_id.startswith("rev-"):
            raise WorkbenchError("Invalid artifact identity")
        expected = f"{self.settings.agents[ref.agent_id].output_dir}/revisions/{ref.revision_id}"
        if ref.path != expected or "/" in ref.revision_id or "\\" in ref.revision_id:
            raise WorkbenchError("Artifact reference escapes its role revision store")
        return secure_path(self.root, expected)

    def manifest(self, ref: ArtifactRef, *, verify=True) -> ArtifactManifest:
        path = self.revision_path(ref)
        try:
            raw = secure_path(path, "_manifest.json").read_bytes()
            if digest(raw) != ref.manifest_hash:
                raise WorkbenchError("Accepted artifact manifest changed outside the application")
            manifest = ArtifactManifest.model_validate_json(raw)
            if manifest.revision_id != ref.revision_id or manifest.agent_id != ref.agent_id:
                raise WorkbenchError("Artifact manifest identity mismatch")
            if verify:
                actual = self.file_entries(path)
                if actual != manifest.files:
                    raise WorkbenchError(
                        "Accepted artifacts were modified externally; restore the revision"
                    )
            return manifest
        except (OSError, ValueError) as exc:
            raise WorkbenchError(f"Cannot read accepted revision {ref.revision_id}") from exc

    def read(self, ref: ArtifactRef, filename: str) -> str:
        manifest = self.manifest(ref)
        if filename not in manifest.files:
            raise WorkbenchError("File is not part of the accepted artifact manifest")
        return secure_path(self.revision_path(ref), filename, artifact=True).read_text(
            encoding="utf-8"
        )

    def file_entries(self, directory: Path) -> dict[str, FileEntry]:
        result, total, folded = {}, 0, set()
        for path in sorted(directory.rglob("*")):
            if is_link(path):
                raise WorkbenchError("Links are not allowed in artifact revisions")
            if path.is_dir():
                continue
            relative = path.relative_to(directory).as_posix()
            if relative == "_manifest.json":
                continue
            secure_path(directory, relative, artifact=True)
            if relative.casefold() in folded:
                raise WorkbenchError("Artifact names collide on a case-insensitive filesystem")
            folded.add(relative.casefold())
            size = path.stat().st_size
            if size > self.settings.artifacts.max_file_bytes:
                raise WorkbenchError("Artifact exceeds the per-file size limit")
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeError as exc:
                raise WorkbenchError("Version one accepts UTF-8 text artifacts only") from exc
            if self.redactor.contains_secret(text):
                raise WorkbenchError("Artifact publication rejected: contains a configured secret")
            total += len(data)
            if total > self.settings.artifacts.max_revision_bytes:
                raise WorkbenchError("Artifact revision exceeds the size limit")
            result[relative] = FileEntry(size=len(data), sha256=digest(data))
            if len(result) > self.settings.artifacts.max_files:
                raise WorkbenchError("Artifact revision contains too many files")
        return result

    def stage(self, agent: str, invocation: str, baseline: ArtifactRef | None) -> Path:
        directory = secure_path(self.role_root(agent), f".staging/{invocation}")
        directory.mkdir(parents=True, exist_ok=False)
        if baseline:
            if baseline.agent_id != agent:
                raise WorkbenchError("A role may stage only its own baseline")
            manifest = self.manifest(baseline)
            for name in manifest.files:
                if name.startswith("evidence/") and name != "evidence/latest.json":
                    continue  # Prior evidence remains in its immutable historical revision.
                destination = secure_path(directory, name, artifact=True)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.revision_path(baseline) / name, destination)
        return directory

    def write(self, stage: Path, name: str, text: str):
        target = secure_path(stage, name, artifact=True)
        if any(
            existing.casefold() == name.casefold() and existing != name
            for existing in self.file_entries(stage)
        ):
            raise WorkbenchError("Artifact names collide on a case-insensitive filesystem")
        if self.redactor.contains_secret(text):
            raise WorkbenchError("Refusing to write a configured secret into an artifact")
        if len(text.encode()) > self.settings.artifacts.max_file_bytes:
            raise WorkbenchError("Artifact exceeds the per-file size limit")
        atomic_write(target, text.encode())
        self.file_entries(stage)

    def commit(
        self,
        *,
        agent: str,
        stage: Path,
        run_id: str,
        cycle_id: str,
        invocation_id: str,
        contract: str,
        inputs: dict[str, ArtifactRef],
        summary: str,
        port: str,
        gate: CancellationGate,
        simulated=False,
    ) -> ArtifactRef:
        gate.check()
        entries = self.file_entries(stage)
        if not entries:
            raise WorkbenchError("Cannot accept an empty artifact revision")
        revision = new_id("rev")
        relative = f"{self.settings.agents[agent].output_dir}/revisions/{revision}"
        manifest = ArtifactManifest(
            revision_id=revision,
            agent_id=agent,
            run_id=run_id,
            cycle_id=cycle_id,
            invocation_id=invocation_id,
            contract=contract,
            files=entries,
            inputs=inputs,
            summary=summary,
            simulated=simulated,
        )
        manifest_data = json_bytes(manifest.model_dump())
        atomic_write(stage / "_manifest.json", manifest_data)
        sealed = secure_path(self.root, relative)
        sealed.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(sealed)
        ref = ArtifactRef(
            revision_id=revision, agent_id=agent, path=relative, manifest_hash=digest(manifest_data)
        )
        pointer = self.pointer(agent)
        pointer["revision"] = ref.model_dump()
        pointer["ports"][port] = ref.model_dump()
        self.records.journal(revision, agent, "prepared", ref.model_dump())
        # Only this short pointer publication is inside the cancellation gate.
        with gate.lock:
            gate.check()
            atomic_write(secure_path(self.role_root(agent), "current.json"), json_bytes(pointer))
        self.records.journal(revision, agent, "accepted", ref.model_dump())
        self.records.put(
            "revision",
            revision,
            {"id": revision, **manifest.model_dump(), "reference": ref.model_dump()},
        )
        return ref

    def recover(self):
        for revision, agent, data in self.records.pending_publications():
            ref = ArtifactRef.model_validate_json(data)
            pointer = self.pointer(agent)
            accepted = ref.model_dump() in [pointer["revision"], *pointer["ports"].values()]
            if accepted:
                manifest = self.manifest(ref)
                self.records.put(
                    "revision",
                    revision,
                    {"id": revision, **manifest.model_dump(), "reference": ref.model_dump()},
                )
            else:
                path = self.revision_path(ref)
                if path.exists():
                    shutil.rmtree(path)
            self.records.journal(
                revision, agent, "accepted" if accepted else "abandoned", ref.model_dump()
            )
        # Staging has no accepted contents and is safe to discard after exclusive startup.
        for agent in self.settings.agents:
            staging = secure_path(self.role_root(agent), ".staging")
            if staging.exists():
                shutil.rmtree(staging)

    def baseline(self) -> dict[str, ArtifactRef | None]:
        return {
            "requirements": self.current("system_engineer"),
            "product": self.current("software_engineer"),
            "tests": self.current("tester"),
            "test_results": self.current("tester", "test_results"),
            "ads": self.current("marketing"),
        }

    def prune(self, active: list[ArtifactRef] = ()):
        revisions = self.records.records("revision")
        cycle_times: dict[str, str] = {}
        for record in revisions:
            cycle_times[record["cycle_id"]] = max(
                cycle_times.get(record["cycle_id"], ""), record["created_at"]
            )
        retained = set(
            sorted(cycle_times, key=cycle_times.get)[-self.settings.artifacts.retain_cycles :]
        )
        pinned = {ref.revision_id for ref in active}
        current_refs = []
        for agent in self.settings.agents:
            pointer = self.pointer(agent)
            current_refs.extend(
                ArtifactRef.model_validate(raw)
                for raw in [pointer["revision"], *pointer["ports"].values()]
                if raw
            )
        for ref in current_refs:
            pinned.add(ref.revision_id)
            manifest = self.manifest(ref)
            # Preserve evidence sources, but not the entire previous-baseline ancestry.
            pinned.update(
                v.revision_id
                for k, v in manifest.inputs.items()
                if k
                in {
                    "requirements",
                    "product",
                    "implementation",
                    "prepared_tests",
                    "test_plan",
                    "test_results",
                }
            )
        for record in revisions:
            if record["cycle_id"] not in retained and record["id"] not in pinned:
                path = self.revision_path(ArtifactRef.model_validate(record["reference"]))
                if path.exists():
                    shutil.rmtree(path)
                record["pruned"] = True
                self.records.put("revision", record["id"], record)
