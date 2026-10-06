from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..domain import ConflictError, WorkbenchError


class PromptDialog(QDialog):
    saved = Signal()

    def __init__(self, store, role, active, parent=None):
        super().__init__(parent)
        self.store, self.role, self.active = store, role, active
        self.original, self.file_hash = store.load(role)
        self._allow_close = False
        agent = store.settings.agents[role]
        self.setWindowTitle(f"{agent.display_name} — role prompt")
        self.resize(850, 660)
        layout = QVBoxLayout(self)
        label = QLabel(f"<b>{agent.display_name}</b> · {store.filename(role)}")
        layout.addWidget(label)
        self.status = QLabel("Saved prompt")
        layout.addWidget(self.status)
        self.editor = QPlainTextEdit(self.original)
        self.editor.setFont(QFont("Monospace", 10))
        self.editor.setObjectName("promptEditor")
        layout.addWidget(self.editor)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.editor.textChanged.connect(self.update_status)
        self.update_status()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_external)
        self.timer.start(1000)

    @property
    def dirty(self):
        return self.editor.toPlainText() != self.original

    def update_status(self):
        self.status.setText(
            ("Unsaved changes · " if self.dirty else "Saved · ")
            + ("saved edits apply next cycle" if self.active() else "applies on next Start")
        )

    def save(self):
        text = self.editor.toPlainText()
        try:
            try:
                new_hash = self.store.save(self.role, text, self.file_hash)
            except ConflictError:
                box = QMessageBox(self)
                box.setWindowTitle("Prompt changed on disk")
                box.setText(
                    "Your draft is intact. Reload the file or explicitly overwrite the external edit."
                )
                reload_button = box.addButton("Reload file", QMessageBox.ButtonRole.DestructiveRole)
                overwrite_button = box.addButton(
                    "Overwrite with draft", QMessageBox.ButtonRole.AcceptRole
                )
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if box.clickedButton() == reload_button:
                    self.original, self.file_hash = self.store.load(self.role)
                    self.editor.setPlainText(self.original)
                    self.update_status()
                    return
                if box.clickedButton() != overwrite_button:
                    return
                new_hash = self.store.save(self.role, text, self.file_hash, overwrite=True)
            self.original, self.file_hash = text, new_hash
            self.update_status()
            self.saved.emit()
        except (WorkbenchError, OSError) as exc:
            self.status.setText("Save failed; your draft is preserved.")
            QMessageBox.warning(self, "Cannot save prompt", str(exc))

    def check_external(self):
        try:
            text, actual = self.store.load(self.role)
            if actual != self.file_hash:
                if self.dirty:
                    self.status.setText(
                        "External edit detected. Saving will require a conflict choice; your draft is preserved."
                    )
                else:
                    self.original, self.file_hash = text, actual
                    self.editor.setPlainText(text)
                    self.update_status()
        except WorkbenchError:
            self.status.setText("File unavailable; your draft is preserved.")

    def may_close(self):
        if self._allow_close or not self.dirty:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved prompt",
            "Discard this unsaved draft?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        self._allow_close = result == QMessageBox.StandardButton.Discard
        return self._allow_close

    def reject(self):
        if self.may_close():
            super().reject()

    def closeEvent(self, event):
        event.accept() if self.may_close() else event.ignore()
