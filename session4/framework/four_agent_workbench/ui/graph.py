from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QPlainTextEdit,
)

from ..domain import EDGES, ROLES

STATE_COLORS = {
    "idle": "#88949f",
    "waiting": "#88949f",
    "running": "#147a73",
    "completed": "#217248",
    "failed": "#b84141",
    "blocked": "#967033",
    "cancelled": "#967033",
}
ACTIVITIES = {
    "define_requirements": "Defining requirements",
    "develop": "Developing software",
    "prepare_tests": "Preparing tests",
    "execute_tests": "Executing tests",
    "final_synthesis": "Writing feature brief",
}


class AgentNode(QGraphicsObject):
    def __init__(self, role, title, open_editor):
        super().__init__()
        self.role, self.title, self.open_editor = role, title, open_editor
        self.status, self.activity = "idle", "Double-click to edit prompt"
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{title}: double-click or press Enter/F2 to edit the role prompt")

    def boundingRect(self):
        return QRectF(-5, -5, 230, 108)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(STATE_COLORS[self.status])
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(
            QPen(
                color if self.status == "running" or self.hasFocus() else QColor("#d9e1e6"),
                3 if self.status == "running" or self.hasFocus() else 1.3,
            )
        )
        painter.drawRoundedRect(QRectF(0, 0, 220, 98), 12, 12)
        painter.setPen(QColor("#1d303e"))
        painter.setFont(QFont("Sans Serif", 12, QFont.Weight.DemiBold))
        painter.drawText(QRectF(16, 13, 192, 26), Qt.AlignmentFlag.AlignLeft, self.title)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(20, 54), 4, 4)
        painter.setPen(color)
        painter.setFont(QFont("Sans Serif", 10, QFont.Weight.Medium))
        painter.drawText(
            QRectF(32, 43, 174, 22), Qt.AlignmentFlag.AlignLeft, self.status.capitalize()
        )
        painter.setPen(QColor("#697986"))
        painter.setFont(QFont("Sans Serif", 8))
        painter.drawText(QRectF(16, 71, 190, 18), Qt.AlignmentFlag.AlignLeft, self.activity)

    def mouseDoubleClickEvent(self, event):
        self.open_editor(self.role)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self.open_editor(self.role)
            event.accept()
        else:
            super().keyPressEvent(event)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, points, label, label_at):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        super().__init__(path)
        self.count = 0
        self.generation = 0
        self.label_text = label
        self.label = QGraphicsSimpleTextItem(label, self)
        self.label.setFont(QFont("Sans Serif", 8))
        self.label.setPos(*label_at)
        x, y = points[-1]
        px, py = points[-2]
        if y > py:
            polygon = [(x, y), (x - 5, y - 9), (x + 5, y - 9)]
        elif y < py:
            polygon = [(x, y), (x - 5, y + 9), (x + 5, y + 9)]
        else:
            direction = 1 if x > px else -1
            polygon = [(x, y), (x - 9 * direction, y - 5), (x - 9 * direction, y + 5)]
        self.arrow = QGraphicsPolygonItem(QPolygonF([QPointF(*p) for p in polygon]), self)
        self.setZValue(-1)
        self.set_color("#c8d0d8")

    def set_color(self, color):
        self.setPen(QPen(QColor(color), 2.4))
        self.arrow.setBrush(QColor(color))
        self.arrow.setPen(Qt.PenStyle.NoPen)
        self.label.setBrush(QColor("#577082" if self.count else "#87949f"))

    def delivered(self, color):
        self.count += 1
        self.generation += 1
        self.set_color(color)
        self.label.setText(f"{self.label_text}  ✓" + (f" ×{self.count}" if self.count > 1 else ""))
        self.setToolTip(
            f"{self.count} handoff(s) delivered this cycle. Delivery does not necessarily start an invocation."
        )
        generation = self.generation
        self.setPen(QPen(QColor(color), 4))
        QTimer.singleShot(
            300,
            self.scene(),
            lambda: self.set_color(color) if self.generation == generation else None,
        )

    def reset(self):
        self.count = 0
        self.generation += 1
        self.label.setText(self.label_text)
        self.set_color("#c8d0d8")


class WorkflowGraph(QGraphicsView):
    def __init__(self, agents, open_editor, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.setObjectName("workflowGraph")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setSceneRect(0, 0, 1230, 530)
        self.setMinimumHeight(390)
        self.setStyleSheet(
            "QGraphicsView { background: #f6f8fa; border: 1px solid #e0e6eb; border-radius: 12px; }"
        )
        self.nodes, self.previews, self.preview_text = {}, {}, {}
        self.cycle = 0
        self.accent = "#326fa4"
        for index, role in enumerate(ROLES):
            node = AgentNode(role, agents[role].display_name, open_editor)
            node.setPos(45 + index * 300, 190)
            scene.addItem(node)
            self.nodes[role] = node
            preview = QPlainTextEdit()
            preview.setReadOnly(True)
            preview.setMaximumBlockCount(120)
            preview.setFont(QFont("Monospace", 8))
            preview.setStyleSheet(
                "QPlainTextEdit { background: #edf3f6; color: #344d60; border: 1px solid #d7e1e8; border-radius: 6px; padding: 5px; }"
            )
            preview.setFixedSize(220, 155)
            proxy = scene.addWidget(preview)
            proxy.setPos(45 + index * 300, 309)
            proxy.setVisible(False)
            self.previews[role] = (preview, proxy)
            self.preview_text[role] = ""
        definitions = [
            ([(265, 226), (345, 226)], "Requirements", (270, 170)),
            (
                [(180, 190), (180, 120), (755, 120), (755, 190)],
                "Requirements · test preparation",
                (330, 98),
            ),
            (
                [(125, 190), (125, 65), (1055, 65), (1055, 190)],
                "Requirements · feature intent",
                (590, 43),
            ),
            ([(565, 226), (645, 226)], "Product", (580, 206)),
            (
                [(565, 266), (601, 266), (601, 493), (916, 493), (916, 266), (945, 266)],
                "Implementation information",
                (654, 495),
            ),
            ([(865, 226), (945, 226)], "Tests", (880, 206)),
        ]
        self.edges = {}
        for edge, definition in zip(EDGES, definitions, strict=True):
            item = EdgeItem(*definition)
            scene.addItem(item)
            self.edges[f"{edge[0]}->{edge[1]}"] = item

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, AgentNode):
            item.open_editor(item.role)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def begin_cycle(self, ordinal):
        self.cycle = ordinal
        self.accent = "#326fa4" if ordinal % 2 else "#168378"
        for edge in self.edges.values():
            edge.reset()
        for role in self.nodes:
            self.set_status(role, "waiting", "Waiting for prerequisites")

    def set_status(self, role, status, activity=None):
        node = self.nodes[role]
        node.status = status
        if activity is not None:
            node.activity = ACTIVITIES.get(activity, activity)
        if status != "running":
            self.previews[role][1].setVisible(False)
            self.preview_text[role] = ""
        else:
            self.previews[role][0].clear()
            self.preview_text[role] = ""
            self.previews[role][1].setVisible(True)
        node.update()

    def append_preview(self, role, text):
        if role not in self.nodes or self.nodes[role].status != "running":
            return
        editor, proxy = self.previews[role]
        bar = editor.verticalScrollBar()
        at_bottom, previous = bar.value() >= bar.maximum() - 2, bar.value()
        self.preview_text[role] = (self.preview_text[role] + text)[-8192:]
        editor.setPlainText(self.preview_text[role])
        bar.setValue(bar.maximum() if at_bottom else previous)
        proxy.setVisible(True)

    def stop_visuals(self):
        for role, node in self.nodes.items():
            if node.status in {"running", "waiting"}:
                self.set_status(role, "cancelled", "Cancelled")

    def terminate_waiting(self, status):
        if status == "completed":
            return
        for role, node in self.nodes.items():
            if node.status not in {"running", "waiting"}:
                continue
            if status in {"cancelled", "cleanup_blocked"}:
                self.set_status(role, "cancelled", "Cancelled")
            elif node.status == "waiting":
                self.set_status(role, "blocked", "Remaining work stopped")
            else:
                self.set_status(role, "cancelled", "Stopped after run failure")
