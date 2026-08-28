from __future__ import annotations

import json
from copy import deepcopy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from maid_ai_control.api_probe import run_probe
from maid_ai_control import __version__
from maid_ai_control.minecraft_locator import inspect_minecraft_dir, locate_minecraft
from maid_ai_control.widgets import Page


class ProviderEditor(QGroupBox):
    def __init__(self, title: str, profile_id: str, config, parent=None):
        super().__init__(title, parent)
        self.profile_id = profile_id
        self.config = config
        form = QFormLayout(self)
        self.base = QLineEdit()
        self.base.setPlaceholderText("例如 https://服务地址/v1")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("已经保存时可留空")
        self.model = QLineEdit()
        self.model.setPlaceholderText("必须与服务中的模型名称完全一致")
        self.path = QLineEdit("/chat/completions")
        self.schema = QCheckBox("接口支持结构化结果")
        self.schema.setChecked(True)
        self.test_button = QPushButton("测试接口")
        self.test_button.clicked.connect(self.probe)
        self.test_result = QLabel("尚未测试")
        self.test_result.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.test_button)
        row.addWidget(self.test_result, 1)
        form.addRow("API 地址", self.base)
        form.addRow("API Key", self.key)
        form.addRow("模型名称", self.model)
        form.addRow(row)

    def set_editor_enabled(self, enabled: bool) -> None:
        for widget in (self.base, self.key, self.model, self.test_button):
            widget.setEnabled(enabled)

    def load(self, profile: dict | None) -> None:
        profile = dict(profile or {})
        self.base.setText(str(profile.get("base_url") or ""))
        self.model.setText(str(profile.get("model") or ""))
        self.path.setText(str(profile.get("chat_completions_path") or "/chat/completions"))
        self.schema.setChecked(bool(profile.get("supports_json_schema", True)))

    def value(self) -> dict:
        if not self.base.text().strip() or not self.model.text().strip():
            raise ValueError(f"{self.title()}缺少 API 地址或模型名称")
        return {
            "profile_id": self.profile_id,
            "display_name": "日常 AI" if self.profile_id == "runtime" else "AI 研发",
            "base_url": self.base.text().strip(),
            "model": self.model.text().strip(),
            "api_key_secret_id": f"{self.profile_id}-api-key",
            "chat_completions_path": self.path.text().strip() or "/chat/completions",
            "timeout_seconds": 120,
            "max_retries": 3,
            "supports_json_schema": self.schema.isChecked(),
        }

    def store_key(self, profile: dict) -> None:
        if self.key.text():
            self.config.secret_set(str(profile["api_key_secret_id"]), self.key.text())
            self.key.clear()

    def probe(self) -> None:
        try:
            profile = self.value()
            self.store_key(profile)
            self.config.data[f"{self.profile_id}_profile"] = profile
            result = run_probe(self.config, self.profile_id, profile)
            if result.last_probe_ok:
                self.test_result.setText(f"测试成功，响应时间 {result.latency_ms} 毫秒")
                self.test_result.setStyleSheet("color:#15803d")
            else:
                self.test_result.setText(f"测试失败：{result.error_summary}")
                self.test_result.setStyleSheet("color:#b91c1c")
        except Exception as exc:
            self.test_result.setText(f"测试失败：{exc}")
            self.test_result.setStyleSheet("color:#b91c1c")


class SettingsPage(Page):
    restartRequested = Signal()

    def __init__(self, config, api=None, parent=None):
        super().__init__("设置", "设置 Minecraft 目录、日常 AI、AI 研发和使用限制。", parent)
        self.config = config
        self.api = api
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)

        minecraft = QGroupBox("Minecraft")
        minecraft_form = QFormLayout(minecraft)
        self.game_dir = QLineEdit()
        game_row = QHBoxLayout()
        game_row.addWidget(self.game_dir, 1)
        auto = QPushButton("自动寻找")
        auto.clicked.connect(self.auto_locate)
        choose = QPushButton("重新选择")
        choose.clicked.connect(self.browse)
        game_row.addWidget(auto)
        game_row.addWidget(choose)
        self.minecraft_status = QLabel("尚未检查")
        self.minecraft_status.setWordWrap(True)
        minecraft_form.addRow("游戏文件夹", game_row)
        minecraft_form.addRow("检查结果", self.minecraft_status)
        layout.addWidget(minecraft)

        self.runtime = ProviderEditor("日常 AI", "runtime", config)
        layout.addWidget(self.runtime)

        rnd_box = QGroupBox("AI 研发")
        rnd_layout = QVBoxLayout(rnd_box)
        self.rnd_same = QCheckBox("使用与日常 AI 相同的配置")
        self.rnd_same.toggled.connect(self.toggle_rnd_same)
        self.rnd = ProviderEditor("独立研发接口", "rnd", config)
        rnd_layout.addWidget(self.rnd_same)
        rnd_layout.addWidget(self.rnd)
        layout.addWidget(rnd_box)

        rules = QGroupBox("研发周期和使用限制")
        rule_form = QFormLayout(rules)
        self.cycle_days = QSpinBox()
        self.cycle_days.setRange(1, 365)
        self.cycle_days.setValue(5)
        self.rnd_budget = QSpinBox()
        self.rnd_budget.setRange(1_000_000, 2_000_000_000)
        self.rnd_budget.setSingleStep(1_000_000)
        self.rnd_budget.setValue(100_000_000)
        rule_form.addRow("每隔多少游戏日研发一次", self.cycle_days)
        rule_form.addRow("每次研发最多使用", self.rnd_budget)
        layout.addWidget(rules)

        self.advanced = QGroupBox("展开高级设置")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_form = QFormLayout(self.advanced)
        self.host = QLineEdit()
        self.bridge_port = QSpinBox(); self.bridge_port.setRange(1024, 65535)
        self.control_port = QSpinBox(); self.control_port.setRange(1024, 65535)
        self.source_workspace = QLineEdit()
        self.harness_path = QLineEdit()
        self.review_seconds = QSpinBox(); self.review_seconds.setRange(15, 3600)
        self.api_timeout = QSpinBox(); self.api_timeout.setRange(5, 1800)
        self.runtime_limit = QSpinBox(); self.runtime_limit.setRange(0, 2_000_000_000); self.runtime_limit.setSpecialValueText("不限制")
        self.log_level = QLineEdit()
        self.log_directory = QLabel(str(self.config.data_dir / "logs"))
        self.developer_info = QLabel(f"MaidAI {__version__} · Minecraft 1.20.1 · Forge 47.x · TLM 1.5.3")
        self.auto_start = QCheckBox("AI 服务启动后自动开始")
        advanced_form.addRow("本机地址", self.host)
        advanced_form.addRow("游戏连接端口", self.bridge_port)
        advanced_form.addRow("界面连接端口", self.control_port)
        advanced_form.addRow("研发源码目录", self.source_workspace)
        advanced_form.addRow("本地研发程序", self.harness_path)
        advanced_form.addRow("AI 请求超时（秒）", self.api_timeout)
        advanced_form.addRow("重新判断间隔（秒）", self.review_seconds)
        advanced_form.addRow("日常 AI 每游戏日上限", self.runtime_limit)
        advanced_form.addRow("日志级别", self.log_level)
        advanced_form.addRow("日志目录", self.log_directory)
        advanced_form.addRow("开发者信息", self.developer_info)
        advanced_form.addRow("日常 AI 请求路径", self.runtime.path)
        advanced_form.addRow("", self.runtime.schema)
        advanced_form.addRow("研发 AI 请求路径", self.rnd.path)
        advanced_form.addRow("", self.rnd.schema)
        advanced_form.addRow("", self.auto_start)
        diagnostic_row = QHBoxLayout()
        diagnostics = QPushButton("刷新诊断信息")
        diagnostics.clicked.connect(lambda: self.api.command("GET_DIAGNOSTICS") if self.api else None)
        export = QPushButton("导出诊断包")
        export.clicked.connect(lambda: self.api.command("EXPORT_DIAGNOSTICS") if self.api else None)
        diagnostic_row.addWidget(diagnostics)
        diagnostic_row.addWidget(export)
        advanced_form.addRow(diagnostic_row)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(180)
        advanced_form.addRow("诊断信息", self.diagnostics)
        self.advanced.toggled.connect(self._advanced_toggled)
        layout.addWidget(self.advanced)

        self.restart_note = QLabel()
        self.restart_note.setStyleSheet("color:#b45309")
        self.restart_note.setWordWrap(True)
        layout.addWidget(self.restart_note)
        save = QPushButton("保存设置并重启 AI 服务")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch()
        scroll.setWidget(body)
        self.layout.addWidget(scroll, 1)
        self.refreshRequested.connect(self.refresh)
        self.load()
        self._advanced_toggled(False)

    def _advanced_toggled(self, checked: bool) -> None:
        for widget in self.advanced.findChildren(QWidget):
            widget.setVisible(checked)

    def refresh(self) -> None:
        self.refresh_minecraft()
        if self.api:
            self.api.command("GET_DIAGNOSTICS")

    def browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Minecraft 文件夹", self.game_dir.text())
        if path:
            self.game_dir.setText(path)
            self.refresh_minecraft()

    def auto_locate(self) -> None:
        state = locate_minecraft(self.game_dir.text())
        if state.get("path"):
            self.game_dir.setText(str(state["path"]))
        self.refresh_minecraft()

    def refresh_minecraft(self) -> None:
        state = inspect_minecraft_dir(self.game_dir.text())
        if state.get("ready"):
            text, color = "已找到游戏、女仆 Mod 和连接组件", "#15803d"
        elif state.get("path_valid"):
            missing = []
            if not state.get("tlm_found"): missing.append("女仆 Mod")
            if not state.get("bridge_found"): missing.append("连接组件")
            text, color = "文件夹有效，但缺少：" + "、".join(missing), "#b45309"
        else:
            text, color = "还没有选择有效的游戏文件夹", "#b91c1c"
        self.minecraft_status.setText(text)
        self.minecraft_status.setStyleSheet(f"color:{color}")

    def toggle_rnd_same(self, same: bool) -> None:
        if same:
            self.rnd.base.setText(self.runtime.base.text())
            self.rnd.model.setText(self.runtime.model.text())
        self.rnd.set_editor_enabled(not same)

    def load(self) -> None:
        d = self.config.data
        self.game_dir.setText(str(d.get("minecraft_dir") or ""))
        self.runtime.load(d.get("runtime_profile"))
        rnd_profile = dict(d.get("rnd_profile") or {})
        runtime_profile = dict(d.get("runtime_profile") or {})
        self.rnd.load(rnd_profile)
        same = bool(runtime_profile and rnd_profile and runtime_profile.get("base_url") == rnd_profile.get("base_url") and runtime_profile.get("model") == rnd_profile.get("model"))
        self.rnd_same.setChecked(same)
        self.toggle_rnd_same(same)
        self.host.setText(str(d.get("host") or "127.0.0.1"))
        self.bridge_port.setValue(int(d.get("bridge_port", 8765)))
        self.control_port.setValue(int(d.get("control_port", 8766)))
        self.source_workspace.setText(str(d.get("source_workspace") or ""))
        self.harness_path.setText(str(d.get("full_harness_runner_path") or ""))
        self.review_seconds.setValue(int(d.get("autonomous_review_seconds", 90)))
        self.api_timeout.setValue(int(runtime_profile.get("timeout_seconds", 120)))
        self.log_level.setText(str(d.get("log_level") or "INFO"))
        self.auto_start.setChecked(bool(d.get("auto_start", False)))
        self.runtime_limit.setValue(int((d.get("runtime_budget") or {}).get("max_per_game_day") or 0))
        self.rnd_budget.setValue(int((d.get("rnd_budget") or {}).get("budget_per_cycle", 100_000_000)))
        self.cycle_days.setValue(int((d.get("rnd_budget") or {}).get("cycle_game_days", 5)))
        self.restart_note.setText("游戏连接端口已改变，需要重启 Minecraft。" if d.get("minecraft_restart_required") else "")
        self.refresh_minecraft()

    def update_diagnostics(self, data: dict) -> None:
        self.diagnostics.setPlainText(json.dumps(data, ensure_ascii=False, indent=2, default=str))

    def save(self) -> None:
        try:
            runtime = self.runtime.value()
            runtime["timeout_seconds"] = self.api_timeout.value()
            if self.rnd_same.isChecked():
                rnd = deepcopy(runtime)
                rnd.update({"profile_id": "rnd", "display_name": "AI 研发"})
            else:
                rnd = self.rnd.value()
                rnd["timeout_seconds"] = self.api_timeout.value()
            old_bridge = int(self.config.data.get("bridge_port", 8765))
            self.runtime.store_key(runtime)
            if not self.rnd_same.isChecked(): self.rnd.store_key(rnd)
            d = self.config.data
            d.update({
                "minecraft_dir": self.game_dir.text().strip(), "runtime_profile": runtime,
                "rnd_profile": rnd, "host": self.host.text().strip() or "127.0.0.1",
                "bridge_port": self.bridge_port.value(), "control_port": self.control_port.value(),
                "source_workspace": self.source_workspace.text().strip(),
                "full_harness_runner_path": self.harness_path.text().strip(),
                "autonomous_review_seconds": self.review_seconds.value(),
                "log_level": self.log_level.text().strip() or "INFO", "auto_start": self.auto_start.isChecked(),
            })
            d["runtime_budget"] = {"enabled": self.runtime_limit.value() > 0, "max_per_game_day": self.runtime_limit.value() or None, "max_per_real_hour": None, "reserve_tokens": 4096}
            d["rnd_budget"] = {"budget_per_cycle": self.rnd_budget.value(), "cycle_game_days": self.cycle_days.value(), "max_single_request": min(2_000_000, self.rnd_budget.value())}
            d["minecraft_restart_required"] = bool(d.get("minecraft_restart_required")) or old_bridge != self.bridge_port.value()
            self.config.save()
            self.restartRequested.emit()
            QMessageBox.information(self, "已保存", "设置已经保存，AI 服务正在重新启动。")
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
