from __future__ import annotations
import os,subprocess,sys
from pathlib import Path
from PySide6.QtWidgets import QHBoxLayout,QPushButton,QLabel
from maid_ai_control.widgets import Page,JsonViewer

class DiagnosticsPage(Page):
    def __init__(self,api,config,parent=None):
        super().__init__("诊断","查看真实连接、端口、进程、R&D 完整度和最近模型请求；可导出不含 API Key 的诊断包。",parent);self.api=api;self.config=config;row=QHBoxLayout();export=QPushButton("导出诊断包");export.clicked.connect(lambda:self.api.command("EXPORT_DIAGNOSTICS"));folder=QPushButton("打开数据目录");folder.clicked.connect(self.open_folder);row.addWidget(export);row.addWidget(folder);row.addStretch();self.layout.addLayout(row);self.viewer=JsonViewer();self.layout.addWidget(self.viewer,1);self.refreshRequested.connect(lambda:self.api.command("GET_DIAGNOSTICS"))
    def update_data(self,data):self.viewer.set_data(data)
    def open_folder(self):
        path=self.config.data_dir
        if sys.platform.startswith("win"):os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform=="darwin":subprocess.Popen(["open",str(path)])
        else:subprocess.Popen(["xdg-open",str(path)])
