import time

import pytest
from four_agent_workbench.domain import Event
from four_agent_workbench.models.mock import MockModel
from four_agent_workbench.orchestration.controller import BackgroundLoop
from four_agent_workbench.ui.window import MainWindow
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


@pytest.fixture
def window(controller, qtbot):
    background = BackgroundLoop()
    view = MainWindow(controller, background)
    qtbot.addWidget(view)
    view.show()
    yield view
    controller.request_stop()
    qtbot.waitUntil(lambda: not controller.active, timeout=5000)
    view.timer.stop()
    view._allow_close = True
    for dialog in view.dialogs.values():
        dialog._allow_close = True
        dialog.close()
    view.close()
    background.close()


def test_four_nodes_six_edges_and_correct_editors(window, qtbot):
    assert len(window.graph.nodes) == 4
    assert len(window.graph.edges) == 6
    assert window.vision.toPlainText() == window.controller.prompts.load("vision")[0]
    assert window.count.value() == 1
    for role, node in window.graph.nodes.items():
        position = window.graph.mapFromScene(node.sceneBoundingRect().center())
        qtbot.mouseClick(window.graph.viewport(), Qt.MouseButton.LeftButton, pos=position)
        qtbot.mouseDClick(window.graph.viewport(), Qt.MouseButton.LeftButton, pos=position)
        qtbot.waitUntil(lambda: role in window.dialogs)
        dialog = window.dialogs[role]
        assert dialog.editor.toPlainText() == window.controller.prompts.load(role)[0]
        assert window.controller.prompts.filename(role).endswith(f"{role}.md")
        dialog.close()
        qtbot.wait(10)


def test_save_cancel_and_keyboard_access(window, qtbot, monkeypatch):
    qtbot.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: "tester" in window.dialogs)
    dialog = window.dialogs["tester"]
    original = dialog.editor.toPlainText()
    dialog.editor.setPlainText(original + "\nA saved testing convention.\n")
    dialog.save()
    assert window.controller.prompts.load("tester")[0].endswith("A saved testing convention.\n")
    saved = window.controller.prompts.load("tester")[0]
    dialog.editor.appendPlainText("This draft is discarded.")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    dialog.reject()
    assert window.controller.prompts.load("tester")[0] == saved


def test_controls_cycles_and_transient_previews(window, qtbot):
    window.controller.model_factory = lambda _: MockModel(delay=0.003)
    qtbot.mouseClick(window.single_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.controller.status == "running", timeout=3000)
    assert not window.single_button.isEnabled()
    assert not window.finite_button.isEnabled()
    assert not window.continuous_button.isEnabled()
    assert not window.count.isEnabled()
    assert window.stop_button.isEnabled()
    qtbot.waitUntil(
        lambda: any(proxy.isVisible() for _, proxy in window.graph.previews.values()), timeout=3000
    )
    qtbot.waitUntil(
        lambda: window.controller.status == "completed" and window.future is None, timeout=10000
    )
    assert not any(proxy.isVisible() for _, proxy in window.graph.previews.values())
    assert window.graph.edges["tester->marketing"].count == 2
    assert all(edge.count for edge in window.graph.edges.values())
    assert window.single_button.isEnabled()
    assert "1 of 1" in window.progress_text.text()


def test_stop_is_prompt_and_late_events_do_not_reactivate(window, qtbot):
    window.controller.model_factory = lambda _: MockModel(scenario="slow")
    window.start("continuous")
    qtbot.waitUntil(
        lambda: any(node.status == "running" for node in window.graph.nodes.values()), timeout=3000
    )
    previous_run, previous_cycle = window._run_id, window._cycle_id
    before = time.monotonic()
    window.stop()
    assert time.monotonic() - before < 0.1
    assert not any(proxy.isVisible() for _, proxy in window.graph.previews.values())
    qtbot.waitUntil(lambda: not window.controller.active and window.future is None, timeout=3000)
    assert window.controller.completed == 0
    window._run_id, window._cycle_id = "next-run", "next-cycle"
    window.handle_event(
        Event(
            type="invocation.started",
            run_id=previous_run,
            cycle_id=previous_cycle,
            agent_id="system_engineer",
            invocation_id="old",
        )
    )
    assert window.graph.nodes["system_engineer"].status == "cancelled"


def test_preview_is_bounded_and_waiting_input_is_visible(window):
    window._run_id, window._cycle_id = "run", "cycle"
    window.graph.begin_cycle(1)
    window.graph.set_status("system_engineer", "running", "define_requirements")
    window.graph.append_preview("system_engineer", "x" * 100000)
    assert len(window.graph.preview_text["system_engineer"]) == 8192
    window.handle_event(
        Event(
            type="handoff.delivered",
            run_id="run",
            cycle_id="cycle",
            payload={"edge_id": "system_engineer->marketing", "kind": "requirements"},
        )
    )
    assert window.graph.edges["system_engineer->marketing"].count == 1
    assert window.graph.nodes["marketing"].status == "waiting"
    assert not window.graph.previews["marketing"][1].isVisible()


def test_disk_failure_shows_terminal_error_and_releases_controls(window, qtbot, monkeypatch):
    def fail_write(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(window.controller.records, "put", fail_write)
    window.start("single")
    qtbot.waitUntil(lambda: window.future is None and not window.controller.active, timeout=3000)
    assert window.controller.status == "failed"
    assert "disk space" in window.status_label.text()
    assert window.single_button.isEnabled()
