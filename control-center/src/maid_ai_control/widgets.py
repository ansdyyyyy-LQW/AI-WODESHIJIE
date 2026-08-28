from __future__ import annotations

import json
from typing import Any
from PySide6.QtCore import Qt,Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QFrame,QTableWidget,QTableWidgetItem,QHeaderView,QPushButton,QPlainTextEdit,QMessageBox


def value_text(value:Any)->str:
    if value is None:return "—"
    if isinstance(value,bool):return "是" if value else "否"
    if isinstance(value,(dict,list)):return json.dumps(value,ensure_ascii=False,separators=(",",":"))
    return str(value)


class Card(QFrame):
    def __init__(self,title:str,value:str="—",parent:QWidget|None=None):
        super().__init__(parent);self.setObjectName("card");layout=QVBoxLayout(self);self.title=QLabel(title);self.title.setObjectName("cardTitle");self.value=QLabel(value);self.value.setObjectName("cardValue");self.value.setWordWrap(True);layout.addWidget(self.title);layout.addWidget(self.value)
    def set_value(self,value:Any)->None:self.value.setText(value_text(value))


class Page(QWidget):
    refreshRequested=Signal()
    def __init__(self,title:str,description:str="",parent:QWidget|None=None):
        super().__init__(parent);self.layout=QVBoxLayout(self);self.layout.setContentsMargins(24,20,24,20);header=QHBoxLayout();texts=QVBoxLayout();h=QLabel(title);h.setObjectName("pageTitle");texts.addWidget(h)
        if description:
            d=QLabel(description);d.setObjectName("pageDescription");d.setWordWrap(True);texts.addWidget(d)
        header.addLayout(texts,1);self.refresh_button=QPushButton("刷新");self.refresh_button.clicked.connect(self.refreshRequested);header.addWidget(self.refresh_button);self.layout.addLayout(header)
    def error(self,title:str,message:str)->None:QMessageBox.warning(self,title,message)


class DataTable(QTableWidget):
    def __init__(self,columns:list[tuple[str,str]],parent:QWidget|None=None):
        super().__init__(parent);self.columns=columns;self.setColumnCount(len(columns));self.setHorizontalHeaderLabels([label for _,label in columns]);self.setAlternatingRowColors(True);self.setSelectionBehavior(QTableWidget.SelectRows);self.setEditTriggers(QTableWidget.NoEditTriggers);self.verticalHeader().setVisible(False);self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);self.horizontalHeader().setStretchLastSection(True)
    @staticmethod
    def _get(row:dict[str,Any],path:str)->Any:
        current:Any=row
        for part in path.split('.'):
            if isinstance(current,dict):current=current.get(part)
            else:return None
        return current
    def set_rows(self,rows:list[dict[str,Any]])->None:
        self.setSortingEnabled(False);self.setRowCount(len(rows))
        for r,row in enumerate(rows):
            for c,(path,_) in enumerate(self.columns):
                item=QTableWidgetItem(value_text(self._get(row,path)));item.setData(Qt.UserRole,row);self.setItem(r,c,item)
        self.setSortingEnabled(True)
    def selected_data(self)->dict[str,Any]|None:
        rows=self.selectionModel().selectedRows() if self.selectionModel() else []
        if not rows:return None
        item=self.item(rows[0].row(),0);return item.data(Qt.UserRole) if item else None


class JsonViewer(QPlainTextEdit):
    def __init__(self,parent:QWidget|None=None):super().__init__(parent);self.setReadOnly(True);self.setLineWrapMode(QPlainTextEdit.NoWrap);self.setFont(QFont("Consolas",9))
    def set_data(self,data:Any)->None:self.setPlainText(json.dumps(data,ensure_ascii=False,indent=2,default=str))
