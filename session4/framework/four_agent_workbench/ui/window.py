from __future__ import annotations

import json
from concurrent.futures import CancelledError

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..domain import ROLES, ConflictError, WorkbenchError
from .editors import PromptDialog
from .graph import WorkflowGraph

STYLE = """
QMainWindow, QDialog { background: #f6f8fa; }
QWidget { color: #263d4c; font-family: 'Segoe UI', 'DejaVu Sans', sans-serif; font-size: 10pt; }
QGroupBox { border: 1px solid #dde5eb; border-radius: 9px; margin-top: 13px; padding: 12px; background: white; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; font-weight: 600; }
QPushButton { border: 1px solid #c9d5df; border-radius: 6px; padding: 9px 14px; background: white; }
QPushButton:hover { border-color: #168378; background: #ecf5f3; }
QPushButton:disabled { color: #929ea8; background: #edf0f3; border-color: #e2e7eb; }
QPushButton#primary { background: #176e68; color: white; border-color: #176e68; }
QPushButton#primary:disabled { background: #acbdbb; border-color: #acbdbb; }
QPushButton#stop { background: #a93a40; color: white; border-color: #a93a40; font-weight: 600; min-width: 145px; }
QPushButton#stop:disabled { background: #ead9da; color: #a17c7e; border-color: #ead9da; }
QPlainTextEdit, QSpinBox, QComboBox { background: white; border: 1px solid #d5dfe7; border-radius: 5px; padding: 5px; }
QProgressBar { border: none; border-radius: 3px; background: #e5ebef; height: 7px; }
QProgressBar::chunk { background: #168378; border-radius: 3px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, controller, background):
        super().__init__()
        self.controller, self.background = controller, background
        self.future = None
        self.dialogs = {}
        self._run_id, self._cycle_id = "", ""
        self._stopping, self._pending_close, self._allow_close = False, False, False
        self._requested = None
        self._last_vision_check = 0
        self._validation_error = None
        self.setWindowTitle("Four-Agent Workbench")
        self.resize(1440, 940)
        self.setMinimumSize(1140, 800)
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(14)
        title_row = QHBoxLayout()
        title = QLabel("Four-Agent Workbench")
        title.setStyleSheet("font-size: 20pt; font-weight: 650; color: #173345;")
        title_row.addWidget(title)
        title_row.addStretch()
        label = "LIVE MODEL · DOCKER TESTS"
        if controller.mock:
            label = "MOCK MODEL · " + (
                "REAL DOCKER TESTS" if controller.real_tests else "SIMULATED TESTS"
            )
        badge = QLabel(label)
        badge.setStyleSheet(
            "background: #e4efef; color: #226f6a; padding: 8px 12px; border-radius: 7px; font-size: 9pt;"
        )
        title_row.addWidget(badge)
        layout.addLayout(title_row)
        top = QHBoxLayout()
        vision_group = QGroupBox("Master Prompt / Vision")
        vision_layout = QVBoxLayout(vision_group)
        self.vision = QPlainTextEdit()
        self.vision.setObjectName("visionEditor")
        self.vision.setMinimumHeight(90)
        self.vision.setMaximumHeight(145)
        try:
            self.vision_original, self.vision_hash = controller.prompts.load("vision")
        except WorkbenchError as exc:
            self.vision_original, self.vision_hash = "", ""
            self._validation_error = str(exc)
        self.vision.setPlainText(self.vision_original)
        vision_layout.addWidget(self.vision)
        save_row = QHBoxLayout()
        self.vision_status = QLabel("prompts/vision.md · Saved")
        self.vision_status.setStyleSheet("color: #6e7e8b; font-size: 9pt;")
        save_row.addWidget(self.vision_status, 1)
        self.save_vision_button = QPushButton("Save Vision")
        self.save_vision_button.clicked.connect(self.save_vision)
        save_row.addWidget(self.save_vision_button)
        vision_layout.addLayout(save_row)
        self.vision.textChanged.connect(self.vision_changed)
        top.addWidget(vision_group, 3)
        control_group = QGroupBox("Cycle controls")
        control_group.setMinimumWidth(410)
        controls = QGridLayout(control_group)
        self.single_button = QPushButton("Process 1 Cycle")
        self.single_button.setObjectName("primary")
        self.single_button.clicked.connect(lambda: self.start("single"))
        controls.addWidget(self.single_button, 0, 0, 1, 2)
        self.count = QSpinBox()
        self.count.setObjectName("cycleCount")
        self.count.setRange(1, 1000000)
        self.count.setValue(1)
        self.count.setAccessibleName("Number of cycles")
        self.count.setToolTip("Enter a positive whole number of complete traversals")
        controls.addWidget(self.count, 1, 0)
        self.finite_button = QPushButton("Process X Cycles")
        self.finite_button.clicked.connect(lambda: self.start("finite"))
        controls.addWidget(self.finite_button, 1, 1)
        self.continuous_button = QPushButton("Process Until Stopped")
        self.continuous_button.clicked.connect(lambda: self.start("continuous"))
        controls.addWidget(self.continuous_button, 2, 0, 1, 2)
        top.addWidget(control_group, 2)
        layout.addLayout(top)
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: 600; font-size: 11pt;")
        layout.addWidget(self.status_label)
        progress_row = QHBoxLayout()
        self.progress_text = QLabel("No active run · 0 cycles completed")
        progress_row.addWidget(self.progress_text, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(350)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_row.addWidget(self.progress, 1)
        layout.addLayout(progress_row)
        self.graph = WorkflowGraph(controller.settings.agents, self.open_prompt)
        layout.addWidget(self.graph, 1)
        foot = QHBoxLayout()
        legend = QLabel(
            "Gray arrows: waiting for delivery  ·  Colored arrows: delivered input\nDouble-click a role to edit its prompt · Ctrl+1…4 also opens the editors"
        )
        legend.setStyleSheet("color: #687d8b; font-size: 9pt;")
        foot.addWidget(legend, 1)
        self.output_role = QComboBox()
        for role, (name, _) in ROLES.items():
            self.output_role.addItem(name, role)
        foot.addWidget(self.output_role)
        output_button = QPushButton("Open Current Output")
        output_button.clicked.connect(self.open_output)
        foot.addWidget(output_button)
        details_button = QPushButton("Details")
        details_button.clicked.connect(self.open_details)
        foot.addWidget(details_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        foot.addWidget(self.stop_button)
        layout.addLayout(foot)
        for index, role in enumerate(ROLES, 1):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index}"))
            action.triggered.connect(lambda checked=False, role=role: self.open_prompt(role))
            self.addAction(action)
        save_action = QAction(self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_vision)
        self.addAction(save_action)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.check_idle()

    @property
    def vision_dirty(self):
        return self.vision.toPlainText() != self.vision_original

    def vision_changed(self):
        self.vision_status.setText(
            "prompts/vision.md · "
            + ("Unsaved changes" if self.vision_dirty else "Saved")
            + (" · saved changes apply next cycle" if self.controller.active else "")
        )
        self.update_controls()

    def save_vision(self):
        text = self.vision.toPlainText()
        try:
            try:
                sha = self.controller.prompts.save("vision", text, self.vision_hash)
            except ConflictError:
                answer = QMessageBox.question(
                    self,
                    "Vision changed on disk",
                    "Overwrite the external edit with your current draft? Cancel preserves your draft.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                sha = self.controller.prompts.save("vision", text, self.vision_hash, overwrite=True)
            self.vision_original, self.vision_hash = text, sha
            self.vision_changed()
            if not self.controller.active:
                self.check_idle()
        except (WorkbenchError, OSError) as exc:
            QMessageBox.warning(
                self, "Vision was not saved", self.controller.redactor.text(str(exc))
            )

    def open_prompt(self, role):
        existing = self.dialogs.get(role)
        if existing and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        try:
            dialog = PromptDialog(
                self.controller.prompts, role, lambda: self.controller.active, self
            )
            dialog.saved.connect(self.check_idle)
            self.dialogs[role] = dialog
            dialog.show()
        except WorkbenchError as exc:
            QMessageBox.warning(self, "Cannot open prompt", self.controller.redactor.text(str(exc)))

    def start(self, mode):
        if self.controller.active or (self.future and not self.future.done()):
            return
        if self.vision_dirty:
            self.status_label.setText("Save the vision before starting.")
            return
        reason = self.controller.validate_start()
        if reason:
            self.status_label.setText(reason)
            return
        self._stopping, self._run_id, self._cycle_id = False, "", ""
        self._requested = (
            1 if mode == "single" else self.count.value() if mode == "finite" else None
        )
        self.future = self.background.submit(self.controller.run(mode, self.count.value()))
        self.status_label.setText("Starting… checking configuration and execution environment")
        self.update_controls()

    def stop(self):
        if self.controller.status == "cleanup_blocked":
            self.future = self.background.submit(self.controller.retry_cleanup())
            self.status_label.setText("Retrying cancellation cleanup…")
            self.update_controls()
            return
        self._stopping = True
        self.controller.request_stop()
        if self.future:
            self.future.cancel()  # Also covers Stop before the background coroutine starts.
        self.graph.stop_visuals()
        self.status_label.setText(
            "Stopping — new work and artifact acceptance revoked; cleaning up"
        )
        self.stop_button.setEnabled(False)

    def update_controls(self):
        busy = self.controller.active or bool(self.future and not self.future.done())
        enabled = not busy and not self.vision_dirty and not self._validation_error
        for button in (self.single_button, self.finite_button, self.continuous_button):
            button.setEnabled(enabled)
        self.count.setEnabled(not busy)
        self.stop_button.setText(
            "Retry Cleanup" if self.controller.status == "cleanup_blocked" else "Stop"
        )
        self.stop_button.setEnabled(
            busy and (not self._stopping or self.controller.status == "cleanup_blocked")
        )

    def check_idle(self):
        if self.controller.active or (self.future and not self.future.done()):
            return
        self._validation_error = self.controller.validate_start()
        if self._validation_error:
            self.status_label.setText(self._validation_error)
        elif self.vision_dirty:
            self.status_label.setText("Save the vision before starting.")
        elif self.controller.status == "idle":
            self.status_label.setText(
                "Ready · "
                + (
                    "mock model uses a fixed Hello World fixture"
                    if self.controller.mock
                    else "live requests use your configured endpoint"
                )
            )
        self.update_controls()

    def tick(self):
        for event in self.controller.bus.drain():
            self.handle_event(event)
        if self.future and self.future.done():
            try:
                result = self.future.result()
                # A disk failure can prevent even run.started from reaching the event log.
                # The completed future remains a second path to an actionable terminal status.
                if isinstance(result, dict) and result.get("error"):
                    self.status_label.setText(self.controller.redactor.text(result["error"])[:1500])
                    self.graph.terminate_waiting(result.get("status", "failed"))
            except CancelledError:
                if not self.controller.active:
                    self.status_label.setText("Cancelled before processing started")
            except Exception as exc:
                self.status_label.setText(self.controller.redactor.text(str(exc))[:1500])
            self.future = None
            self.check_idle()
        self._last_vision_check += 1
        if self._last_vision_check >= 10:
            self._last_vision_check = 0
            try:
                text, sha = self.controller.prompts.load("vision")
                if sha != self.vision_hash:
                    if self.vision_dirty:
                        self.vision_status.setText(
                            "External edit detected · your draft is preserved"
                        )
                    else:
                        self.vision_original, self.vision_hash = text, sha
                        self.vision.setPlainText(text)
            except WorkbenchError:
                self.vision_status.setText("Vision file unavailable · draft preserved")
            self.check_idle()
        self.update_controls()
        if self._pending_close and not self.controller.active and not self.future:
            self._pending_close = False
            self.close()

    def handle_event(self, event):
        if event.type == "run.started":
            self._run_id = event.run_id
            self._requested = event.payload.get("requested")
        if event.run_id != self._run_id:
            return
        if event.type == "cycle.started":
            if self._stopping:
                return
            self._cycle_id = event.cycle_id
            ordinal = event.payload["ordinal"]
            self.graph.begin_cycle(ordinal)
            self.status_label.setText(f"Running · Cycle {ordinal}")
            self.set_progress(event.payload["completed"], ordinal)
        if event.cycle_id and event.cycle_id != self._cycle_id and event.type != "run.terminated":
            return
        if self._stopping and event.type not in {
            "run.terminated",
            "cycle.terminated",
            "invocation.cancelled",
        }:
            return
        if event.type == "invocation.started":
            self.graph.set_status(
                event.agent_id, "running", event.payload.get("activity", "Processing")
            )
        elif event.type == "preview":
            self.graph.append_preview(event.agent_id, event.payload.get("text", ""))
        elif event.type == "invocation.completed":
            if event.agent_id == "tester" and event.invocation_id.endswith("-prepare_tests"):
                self.graph.set_status("tester", "waiting", "Tests prepared · awaiting product")
            else:
                self.graph.set_status(event.agent_id, "completed", "Accepted output")
        elif event.type in {"invocation.failed", "invocation.cancelled"}:
            # Cleanup notifications must not erase a more specific terminal result.
            if event.type == "invocation.failed" or self.graph.nodes[event.agent_id].status not in {
                "failed",
                "completed",
                "blocked",
            }:
                self.graph.set_status(event.agent_id, event.status, event.status.capitalize())
            if event.payload.get("error"):
                self.status_label.setText(event.payload["error"])
        elif event.type == "handoff.delivered":
            self.graph.edges[event.payload["edge_id"]].delivered(self.graph.accent)
        elif event.type == "cycle.completed":
            self.set_progress(event.payload["completed"], self.graph.cycle)
            self.status_label.setText(
                "Cycle completed · product outcome: " + event.payload["test_outcome"]
            )
        elif event.type == "cycle.terminated":
            self.graph.terminate_waiting(event.status)
        elif event.type == "run.terminated":
            if event.status != "completed":
                self.graph.terminate_waiting(event.status)
            self.status_label.setText(
                event.status.replace("_", " ").capitalize()
                + (" · " + event.payload["error"] if event.payload.get("error") else "")
            )
            self.set_progress(event.payload["completed"], self.graph.cycle)
            self._stopping = event.status == "cleanup_blocked"

    def set_progress(self, completed, ordinal):
        total = f" of {self._requested}" if self._requested is not None else " · continuous"
        self.progress_text.setText(f"Cycle {ordinal} · {completed}{total} traversals completed")
        if self._requested is None and self.controller.active:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, self._requested or max(completed, 1))
            self.progress.setValue(completed)

    def open_output(self):
        role = self.output_role.currentData()
        try:
            reference = self.controller.store.current(role)
            if not reference:
                QMessageBox.information(
                    self,
                    "No accepted output",
                    "This role has not published an accepted revision yet.",
                )
                return
            self.controller.store.manifest(reference)
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.controller.store.revision_path(reference)))
            )
        except WorkbenchError as exc:
            QMessageBox.warning(self, "Cannot open output", str(exc))

    def open_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sanitized activity and artifact references")
        dialog.resize(950, 650)
        layout = QVBoxLayout(dialog)
        label = QLabel(
            "Recent lifecycle events. Full test evidence is stored in the Tester's accepted revision."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setMaximumBlockCount(5000)
        layout.addWidget(text)

        def refresh():
            events = self.controller.records.recent_events(300, self._run_id or None)
            text.setPlainText(
                "\n".join(json.dumps(event.model_dump(), ensure_ascii=False) for event in events)
            )

        button = QPushButton("Refresh")
        button.clicked.connect(refresh)
        layout.addWidget(button)
        refresh()
        dialog.show()

    def closeEvent(self, event):
        if self._allow_close:
            event.accept()
            return
        if self.controller.active or (self.future and not self.future.done()):
            if self.controller.status == "cleanup_blocked":
                QMessageBox.warning(
                    self,
                    "Cleanup is blocked",
                    "Restore Docker and use Retry Cleanup before closing safely.",
                )
                event.ignore()
                return
            self._pending_close = True
            self.stop()
            event.ignore()
            return
        if self.vision_dirty:
            result = QMessageBox.question(
                self,
                "Unsaved vision",
                "Save the vision before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if result == QMessageBox.StandardButton.Save:
                self.save_vision()
                if self.vision_dirty:
                    event.ignore()
                    return
        for dialog in self.dialogs.values():
            if dialog.isVisible() and not dialog.may_close():
                event.ignore()
                return
        self._allow_close = True
        event.accept()
