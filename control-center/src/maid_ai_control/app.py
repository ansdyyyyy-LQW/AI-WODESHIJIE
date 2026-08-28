from __future__ import annotations

import sys

from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QStackedWidget, QStatusBar, QWizard, QWidget,
)

from maid_ai_control.api_client import ApiClient
from maid_ai_control.config import ConfigManager
from maid_ai_control.pages import ControlPage, MaidAiPage, SettingsPage, TokenRndPage
from maid_ai_control.process_supervisor import ProcessSupervisor
from maid_ai_control.user_text import public_model_text


APP_STYLE = """
QWidget { font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; }
QMainWindow, QWidget#root { background: #f4f6f9; }
QListWidget#navigation { background: #202733; color: #eef2f7; border: 0; padding: 10px 7px; }
QListWidget#navigation::item { min-height: 42px; padding: 9px 12px; margin: 3px 0; border-radius: 7px; }
QListWidget#navigation::item:selected { background: #2563eb; color: white; }
QPushButton { min-height: 30px; padding: 4px 13px; border: 1px solid #c9d1dc; border-radius: 6px; background: white; }
QPushButton:hover { border-color: #2563eb; }
QPushButton:disabled { color: #9aa4b2; background: #f4f6f9; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QTableWidget, QTreeWidget, QListWidget {
  border: 1px solid #cbd3df; border-radius: 6px; padding: 5px; background: white;
}
QGroupBox, QFrame#card { border: 1px solid #d7dde7; border-radius: 9px; margin-top: 10px; padding: 12px; background: white; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QHeaderView::section { background: #eef2f7; padding: 7px; border: 0; border-right: 1px solid #d7dde7; }
QLabel#pageTitle { font-size: 25px; font-weight: 650; }
QLabel#pageSubtitle, QLabel#pageDescription { color: #596579; }
QLabel#cardTitle { color: #596579; }
QLabel#cardValue { font-size: 18px; font-weight: 600; }
QStatusBar { background: white; border-top: 1px solid #d7dde7; }
"""


EVENT_TEXT = {
    "RUNTIME_STATUS": "AI 状态已更新",
    "GOAL_STATUS": "目标有了新进展",
    "PLAN_STATUS": "任务计划有了新进展",
    "CURRENT_ACTION": "女仆开始了新的操作",
    "BRIDGE_STATUS": "Minecraft 连接状态已更新",
    "RND_STATUS": "AI 研发有了新进展",
    "RND_CYCLE_CREATED": "新的研发周期已经开始",
    "BRIDGE_EVENT": "Minecraft 世界发生了变化",
    "THREAT_CHANGED": "附近危险情况发生了变化",
    "DECISION": "AI 作出了新的决定",
}

VISIBLE_REPLACEMENTS = (
    ("R&D Harness", "本地研发环境"), ("Source Workspace", "研发源码目录"),
    ("Start Gate", "启动条件"), ("Postcondition", "结果确认"),
    ("Checkpoint", "进度保存点"), ("Token Ledger", "用量记录"),
    ("ThreatAnalytics", "危险分析"), ("Memory Context", "记忆信息"),
    ("PlanStep", "任务步骤"), ("Bridge", "游戏连接"), ("Runtime", "日常 AI"),
    ("Goal", "目标"), ("Plan", "任务计划"), ("Action", "操作"),
    ("Runner", "本地研发程序"), ("Worktree", "隔离源码目录"),
    ("Candidate", "待确认成果"), ("Patch", "修改内容"), ("UUID", "编号"),
    ("JSON", "结构化数据"), ("Protocol", "连接规则"), ("Session", "连接会话"),
)


def visible_text(value: object) -> str:
    return public_model_text(value)


class MainWindow(QMainWindow):
    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.supervisor = ProcessSupervisor(config)
        self.api = ApiClient(config.control_url, str(config.data.get("control_token", "")), self)
        self._players_requested = False
        self._last_status: dict = {}
        self.setWindowTitle("MaidAI")
        self.setMinimumSize(1180, 760)
        self.resize(1420, 900)
        self._build_ui()
        self._wire()
        self._start_runtime()

    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.navigation = QListWidget(objectName="navigation")
        self.navigation.setFixedWidth(205)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.pages: list[tuple[str, QWidget]] = [
            ("控制", ControlPage(self.api, self.config)),
            ("女仆AI", MaidAiPage(self.api)),
            ("Token 与 AI研发", TokenRndPage(self.api, self.config)),
            ("设置", SettingsPage(self.config, self.api)),
        ]
        for title, page in self.pages:
            self.navigation.addItem(QListWidgetItem(title))
            self.stack.addWidget(page)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.navigation.setCurrentRow(0)
        status = QStatusBar()
        self.setStatusBar(status)
        self.connection_label = QLabel("AI 服务：启动中")
        self.maid_label = QLabel("女仆：未选择")
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(110)
        self.busy.hide()
        status.addPermanentWidget(self.busy)
        status.addPermanentWidget(self.connection_label)
        status.addPermanentWidget(self.maid_label)
        restart = QAction("重启 AI 服务", self)
        restart.triggered.connect(self._restart_runtime)
        self.menuBar().addMenu("运行").addAction(restart)

    @property
    def control_page(self) -> ControlPage:
        return self.pages[0][1]  # type: ignore[return-value]

    @property
    def maid_ai_page(self) -> MaidAiPage:
        return self.pages[1][1]  # type: ignore[return-value]

    @property
    def token_rnd_page(self) -> TokenRndPage:
        return self.pages[2][1]  # type: ignore[return-value]

    @property
    def settings_page(self) -> SettingsPage:
        return self.pages[3][1]  # type: ignore[return-value]

    def _wire(self) -> None:
        self.navigation.currentRowChanged.connect(self._page_changed)
        self.api.connectedChanged.connect(self._connection_changed)
        self.api.result.connect(self._route_result)
        self.api.failed.connect(self._command_failed)
        self.api.event.connect(self._route_event)
        self.settings_page.restartRequested.connect(self._restart_runtime)
        self.poll = QTimer(self)
        self.poll.setInterval(1500)
        self.poll.timeout.connect(lambda: self.api.command("GET_STATUS"))
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(7000)
        self.heartbeat.timeout.connect(self._refresh_secondary)

    def _start_runtime(self) -> None:
        self.busy.show()
        try:
            self.supervisor.start_agent()
        except Exception as exc:
            self.busy.hide()
            self.connection_label.setText("AI 服务：启动失败")
            QMessageBox.critical(self, "AI 服务启动失败", str(exc))
            return
        self.api.update_endpoint(self.config.control_url, str(self.config.data.get("control_token", "")))
        self.api.connect()

    def _restart_runtime(self) -> None:
        self.busy.show()
        self.connection_label.setText("AI 服务：正在重启")
        self.poll.stop()
        self.heartbeat.stop()
        self.api.close()
        try:
            self.supervisor.restart_agent()
        except Exception as exc:
            self.busy.hide()
            self.connection_label.setText("AI 服务：重启失败")
            QMessageBox.critical(self, "重启失败", str(exc))
            return
        self.api = self._replace_api_client()
        self.api.connect()

    def _replace_api_client(self) -> ApiClient:
        old = self.api
        client = ApiClient(self.config.control_url, str(self.config.data.get("control_token", "")), self)
        self.api = client
        for _title, page in self.pages:
            if hasattr(page, "api"):
                setattr(page, "api", client)
        client.connectedChanged.connect(self._connection_changed)
        client.result.connect(self._route_result)
        client.failed.connect(self._command_failed)
        client.event.connect(self._route_event)
        old.deleteLater()
        return client

    def _connection_changed(self, connected: bool) -> None:
        self.busy.hide()
        self.connection_label.setText("AI 服务：已连接" if connected else "AI 服务：未连接")
        self.control_page.set_control_connected(connected)
        if connected:
            self.poll.start()
            self.heartbeat.start()
            self.api.command("GET_STATUS")
        else:
            self.poll.stop()

    def _page_changed(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        signal = getattr(self.stack.widget(index), "refreshRequested", None)
        if signal is not None:
            signal.emit()

    def _refresh_secondary(self) -> None:
        if not self.api.connected:
            return
        for command in ("GET_MEMORY", "GET_THREAT", "GET_TOKENS", "GET_RND"):
            self.api.command(command)

    def _route_result(self, command: str, data: dict) -> None:
        command = command.upper()
        if command in {"GET_STATUS", "START", "PAUSE", "RESUME", "STOP"}:
            self._update_status(data)
        elif command == "LIST_PLAYERS":
            self.control_page.set_players(data)
        elif command == "SELECT_OWNER":
            self.control_page.owner_selected(data)
        elif command == "DISCOVER_MAIDS":
            self.control_page.set_maids(data)
        elif command in {"BIND_MAID", "UNBIND_MAID"}:
            self.api.command("GET_STATUS")
            self.api.command("DISCOVER_MAIDS")
        elif command == "GET_MEMORY":
            self.maid_ai_page.update_memory(data)
        elif command == "GET_THREAT":
            self.maid_ai_page.update_threat(data)
        elif command == "GET_BUILDING":
            self.maid_ai_page.update_building(data)
        elif command == "GET_SKILLS":
            self.maid_ai_page.update_skills(data)
        elif command == "GET_TOKENS":
            self.token_rnd_page.update_tokens(data)
        elif command == "GET_RND":
            self.token_rnd_page.update_rnd(data)
        elif command == "RESEARCH_MODS":
            self.token_rnd_page.update_research(data)
        elif command == "MARK_RND_HANDLED":
            self.api.command("GET_RND")
            self.api.command("GET_TOKENS")
        elif command == "GET_DIAGNOSTICS":
            self.settings_page.update_diagnostics(data)
        elif command == "EXPORT_DIAGNOSTICS":
            self.statusBar().showMessage(f"诊断包已保存到：{data.get('path', '')}", 12000)
        else:
            self.statusBar().showMessage("操作已完成", 4000)

    def _update_status(self, data: dict) -> None:
        self._last_status = data
        self.control_page.update_status(data)
        self.maid_ai_page.update_status(data)
        self.token_rnd_page.update_status(data)
        bound = bool(data.get("bound_maid_uuid"))
        maid_name = str((data.get("snapshot") or {}).get("maid_name") or "")
        self.maid_label.setText(f"女仆：{maid_name}" if bound and maid_name else ("女仆：已选择" if bound else "女仆：未选择"))
        if data.get("bridge_connected") and self.config.data.get("minecraft_restart_required"):
            self.config.data["minecraft_restart_required"] = False
            self.config.save()
        if data.get("bridge_connected") and not self._players_requested:
            self._players_requested = True
            self.api.command("LIST_PLAYERS")
        elif not data.get("bridge_connected"):
            self._players_requested = False

    def _route_event(self, event: str, payload: dict) -> None:
        if event in {"RUNTIME_STATUS", "GOAL_STATUS", "PLAN_STATUS", "CURRENT_ACTION", "BRIDGE_STATUS", "DECISION"}:
            self.api.command("GET_STATUS")
        elif event in {"RND_STATUS", "RND_CYCLE_CREATED"}:
            self.api.command("GET_RND")
        elif event in {"BRIDGE_EVENT", "THREAT_CHANGED"}:
            self.api.command("GET_THREAT")
        detail = visible_text(payload.get("summary") or payload.get("decision_summary") or "")
        self.statusBar().showMessage(EVENT_TEXT.get(event, "状态已经更新") + (f"：{detail}" if detail else ""), 3500)

    def _command_failed(self, command: str, code: str, message: str) -> None:
        if code == "NOT_CONNECTED" and command in {"GET_STATUS", "GET_MEMORY", "GET_THREAT", "GET_TOKENS", "GET_RND"}:
            return
        self.busy.hide()
        friendly = visible_text(message) or "操作没有成功，请稍后再试。"
        self.statusBar().showMessage(f"操作失败：{friendly}", 10000)
        QMessageBox.warning(self, "操作失败", friendly)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        running = bool(self._last_status.get("desired_running")) or str(self._last_status.get("mode")) in {"RUNNING", "SAFE_IDLE", "WAITING_SNAPSHOT"}
        if running:
            choice = QMessageBox.question(self, "停止 AI 并退出", "AI 当前仍在运行。\n\n是否停止 AI 并退出？", QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if choice != QMessageBox.Yes:
                event.ignore()
                return
            completed = QEventLoop(self)
            timeout = QTimer(self)
            timeout.setSingleShot(True)
            timeout.timeout.connect(completed.quit)
            self.api.result.connect(lambda command, _data: completed.quit() if command == "STOP" else None)
            self.api.command("STOP")
            # R&D cancellation may need a few seconds to stop its child process,
            # persist Token/phase/workspace state, and release both file locks.
            timeout.start(8000)
            completed.exec()
        self.poll.stop()
        self.heartbeat.stop()
        self.api.close()
        try:
            self.supervisor.stop_agent()
        except Exception:
            pass
        event.accept()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MaidAI")
    app.setOrganizationName("MaidAI")
    app.setStyleSheet(APP_STYLE)
    config = ConfigManager()
    if not config.setup_complete:
        from maid_ai_control.wizard import SetupWizard
        wizard = SetupWizard(config)
        if wizard.exec() != QWizard.Accepted:
            return 0
    window = MainWindow(config)
    window.show()
    return app.exec()
