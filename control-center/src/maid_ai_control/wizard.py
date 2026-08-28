from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from maid_ai_control.api_probe import probe_is_recent, profile_signature, run_probe
from maid_ai_control.config import ConfigManager
from maid_ai_control.minecraft_locator import inspect_minecraft_dir, locate_minecraft


def _profile(profile_id: str, base: str, model: str, secret_id: str) -> dict:
    return {
        "profile_id": profile_id,
        "display_name": "日常 AI" if profile_id == "runtime" else "AI 研发",
        "base_url": base.strip(),
        "model": model.strip(),
        "api_key_secret_id": secret_id,
        "chat_completions_path": "/chat/completions",
        "timeout_seconds": 120,
        "max_retries": 3,
        "supports_json_schema": True,
    }


class SetupWizard(QWizard):
    """First-run product setup; all four pages write the production config."""

    MINECRAFT_PAGE = 0
    RUNTIME_PAGE = 1
    RND_PAGE = 2
    FINISH_PAGE = 3

    def __init__(self, config: ConfigManager, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Maid AI 首次设置")
        self.setMinimumSize(700, 520)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setWizardStyle(QWizard.ModernStyle)
        self._minecraft_state: dict = {}
        self.addPage(self._minecraft_page())
        self.addPage(self._runtime_page())
        self.addPage(self._rnd_page())
        self.addPage(self._finish_page())
        self.currentIdChanged.connect(self._page_changed)
        self._auto_locate()

    def _minecraft_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("1. Minecraft 环境")
        page.setSubTitle("选择实际使用的 .minecraft 或启动器实例文件夹，程序会自动检查女仆 Mod 和连接组件。")
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.minecraft_dir = QLineEdit(str(self.config.data.get("minecraft_dir") or ""))
        auto = QPushButton("自动寻找")
        auto.clicked.connect(self._auto_locate)
        choose = QPushButton("选择文件夹")
        choose.clicked.connect(self._choose_minecraft)
        row.addWidget(self.minecraft_dir, 1)
        row.addWidget(auto)
        row.addWidget(choose)
        layout.addLayout(row)
        self.minecraft_status = QLabel()
        self.minecraft_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.minecraft_status)
        layout.addStretch()
        return page

    def _runtime_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("2. 日常 AI")
        page.setSubTitle("填写每天控制女仆所用的 AI 服务。模型名称必须与服务中的名称完全一致，API Key 不会写入普通配置文件。")
        profile = dict(self.config.data.get("runtime_profile") or {})
        form = QFormLayout(page)
        self.runtime_base = QLineEdit(str(profile.get("base_url") or ""))
        self.runtime_model = QLineEdit(str(profile.get("model") or ""))
        self.runtime_key = QLineEdit()
        self.runtime_key.setEchoMode(QLineEdit.Password)
        self.runtime_key.setPlaceholderText("已保存时可留空")
        test = QPushButton("测试日常 AI 接口")
        test.clicked.connect(self._probe_runtime)
        self.runtime_probe = QLabel("尚未测试")
        form.addRow("API 地址", self.runtime_base)
        form.addRow("API Key", self.runtime_key)
        form.addRow("模型名称", self.runtime_model)
        form.addRow(test, self.runtime_probe)
        return page

    def _rnd_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("3. AI 研发")
        page.setSubTitle("AI 研发使用独立用量记录；可以复用日常 AI 的服务配置，但研发预算仍单独计算。")
        form = QFormLayout(page)
        self.rnd_same = QCheckBox("使用与日常 AI 相同的配置")
        self.rnd_same.setChecked(True)
        self.rnd_same.toggled.connect(self._toggle_rnd)
        profile = dict(self.config.data.get("rnd_profile") or {})
        self.rnd_base = QLineEdit(str(profile.get("base_url") or ""))
        self.rnd_model = QLineEdit(str(profile.get("model") or ""))
        self.rnd_key = QLineEdit()
        self.rnd_key.setEchoMode(QLineEdit.Password)
        self.rnd_key.setPlaceholderText("已保存时可留空")
        test = QPushButton("测试 AI 研发接口")
        test.clicked.connect(self._probe_rnd)
        self.rnd_probe = QLabel("尚未测试")
        self.rnd_paths = QLabel()
        form.addRow(self.rnd_same)
        form.addRow("API 地址", self.rnd_base)
        form.addRow("API Key", self.rnd_key)
        form.addRow("模型名称", self.rnd_model)
        form.addRow(test, self.rnd_probe)
        form.addRow("本地研发环境", self.rnd_paths)
        self._toggle_rnd(True)
        return page

    def _finish_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("4. 完成")
        page.setSubTitle("基础环境已就绪。进入主界面后，Minecraft 在线玩家和女仆将自动发现。")
        layout = QVBoxLayout(page)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch()
        return page

    def _choose_minecraft(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择 Minecraft 目录", self.minecraft_dir.text())
        if selected:
            self.minecraft_dir.setText(selected)
            self._refresh_minecraft()

    def _auto_locate(self) -> None:
        state = locate_minecraft(self.minecraft_dir.text())
        if state.get("path"):
            self.minecraft_dir.setText(str(state["path"]))
        self._refresh_minecraft()

    def _refresh_minecraft(self) -> None:
        self._minecraft_state = inspect_minecraft_dir(self.minecraft_dir.text())
        state = self._minecraft_state
        mark = lambda value: "✓" if value else "✗"
        tlm = state.get("tlm") or {}
        bridge = state.get("bridge") or {}
        self.minecraft_status.setText(
            f"Minecraft 目录：{mark(state.get('path_valid'))}\n"
            f"Touhou Little Maid：{mark(state.get('tlm_found'))} {tlm.get('version', '')}\n"
            f"MaidAI 连接组件：{mark(state.get('bridge_found'))} {bridge.get('version', '')}"
        )

    def _runtime_profile(self) -> dict:
        return _profile("runtime", self.runtime_base.text(), self.runtime_model.text(), "runtime-api-key")

    def _rnd_profile(self) -> dict:
        if self.rnd_same.isChecked():
            profile = deepcopy(self._runtime_profile())
            profile["profile_id"] = "rnd"
            profile["display_name"] = "AI 研发"
            return profile
        return _profile("rnd", self.rnd_base.text(), self.rnd_model.text(), "rnd-api-key")

    def _store_key(self, secret_id: str, value: str) -> None:
        if value:
            self.config.secret_set(secret_id, value)

    def _probe_runtime(self) -> None:
        profile = self._runtime_profile()
        self._store_key(profile["api_key_secret_id"], self.runtime_key.text())
        self.config.data["runtime_profile"] = profile
        result = run_probe(self.config, "runtime", profile)
        self.runtime_probe.setText(
            f"成功，{result.latency_ms} ms，HTTP {result.http_status}" if result.last_probe_ok
            else f"失败：{result.error_summary}"
        )

    def _probe_rnd(self) -> None:
        profile = self._rnd_profile()
        if not self.rnd_same.isChecked():
            self._store_key(profile["api_key_secret_id"], self.rnd_key.text())
        self.config.data["rnd_profile"] = profile
        result = run_probe(self.config, "rnd", profile)
        self.rnd_probe.setText(
            f"成功，{result.latency_ms} ms，HTTP {result.http_status}" if result.last_probe_ok
            else f"失败：{result.error_summary}"
        )

    def _toggle_rnd(self, same: bool) -> None:
        for widget in (self.rnd_base, self.rnd_model, self.rnd_key):
            widget.setEnabled(not same)
        if same:
            self.rnd_base.setText(self.runtime_base.text())
            self.rnd_model.setText(self.runtime_model.text())

    def _harness_ready(self) -> bool:
        source = Path(str(self.config.data.get("source_workspace") or ""))
        runner = Path(str(self.config.data.get("full_harness_runner_path") or ""))
        ready = source.is_dir() and runner.is_file()
        self.rnd_paths.setText(
            f"本地研发程序：{'就绪' if runner.is_file() else '缺失'}；研发源码：{'就绪' if source.is_dir() else '缺失'}"
        )
        return ready

    def _page_changed(self, page_id: int) -> None:
        if page_id == self.MINECRAFT_PAGE:
            self._refresh_minecraft()
        elif page_id == self.RND_PAGE:
            self._toggle_rnd(self.rnd_same.isChecked())
            self._harness_ready()
        elif page_id == self.FINISH_PAGE:
            self.summary.setText(
                "Minecraft、女仆 Mod、连接组件、日常 AI 与本地研发环境均已通过基础检查。\n\n"
                "主界面的“开始 AI”按钮还会等待游戏在线、版本匹配、玩家与女仆选择完成后才启用。"
            )

    def validateCurrentPage(self) -> bool:  # noqa: N802 - Qt API
        page_id = self.currentId()
        if page_id == self.MINECRAFT_PAGE:
            self._refresh_minecraft()
            if not self._minecraft_state.get("ready"):
                QMessageBox.warning(self, "环境未就绪", "请选择含 Touhou Little Maid 和 MaidAI 连接组件的真实 Minecraft 实例。")
                return False
            self.config.data["minecraft_dir"] = self.minecraft_dir.text().strip()
        elif page_id == self.RUNTIME_PAGE:
            profile = self._runtime_profile()
            if not probe_is_recent(self.config, "runtime", profile):
                QMessageBox.warning(self, "日常 AI 未通过测试", "请填写配置并点击“测试日常 AI 接口”。")
                return False
            self.config.data["runtime_profile"] = profile
        elif page_id == self.RND_PAGE:
            profile = self._rnd_profile()
            if self.rnd_same.isChecked() and probe_is_recent(self.config, "runtime", self._runtime_profile()):
                copied = deepcopy((self.config.data.get("api_probes") or {}).get("runtime") or {})
                copied["profile_signature"] = profile_signature(profile)
                self.config.data.setdefault("api_probes", {})["rnd"] = copied
            if not probe_is_recent(self.config, "rnd", profile):
                QMessageBox.warning(self, "AI 研发接口未通过测试", "请测试 AI 研发接口，或选择复用已经通过测试的日常 AI 配置。")
                return False
            if not self._harness_ready():
                QMessageBox.warning(self, "本地研发环境缺失", "正式自主研发需要本地研发程序和研发源码目录。")
                return False
            self.config.data["rnd_profile"] = profile
        return super().validateCurrentPage()

    def accept(self) -> None:
        self.config.data["minecraft_dir"] = self.minecraft_dir.text().strip()
        self.config.data["runtime_profile"] = self._runtime_profile()
        self.config.data["rnd_profile"] = self._rnd_profile()
        self.config.data["setup_complete"] = True
        self.config.save()
        super().accept()
