import sys

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication, QWidget

from jinx_ai.widgets import FlowLayout


def test_flow_layout_stays_one_row_when_wide() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    host = QWidget()
    layout = FlowLayout(host, spacing=4)
    first = QWidget()
    first.setFixedSize(80, 24)
    second = QWidget()
    second.setFixedSize(80, 24)
    layout.addWidget(first)
    layout.addWidget(second)
    assert layout.heightForWidth(200) <= 28
    assert layout.heightForWidth(100) >= 48


def test_flow_layout_two_columns() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    host = QWidget()
    layout = FlowLayout(host, spacing=8, columns=2)
    first = QWidget()
    first.setFixedHeight(40)
    second = QWidget()
    second.setFixedHeight(40)
    layout.addWidget(first)
    layout.addWidget(second)
    layout.setGeometry(QRect(0, 0, 408, 80))
    assert first.width() == 200
    assert second.width() == 200
    assert first.x() == 0
    assert second.x() == 208


def test_flow_layout_does_not_spread_fixed_cards() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    host = QWidget()
    layout = FlowLayout(host, spacing=10, grow=False)
    first = QWidget()
    first.setFixedSize(80, 40)
    second = QWidget()
    second.setFixedSize(80, 40)
    layout.addWidget(first)
    layout.addWidget(second)
    layout.setGeometry(QRect(0, 0, 400, 50))
    assert first.x() == 0
    assert second.x() == 90
    assert first.width() == 80
    assert second.width() == 80


def test_flow_layout_two_columns_equal_height() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    host = QWidget()
    layout = FlowLayout(host, spacing=8, columns=2)
    first = QWidget()
    first.setMinimumHeight(40)
    second = QWidget()
    second.setMinimumHeight(70)
    layout.addWidget(first)
    layout.addWidget(second)
    layout.setGeometry(QRect(0, 0, 408, 120))
    assert first.height() == second.height()
    assert first.height() >= 70
