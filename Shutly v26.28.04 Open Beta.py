import ctypes
import math
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import (
    QElapsedTimer,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
    QVariantAnimation,
)
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QIntValidator, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Shutly"
APP_ID = "Shutly.Gen1.Stable"
WINDOW_SIZE = (920, 720)
FINAL_MINUTE = 60

ACTIONS = {
    "shutdown": ("Shutdown", "/s"),
    "restart": ("Restart", "/r"),
}

PRESETS = (
    ("30m", 30 * 60),
    ("1h", 60 * 60),
    ("2h", 2 * 60 * 60),
    ("3h", 3 * 60 * 60),
    ("4h", 4 * 60 * 60)
)


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def get_icon() -> QIcon:
    base = resource_dir() / "assets"
    for icon_path in (base / "hyrstarlight.ico", base / "hyrstarlight.png"):
        if icon_path.exists():
            return QIcon(str(icon_path))
    return QIcon()


def set_app_id() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


class AnimatedLineEdit(QLineEdit):
    def __init__(self, placeholder: str, maximum: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setValidator(QIntValidator(0, maximum, self))

        self.highlight = 0.0
        self.last_pulse = QElapsedTimer()
        self.last_pulse.start()

        self.highlight_anim = QVariantAnimation(self)
        self.highlight_anim.setDuration(260)
        self.highlight_anim.setStartValue(0.0)
        self.highlight_anim.setKeyValueAt(0.32, 1.0)
        self.highlight_anim.setEndValue(0.0)
        self.highlight_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.highlight_anim.valueChanged.connect(self.set_highlight)
        self.textEdited.connect(self.pulse)

    def set_highlight(self, value: float) -> None:
        self.highlight = float(value)
        self.update()

    def pulse(self) -> None:
        if self.last_pulse.elapsed() < 55:
            return
        self.last_pulse.restart()
        self.highlight_anim.stop()
        self.highlight_anim.start()

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.pulse()
        super().focusInEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.highlight <= 0.001:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        alpha = int(120 * self.highlight)
        pen = QPen(QColor(112, 170, 255, alpha))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 9, 9)


class CountdownDial(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(260)
        self.total_seconds = 1
        self.remaining_seconds = 0.0
        self.action = "shutdown"
        self.mode = "IDLE"

        self.time_label = QLabel("00:00:00", self)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setObjectName("timeLabel")

        self.action_label = QLabel("Ready", self)
        self.action_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.action_label.setObjectName("dialAction")

    def resizeEvent(self, event) -> None:  # noqa: N802
        width = self.width()
        center_y = self.height() // 2
        self.time_label.setGeometry(0, center_y - 40, width, 56)
        self.action_label.setGeometry(0, center_y + 18, width, 28)
        super().resizeEvent(event)

    def set_state(self, mode: str, action: str, remaining: float, total: int) -> None:
        self.mode = mode
        self.action = action
        self.remaining_seconds = max(0.0, remaining)
        self.total_seconds = max(1, total)
        self.time_label.setText(format_time(math.ceil(self.remaining_seconds)))

        if mode == "RUNNING":
            self.action_label.setText(f"{ACTIONS[action][0]} scheduled")
        elif mode == "PAUSED":
            self.action_label.setText("Paused")
        else:
            self.action_label.setText("Ready")

        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        size = min(self.width(), self.height()) - 18
        left = (self.width() - size) // 2
        top = (self.height() - size) // 2
        rect = QRect(left, top, size, size)

        painter.setPen(QPen(QColor("#2a2a2a"), 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)

        progress = self.remaining_seconds / self.total_seconds if self.mode != "IDLE" else 0
        progress = max(0.0, min(1.0, progress))
        accent = QColor("#ff6b4a") if self.remaining_seconds <= FINAL_MINUTE and self.mode == "RUNNING" else QColor("#6da8ff")
        if self.mode == "PAUSED":
            accent = QColor("#f0b95a")

        painter.setPen(QPen(accent, 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * progress))


class Shutly(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.state = "IDLE"
        self.end_time = 0.0
        self.total_seconds = 0
        self.paused_remaining = 0
        self.action = "shutdown"
        self.drag_pos: QPoint | None = None

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(get_icon())
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setFixedSize(*WINDOW_SIZE)

        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(250)
        self.tick_timer.timeout.connect(self.tick)

        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(lambda: self.status.setText(self.status_base_text()))

        self.build_ui()
        self.build_tray()
        self.apply_style()
        self.update_ui(animated=False)

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(16)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        left_balance = QWidget()
        left_balance.setFixedWidth(76)

        self.title = QLabel(APP_NAME)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setObjectName("title")

        self.min_btn = QPushButton("-")
        self.close_btn = QPushButton("x")
        self.min_btn.setFixedSize(36, 28)
        self.close_btn.setFixedSize(36, 28)
        self.min_btn.clicked.connect(self.hide_to_tray)
        self.close_btn.clicked.connect(self.hide_to_tray)

        window_controls = QHBoxLayout()
        window_controls.setSpacing(8)
        window_controls.addWidget(self.min_btn)
        window_controls.addWidget(self.close_btn)

        title_row.addWidget(left_balance)
        title_row.addWidget(self.title, stretch=1)
        title_row.addLayout(window_controls)

        self.dial = CountdownDial(self)

        self.status = QLabel()
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setObjectName("status")

        self.input_panel = QFrame()
        self.input_panel.setObjectName("inputPanel")
        input_layout = QVBoxLayout(self.input_panel)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(12)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        self.preset_buttons: list[QPushButton] = []
        for label, seconds in PRESETS:
            button = QPushButton(label)
            button.setProperty("role", "chip")
            button.clicked.connect(lambda checked=False, value=seconds: self.apply_preset(value))
            preset_row.addWidget(button)
            self.preset_buttons.append(button)

        time_row = QHBoxLayout()
        time_row.setSpacing(10)
        self.hours = AnimatedLineEdit("Hours", 999, self)
        self.minutes = AnimatedLineEdit("Minutes", 59, self)
        self.seconds = AnimatedLineEdit("Seconds", 59, self)
        time_row.addWidget(self.hours)
        time_row.addWidget(self.minutes)
        time_row.addWidget(self.seconds)

        self.action_group = QButtonGroup(self)
        self.action_group.setExclusive(True)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.action_buttons: dict[str, QPushButton] = {}
        for action_key, (label, _) in ACTIONS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.clicked.connect(lambda checked=False, key=action_key: self.set_action(key))
            self.action_group.addButton(button)
            action_row.addWidget(button)
            self.action_buttons[action_key] = button
        self.action_buttons[self.action].setChecked(True)

        input_layout.addLayout(preset_row)
        input_layout.addLayout(time_row)
        input_layout.addLayout(action_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.start_btn = QPushButton("Start")
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Cancel")
        self.start_btn.setObjectName("primaryButton")

        for button in (self.start_btn, self.pause_btn, self.cancel_btn):
            button.setMinimumWidth(130)
            button.setFixedHeight(42)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.start_btn.clicked.connect(self.start)
        self.pause_btn.clicked.connect(self.pause_resume)
        self.cancel_btn.clicked.connect(self.cancel)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.pause_btn)
        button_row.addWidget(self.cancel_btn)

        for button in (
            self.min_btn,
            self.close_btn,
            self.start_btn,
            self.pause_btn,
            self.cancel_btn,
            *self.preset_buttons,
            *self.action_buttons.values(),
        ):
            self.add_press_feedback(button)

        root.addLayout(title_row)
        root.addWidget(self.dial)
        root.addWidget(self.status)
        root.addWidget(self.input_panel)
        root.addLayout(button_row)

    def build_tray(self) -> None:
        icon = get_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self.handle_tray_activation)

        menu = QMenu()
        menu.addAction(QAction("Show", self, triggered=self.show_from_tray))
        menu.addAction(QAction("Cancel Timer", self, triggered=self.cancel))
        menu.addSeparator()
        menu.addAction(QAction("Exit", self, triggered=self.exit_app))
        self.tray.setContextMenu(menu)
        self.tray.setVisible(True)

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background-color: #171717;
                color: #f7f7f7;
                font-family: Segoe UI;
                font-size: 13px;
            }

            QLabel#title {
                font-size: 28px;
                font-weight: 700;
            }

            QLabel#timeLabel {
                background: transparent;
                font-size: 36px;
                font-weight: 700;
            }

            QLabel#dialAction {
                background: transparent;
                color: #a9a9a9;
            }

            QLabel#status {
                background-color: #222222;
                border: 1px solid #303030;
                border-radius: 8px;
                color: #d8d8d8;
                min-height: 38px;
                padding: 8px 12px;
            }

            QFrame#inputPanel {
                background: transparent;
            }

            QLineEdit {
                background-color: #222222;
                border: 1px solid #333333;
                border-radius: 9px;
                color: #ffffff;
                min-height: 36px;
                padding: 9px;
            }

            QLineEdit:focus {
                border-color: #6da8ff;
            }

            QPushButton {
                background-color: #2a2a2a;
                border: 1px solid #333333;
                border-radius: 8px;
                color: #f2f2f2;
                min-height: 36px;
                padding: 8px 12px;
            }

            QPushButton:hover {
                background-color: #343434;
                border-color: #444444;
            }

            QPushButton:checked {
                background-color: #2f4b70;
                border-color: #6da8ff;
            }

            QPushButton#primaryButton {
                background-color: #315b8f;
                border-color: #6da8ff;
            }

            QPushButton#primaryButton:hover {
                background-color: #3768a5;
            }

            QPushButton#primaryButton:disabled {
                background-color: #202020;
                border-color: #292929;
                color: #777777;
            }

            QPushButton[role="chip"] {
                color: #dcdcdc;
            }

            QPushButton[role="segment"] {
                font-weight: 600;
            }

            QPushButton:disabled {
                background-color: #202020;
                border-color: #292929;
                color: #777777;
            }
            """
        )

    def add_press_feedback(self, button: QPushButton) -> None:
        effect = QGraphicsOpacityEffect(button)
        effect.setOpacity(1.0)
        button.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", button)
        animation.setDuration(100)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        button._press_animation = animation  # type: ignore[attr-defined]

        def animate_to(value: float) -> None:
            animation.stop()
            animation.setStartValue(effect.opacity())
            animation.setEndValue(value)
            animation.start()

        button.pressed.connect(lambda: animate_to(0.86))
        button.released.connect(lambda: animate_to(1.0))

    def set_action(self, action: str) -> None:
        self.action = action
        self.action_buttons[action].setChecked(True)
        self.update_ui(animated=True)

    def apply_preset(self, seconds: int) -> None:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.hours.setText(str(hours) if hours else "")
        self.minutes.setText(str(minutes) if minutes else "")
        self.seconds.setText(str(seconds) if seconds else "")
        self.flash_status(f"{format_time(hours * 3600 + minutes * 60 + seconds)} selected")

    def read_time(self) -> int:
        return (
            int(self.hours.text() or 0) * 3600
            + int(self.minutes.text() or 0) * 60
            + int(self.seconds.text() or 0)
        )

    def start(self) -> None:
        if self.state != "IDLE":
            return

        seconds = self.read_time()
        if seconds <= 0:
            self.flash_status("Enter a time greater than zero")
            return

        self.cancel_windows_schedule()
        if not self.schedule(seconds):
            self.flash_status("Scheduling is available on Windows")
            return

        self.total_seconds = seconds
        self.end_time = time.time() + seconds
        self.paused_remaining = 0
        self.state = "RUNNING"
        self.tick_timer.start()
        self.update_ui(animated=True)
        self.hide_to_tray()

    def pause_resume(self) -> None:
        if self.state == "RUNNING":
            self.paused_remaining = max(1, int(self.end_time - time.time()))
            self.tick_timer.stop()
            self.cancel_windows_schedule()
            self.state = "PAUSED"
        elif self.state == "PAUSED":
            self.end_time = time.time() + self.paused_remaining
            self.schedule(self.paused_remaining)
            self.tick_timer.start()
            self.state = "RUNNING"
        else:
            return

        self.update_ui(animated=True)

    def cancel(self) -> None:
        was_active = self.state in {"RUNNING", "PAUSED"}
        if not was_active:
            return

        self.tick_timer.stop()
        self.cancel_windows_schedule()
        self.state = "IDLE"
        self.end_time = 0.0
        self.total_seconds = 0
        self.paused_remaining = 0
        self.update_ui(animated=True)

        if was_active:
            self.flash_status("Timer cancelled")

    def schedule(self, seconds: int) -> bool:
        if sys.platform != "win32":
            return False

        _, flag = ACTIONS[self.action]
        result = subprocess.run(
            ["shutdown", flag, "/t", str(seconds)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def cancel_windows_schedule(self) -> None:
        if sys.platform == "win32":
            subprocess.run(["shutdown", "/a"], capture_output=True, text=True, check=False)

    def tick(self) -> None:
        remaining = max(0.0, self.end_time - time.time())
        if remaining <= 0:
            self.tick_timer.stop()
            self.state = "IDLE"
            self.update_ui(animated=True)
            self.flash_status("Done")
            return

        self.dial.set_state(self.state, self.action, remaining, self.total_seconds)
        if remaining <= FINAL_MINUTE:
            self.status.setText(f"Final minute: {format_time(math.ceil(remaining))}")

    def update_ui(self, animated: bool) -> None:
        if self.state == "IDLE":
            remaining = 0.0
            total = 1
        elif self.state == "PAUSED":
            remaining = float(self.paused_remaining)
            total = self.total_seconds or self.paused_remaining
        else:
            remaining = max(0.0, self.end_time - time.time())
            total = self.total_seconds or remaining

        self.dial.set_state(self.state, self.action, remaining, total)
        self.status.setText(self.status_base_text())

        is_idle = self.state == "IDLE"
        is_paused = self.state == "PAUSED"
        self.input_panel.setVisible(is_idle)
        self.start_btn.setEnabled(is_idle)
        self.pause_btn.setEnabled(not is_idle)
        self.pause_btn.setText("Resume" if is_paused else "Pause")
        self.cancel_btn.setEnabled(not is_idle)

        for button in (self.start_btn, self.pause_btn, self.cancel_btn):
            effect = button.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                effect.setOpacity(1.0)

        for button in self.action_buttons.values():
            button.setEnabled(is_idle)
        for button in self.preset_buttons:
            button.setEnabled(is_idle)

        if animated:
            self.pulse_widget(self.status)

    def status_base_text(self) -> str:
        if self.state == "RUNNING":
            return f"{ACTIONS[self.action][0]} in {format_time(max(0, int(self.end_time - time.time())))}"
        if self.state == "PAUSED":
            return f"Paused at {format_time(self.paused_remaining)}"
        return "Choose a preset or enter a custom time"

    def pulse_widget(self, widget: QWidget) -> None:
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(180)
        animation.setStartValue(0.72)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        widget._pulse_animation = animation  # type: ignore[attr-defined]
        animation.start()

    def flash_status(self, text: str) -> None:
        self.status.setText(text)
        self.pulse_widget(self.status)
        self.notice_timer.start(1700)

    def hide_to_tray(self) -> None:
        self.hide()

    def show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.pulse_widget(self)

    def handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_from_tray()

    def exit_app(self) -> None:
        self.tick_timer.stop()
        self.tray.hide()
        QApplication.quit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.start()
        elif event.key() == Qt.Key.Key_Escape:
            self.hide_to_tray()
        elif event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.cancel()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide_to_tray()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.drag_pos = None


def format_time(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def main() -> None:
    set_app_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(get_icon())

    window = Shutly()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
