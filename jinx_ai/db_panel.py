"""Browse SQLite and SQL Server databases; insert, edit, and delete rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db_backend import (
    DatabaseBackend,
    add_connection,
    get_connection,
    load_connections,
    open_backend,
    pk_map_for_row,
    remove_connection,
    update_connection,
)


class SqlServerDialog(QDialog):
    def __init__(self, parent=None, spec: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SQL Server connection")
        self.setMinimumWidth(420)
        spec = spec or {}
        form = QFormLayout()
        self.name = QLineEdit(str(spec.get("name") or "SQL Server"))
        self.server = QLineEdit(str(spec.get("server") or r"localhost\SQLEXPRESS"))
        self.database = QLineEdit(str(spec.get("database") or ""))
        self.trusted = QCheckBox("Windows authentication")
        self.trusted.setChecked(bool(spec.get("trusted", True)))
        self.username = QLineEdit(str(spec.get("username") or ""))
        self.password = QLineEdit(str(spec.get("password") or ""))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Name", self.name)
        form.addRow("Server", self.server)
        form.addRow("Database", self.database)
        form.addRow(self.trusted)
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def spec(self) -> dict[str, Any]:
        return {
            "kind": "sqlserver",
            "name": self.name.text().strip() or "SQL Server",
            "server": self.server.text().strip(),
            "database": self.database.text().strip(),
            "trusted": self.trusted.isChecked(),
            "username": self.username.text().strip(),
            "password": self.password.text(),
        }


class InsertRowDialog(QDialog):
    def __init__(self, columns: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert row")
        self.fields: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for col in columns:
            if col.name in {"rowid", "__rowid"}:
                continue
            edit = QLineEdit()
            hint = col.type_name or ""
            if col.pk:
                hint = (hint + " PK").strip()
            edit.setPlaceholderText(hint)
            self.fields[col.name] = edit
            form.addRow(col.name, edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Any]:
        return {name: edit.text() for name, edit in self.fields.items() if edit.text() != ""}


class DatabasePanel(QWidget):
    status = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend: DatabaseBackend | None = None
        self._table = ""
        self._pk_names: list[str] = []
        self._loading = False
        self._offset = 0
        self._limit = 200

        self.conn_combo = QComboBox()
        self.tables = QListWidget()
        self.tables.currentTextChanged.connect(self._on_table)
        self.grid = QTableWidget()
        self.grid.itemChanged.connect(self._on_cell_changed)
        self.sql = QPlainTextEdit()
        self.sql.setPlaceholderText("SQL (SELECT / INSERT / UPDATE / DELETE)")
        self.sql.setMaximumHeight(80)
        self.info = QLabel("No connection")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)

        def _btn(text: str, slot, secondary: bool = True, tip: str = "") -> QPushButton:
            btn = QPushButton(text)
            if secondary:
                btn.setObjectName("secondary")
            btn.setToolTip(tip or text)
            btn.clicked.connect(slot)
            return btn

        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        conn_row.addWidget(self.conn_combo, stretch=1)
        conn_row.addWidget(_btn("SQLite", self._add_sqlite, tip="Add a SQLite file"))
        conn_row.addWidget(_btn("SQL Server", self._add_sqlserver, tip="Add a SQL Server connection"))
        conn_row.addWidget(_btn("Edit", self._edit_conn, tip="Edit this connection"))
        conn_row.addWidget(_btn("Remove", self._remove_conn, tip="Remove this connection"))

        table_row = QHBoxLayout()
        table_row.setSpacing(6)
        table_row.addWidget(_btn("Reload", self._load_table, tip="Reload the current table"))
        table_row.addWidget(_btn("Insert", self._insert, secondary=False, tip="Insert a row"))
        table_row.addWidget(_btn("Delete", self._delete, tip="Delete the selected row"))
        table_row.addWidget(_btn("Run SQL", self._run_sql, tip="Run the SQL below"))

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tables)
        split.addWidget(self.grid)
        split.addWidget(self.sql)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setStretchFactor(2, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(conn_row)
        layout.addWidget(self.info)
        layout.addLayout(table_row)
        layout.addWidget(split, stretch=1)

        self.conn_combo.currentIndexChanged.connect(self._on_conn_changed)
        self.reload_connections()

    def reload_connections(self, select_id: str | None = None) -> None:
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        self.conn_combo.addItem("No connection", "")
        for spec in load_connections():
            label = spec.get("name") or spec.get("path") or spec.get("database") or spec.get("id")
            self.conn_combo.addItem(str(label), spec.get("id"))
        self.conn_combo.blockSignals(False)
        if select_id:
            idx = self.conn_combo.findData(select_id)
            if idx >= 0:
                self.conn_combo.setCurrentIndex(idx)
                return
        if self.conn_combo.count() > 1:
            self.conn_combo.setCurrentIndex(1)

    def open_sqlite_file(self, path: str) -> None:
        folder = Path(path).expanduser().resolve()
        for existing in load_connections():
            if existing.get("kind") == "sqlite":
                try:
                    if Path(existing.get("path") or "").resolve() == folder:
                        self.reload_connections(existing.get("id"))
                        self.status.emit(f"Opened SQLite: {folder.name}")
                        return
                except OSError:
                    continue
        spec = add_connection({
            "kind": "sqlite",
            "name": folder.name,
            "path": str(folder),
        })
        self.reload_connections(spec["id"])
        self.status.emit(f"Opened SQLite: {folder.name}")

    def _add_sqlite(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open SQLite database",
            "",
            "SQLite (*.db *.sqlite *.sqlite3 *.db3);;All files (*.*)",
        )
        if path:
            self.open_sqlite_file(path)

    def _add_sqlserver(self) -> None:
        dlg = SqlServerDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        spec = dlg.spec()
        if not spec["server"] or not spec["database"]:
            QMessageBox.warning(self, "SQL Server", "Server and database are required.")
            return
        saved = add_connection(spec)
        self.reload_connections(saved["id"])

    def _edit_conn(self) -> None:
        conn_id = str(self.conn_combo.currentData() or "")
        spec = get_connection(conn_id) if conn_id else None
        if not spec:
            self.status.emit("Select a connection to edit.")
            return
        if spec.get("kind") == "sqlserver":
            dlg = SqlServerDialog(self, spec)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            updated = dlg.spec()
            if not updated["server"] or not updated["database"]:
                QMessageBox.warning(self, "SQL Server", "Server and database are required.")
                return
            update_connection(conn_id, updated)
        else:
            from PyQt6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(
                self, "Edit SQLite", "Name:", text=str(spec.get("name") or "")
            )
            if not ok or not name.strip():
                return
            path, _ = QFileDialog.getOpenFileName(
                self,
                "SQLite file",
                str(spec.get("path") or ""),
                "SQLite (*.db *.sqlite *.sqlite3 *.db3);;All files (*.*)",
            )
            update_connection(
                conn_id,
                {"name": name.strip(), "path": path or spec.get("path")},
            )
        self.reload_connections(conn_id)
        self.status.emit("Connection updated.")

    def _remove_conn(self) -> None:
        conn_id = str(self.conn_combo.currentData() or "")
        if not conn_id:
            return
        remove_connection(conn_id)
        self._close_backend()
        self.reload_connections()

    def _close_backend(self) -> None:
        if self.backend is not None:
            self.backend.close()
            self.backend = None
        self.tables.clear()
        self.grid.clear()
        self.grid.setRowCount(0)
        self.grid.setColumnCount(0)

    def _on_conn_changed(self, _index: int = 0) -> None:
        conn_id = str(self.conn_combo.currentData() or "")
        self._close_backend()
        if not conn_id:
            self.info.setText("No connection")
            return
        spec = get_connection(conn_id)
        if spec is None:
            self.info.setText("Connection missing")
            return
        try:
            self.backend = open_backend(spec)
            tables = self.backend.list_tables()
        except Exception as exc:  # noqa: BLE001
            self.backend = None
            self.info.setText(str(exc))
            QMessageBox.warning(self, "Database", str(exc))
            return
        self.info.setText(f"{spec.get('kind')}: {len(tables)} table(s)")
        self.tables.clear()
        self.tables.addItems(tables)

    def _on_table(self, name: str) -> None:
        self._table = name
        self._offset = 0
        if name:
            self._load_table()

    def _load_table(self) -> None:
        if self.backend is None or not self._table:
            return
        try:
            cols = self.backend.columns(self._table)
            result = self.backend.fetch_rows(self._table, self._offset, self._limit)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Database", str(exc))
            return
        self._pk_names = [c.name for c in cols if c.pk] or (
            ["rowid"] if self.backend.kind == "sqlite" else []
        )
        self._fill_grid(result.columns, result.rows)
        self.info.setText(
            f"{self._table}  ·  {len(result.rows)} row(s)  ·  PK: {', '.join(self._pk_names) or 'none (read-only edits)'}"
        )

    def _fill_grid(self, columns: list[str], rows: list[list[Any]]) -> None:
        self._loading = True
        self.grid.clear()
        self.grid.setColumnCount(len(columns))
        self.grid.setHorizontalHeaderLabels(columns)
        self.grid.setRowCount(len(rows))
        for i in range(len(columns)):
            self.grid.setColumnHidden(i, False)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                if columns[c] in {"__rowid", "rowid"} and columns[c] in (self._pk_names + ["__rowid"]):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, pk_map_for_row(columns, row, self._pk_names))
                self.grid.setItem(r, c, item)
        if "__rowid" in columns:
            self.grid.setColumnHidden(columns.index("__rowid"), True)
        self.grid.resizeColumnsToContents()
        self._loading = False

    def _row_pk(self, row: int) -> dict[str, Any]:
        first = self.grid.item(row, 0)
        if first is not None:
            stored = first.data(Qt.ItemDataRole.UserRole)
            if isinstance(stored, dict) and stored:
                return stored
        columns = [self.grid.horizontalHeaderItem(i).text() for i in range(self.grid.columnCount())]
        values = []
        for c in range(self.grid.columnCount()):
            item = self.grid.item(row, c)
            values.append(None if item is None else item.text())
        return pk_map_for_row(columns, values, self._pk_names)

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or self.backend is None or not self._table:
            return
        if not self._pk_names:
            self.status.emit("No primary key — cannot edit this table in the grid.")
            return
        col_name = self.grid.horizontalHeaderItem(item.column()).text()
        if col_name in {"__rowid"}:
            return
        pk = self._row_pk(item.row())
        if not pk:
            return
        try:
            msg = self.backend.update_row(self._table, pk, {col_name: item.text()})
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Update failed", str(exc))
            self._load_table()
            return
        self.status.emit(msg)

    def _insert(self) -> None:
        if self.backend is None or not self._table:
            return
        dlg = InsertRowDialog(self.backend.columns(self._table), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            msg = self.backend.insert_row(self._table, dlg.values())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Insert failed", str(exc))
            return
        self.status.emit(msg)
        self._load_table()

    def _delete(self) -> None:
        if self.backend is None or not self._table:
            return
        row = self.grid.currentRow()
        if row < 0:
            self.status.emit("Select a row to delete.")
            return
        pk = self._row_pk(row)
        if not pk:
            QMessageBox.warning(self, "Delete", "This table has no primary key / rowid.")
            return
        if QMessageBox.question(
            self,
            "Delete row",
            f"Delete the selected row from {self._table}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            msg = self.backend.delete_row(self._table, pk)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self.status.emit(msg)
        self._load_table()

    def _run_sql(self) -> None:
        if self.backend is None:
            return
        sql = self.sql.toPlainText().strip()
        try:
            result = self.backend.execute(sql)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "SQL failed", str(exc))
            return
        if result.columns:
            self._pk_names = []
            self._fill_grid(result.columns, result.rows)
            self.info.setText(result.message or f"{len(result.rows)} row(s)")
        else:
            self.status.emit(result.message or "OK")
            self._load_table()
