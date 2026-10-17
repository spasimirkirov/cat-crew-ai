"""SQLite and SQL Server backends for the database explorer."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import BASE_DIR

CONNECTIONS_PATH = BASE_DIR / "data" / "db_connections.json"

SQLITE_EXTS = {".db", ".sqlite", ".sqlite3", ".db3"}


@dataclass
class ColumnInfo:
    name: str
    type_name: str = ""
    pk: bool = False
    nullable: bool = True


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    rowcount: int = 0
    message: str = ""


def quote_ident_sqlite(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def quote_ident_mssql(name: str) -> str:
    return "[" + str(name).replace("]", "]]") + "]"


def load_connections() -> list[dict[str, Any]]:
    if not CONNECTIONS_PATH.is_file():
        return []
    try:
        data = json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_connections(items: list[dict[str, Any]]) -> None:
    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTIONS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_connection(spec: dict[str, Any]) -> dict[str, Any]:
    items = load_connections()
    spec = dict(spec)
    spec.setdefault("id", uuid.uuid4().hex[:10])
    items.append(spec)
    save_connections(items)
    return spec


def update_connection(conn_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    items = load_connections()
    for index, item in enumerate(items):
        if item.get("id") == conn_id:
            merged = dict(item)
            merged.update(spec)
            merged["id"] = conn_id
            items[index] = merged
            save_connections(items)
            return merged
    raise KeyError(conn_id)


def remove_connection(conn_id: str) -> None:
    save_connections([c for c in load_connections() if c.get("id") != conn_id])


def get_connection(conn_id: str) -> dict[str, Any] | None:
    for item in load_connections():
        if item.get("id") == conn_id:
            return item
    return None


def _coerce(value: Any, type_name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value == "":
        return ""
    kind = (type_name or "").upper()
    text = str(value)
    if any(token in kind for token in ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE")):
        if text.strip() == "":
            return None
        if "." in text or "E" in text.upper() or "FLOAT" in kind or "REAL" in kind:
            return float(text)
        return int(float(text))
    if "BOOL" in kind:
        return text.lower() in ("1", "true", "yes")
    return text


class DatabaseBackend:
    kind = "unknown"

    def close(self) -> None:
        return

    def list_tables(self) -> list[str]:
        raise NotImplementedError

    def columns(self, table: str) -> list[ColumnInfo]:
        raise NotImplementedError

    def fetch_rows(self, table: str, offset: int = 0, limit: int = 200) -> QueryResult:
        raise NotImplementedError

    def insert_row(self, table: str, values: dict[str, Any]) -> str:
        raise NotImplementedError

    def update_row(self, table: str, pk: dict[str, Any], changes: dict[str, Any]) -> str:
        raise NotImplementedError

    def delete_row(self, table: str, pk: dict[str, Any]) -> str:
        raise NotImplementedError

    def execute(self, sql: str) -> QueryResult:
        raise NotImplementedError


class SqliteBackend(DatabaseBackend):
    kind = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"SQLite file not found: {self.path}")
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def list_tables(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        return [row[0] for row in cur.fetchall()]

    def columns(self, table: str) -> list[ColumnInfo]:
        cur = self.conn.execute(f"PRAGMA table_info({quote_ident_sqlite(table)})")
        out: list[ColumnInfo] = []
        for row in cur.fetchall():
            out.append(
                ColumnInfo(
                    name=str(row[1]),
                    type_name=str(row[2] or ""),
                    pk=bool(row[5]),
                    nullable=not bool(row[3]),
                )
            )
        return out

    def _pk_columns(self, table: str) -> list[str]:
        pks = [c.name for c in self.columns(table) if c.pk]
        return pks or ["rowid"]

    def fetch_rows(self, table: str, offset: int = 0, limit: int = 200) -> QueryResult:
        ident = quote_ident_sqlite(table)
        pks = self._pk_columns(table)
        extra = ""
        if pks == ["rowid"]:
            extra = ", rowid AS __rowid"
        sql = f"SELECT *{extra} FROM {ident} LIMIT ? OFFSET ?"
        cur = self.conn.execute(sql, (int(limit), int(offset)))
        names = [d[0] for d in cur.description]
        rows = [[row[name] for name in names] for row in cur.fetchall()]
        return QueryResult(columns=names, rows=rows, rowcount=len(rows))

    def insert_row(self, table: str, values: dict[str, Any]) -> str:
        cols = self.columns(table)
        types = {c.name: c.type_name for c in cols}
        keys = [k for k in values if k in types]
        if not keys:
            return "Error: no matching columns."
        placeholders = ", ".join("?" for _ in keys)
        idents = ", ".join(quote_ident_sqlite(k) for k in keys)
        params = [_coerce(values[k], types[k]) for k in keys]
        self.conn.execute(
            f"INSERT INTO {quote_ident_sqlite(table)} ({idents}) VALUES ({placeholders})",
            params,
        )
        self.conn.commit()
        return f"Inserted 1 row into {table}."

    def update_row(self, table: str, pk: dict[str, Any], changes: dict[str, Any]) -> str:
        cols = {c.name: c.type_name for c in self.columns(table)}
        sets = [k for k in changes if k in cols and k not in ("__rowid",)]
        pk = {("rowid" if k == "__rowid" else k): v for k, v in (pk or {}).items()}
        if not sets or not pk:
            return "Error: nothing to update."
        set_sql = ", ".join(f"{quote_ident_sqlite(k)}=?" for k in sets)
        where_keys = list(pk.keys())
        where_sql = " AND ".join(f"{quote_ident_sqlite(k)}=?" for k in where_keys)
        params = [_coerce(changes[k], cols.get(k, "")) for k in sets]
        params.extend(pk[k] for k in where_keys)
        cur = self.conn.execute(
            f"UPDATE {quote_ident_sqlite(table)} SET {set_sql} WHERE {where_sql}",
            params,
        )
        self.conn.commit()
        return f"Updated {cur.rowcount} row(s) in {table}."

    def delete_row(self, table: str, pk: dict[str, Any]) -> str:
        pk = {("rowid" if k == "__rowid" else k): v for k, v in (pk or {}).items()}
        if not pk:
            return "Error: missing primary key."
        where_keys = list(pk.keys())
        where_sql = " AND ".join(f"{quote_ident_sqlite(k)}=?" for k in where_keys)
        cur = self.conn.execute(
            f"DELETE FROM {quote_ident_sqlite(table)} WHERE {where_sql}",
            [pk[k] for k in where_keys],
        )
        self.conn.commit()
        return f"Deleted {cur.rowcount} row(s) from {table}."

    def execute(self, sql: str) -> QueryResult:
        text = (sql or "").strip()
        if not text:
            return QueryResult([], [], message="Error: empty SQL.")
        cur = self.conn.execute(text)
        if cur.description:
            names = [d[0] for d in cur.description]
            rows = [list(row) for row in cur.fetchall()]
            self.conn.commit()
            return QueryResult(names, rows, len(rows))
        self.conn.commit()
        return QueryResult([], [], cur.rowcount, message=f"OK, {cur.rowcount} row(s) affected.")


def _find_odbc_driver() -> str | None:
    try:
        import pyodbc
    except ImportError:
        return None
    preferred = (
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server",
    )
    installed = set(pyodbc.drivers())
    for name in preferred:
        if name in installed:
            return name
    return None


class SqlServerBackend(DatabaseBackend):
    kind = "sqlserver"

    def __init__(self, spec: dict[str, Any]) -> None:
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError(
                "pyodbc is not installed. Install it to connect to SQL Server."
            ) from exc
        driver = _find_odbc_driver()
        if not driver:
            raise RuntimeError(
                "No SQL Server ODBC driver found. Install ODBC Driver 17 or 18."
            )
        server = str(spec.get("server") or "").strip()
        database = str(spec.get("database") or "").strip()
        if not server or not database:
            raise ValueError("SQL Server needs server and database.")
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}",
            "TrustServerCertificate=yes",
        ]
        if spec.get("trusted", True):
            parts.append("Trusted_Connection=yes")
        else:
            user = str(spec.get("username") or "")
            password = str(spec.get("password") or "")
            parts.append(f"UID={user}")
            parts.append(f"PWD={password}")
        self.conn = pyodbc.connect(";".join(parts), timeout=8)
        self.conn.timeout = 30

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def list_tables(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        return [f"{schema}.{name}" for schema, name in cur.fetchall()]

    def _split_table(self, table: str) -> tuple[str, str]:
        if "." in table:
            schema, name = table.split(".", 1)
            return schema, name
        return "dbo", table

    def _qtable(self, table: str) -> str:
        schema, name = self._split_table(table)
        return f"{quote_ident_mssql(schema)}.{quote_ident_mssql(name)}"

    def columns(self, table: str) -> list[ColumnInfo]:
        schema, name = self._split_table(table)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=? AND TABLE_NAME=? ORDER BY ORDINAL_POSITION",
            (schema, name),
        )
        cols = [
            ColumnInfo(name=str(r[0]), type_name=str(r[1] or ""), nullable=str(r[2]).upper() == "YES")
            for r in cur.fetchall()
        ]
        pks = set(self._pk_columns(table))
        for col in cols:
            col.pk = col.name in pks
        return cols

    def _pk_columns(self, table: str) -> list[str]:
        schema, name = self._split_table(table)
        cur = self.conn.cursor()
        cur.execute(
            "SELECT kcu.COLUMN_NAME FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            "AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA "
            "WHERE tc.CONSTRAINT_TYPE='PRIMARY KEY' AND tc.TABLE_SCHEMA=? AND tc.TABLE_NAME=? "
            "ORDER BY kcu.ORDINAL_POSITION",
            (schema, name),
        )
        return [str(r[0]) for r in cur.fetchall()]

    def fetch_rows(self, table: str, offset: int = 0, limit: int = 200) -> QueryResult:
        ident = self._qtable(table)
        order = self._pk_columns(table)
        order_sql = ", ".join(quote_ident_mssql(c) for c in order) if order else "(SELECT NULL)"
        sql = (
            f"SELECT * FROM {ident} ORDER BY {order_sql} "
            f"OFFSET {int(offset)} ROWS FETCH NEXT {int(limit)} ROWS ONLY"
        )
        cur = self.conn.cursor()
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        rows = [list(row) for row in cur.fetchall()]
        return QueryResult(columns=names, rows=rows, rowcount=len(rows))

    def insert_row(self, table: str, values: dict[str, Any]) -> str:
        cols = {c.name: c.type_name for c in self.columns(table)}
        keys = [k for k in values if k in cols]
        if not keys:
            return "Error: no matching columns."
        idents = ", ".join(quote_ident_mssql(k) for k in keys)
        placeholders = ", ".join("?" for _ in keys)
        params = [_coerce(values[k], cols[k]) for k in keys]
        cur = self.conn.cursor()
        cur.execute(
            f"INSERT INTO {self._qtable(table)} ({idents}) VALUES ({placeholders})",
            params,
        )
        self.conn.commit()
        return f"Inserted 1 row into {table}."

    def update_row(self, table: str, pk: dict[str, Any], changes: dict[str, Any]) -> str:
        cols = {c.name: c.type_name for c in self.columns(table)}
        sets = [k for k in changes if k in cols]
        if not sets or not pk:
            return "Error: nothing to update."
        set_sql = ", ".join(f"{quote_ident_mssql(k)}=?" for k in sets)
        where_keys = list(pk.keys())
        where_sql = " AND ".join(f"{quote_ident_mssql(k)}=?" for k in where_keys)
        params = [_coerce(changes[k], cols.get(k, "")) for k in sets]
        params.extend(pk[k] for k in where_keys)
        cur = self.conn.cursor()
        cur.execute(
            f"UPDATE {self._qtable(table)} SET {set_sql} WHERE {where_sql}",
            params,
        )
        self.conn.commit()
        return f"Updated {cur.rowcount} row(s) in {table}."

    def delete_row(self, table: str, pk: dict[str, Any]) -> str:
        if not pk:
            return "Error: missing primary key."
        where_keys = list(pk.keys())
        where_sql = " AND ".join(f"{quote_ident_mssql(k)}=?" for k in where_keys)
        cur = self.conn.cursor()
        cur.execute(
            f"DELETE FROM {self._qtable(table)} WHERE {where_sql}",
            [pk[k] for k in where_keys],
        )
        self.conn.commit()
        return f"Deleted {cur.rowcount} row(s) from {table}."

    def execute(self, sql: str) -> QueryResult:
        text = (sql or "").strip()
        if not text:
            return QueryResult([], [], message="Error: empty SQL.")
        cur = self.conn.cursor()
        cur.execute(text)
        if cur.description:
            names = [d[0] for d in cur.description]
            rows = [list(row) for row in cur.fetchall()]
            self.conn.commit()
            return QueryResult(names, rows, len(rows))
        self.conn.commit()
        return QueryResult([], [], cur.rowcount, message=f"OK, {cur.rowcount} row(s) affected.")


def open_backend(spec: dict[str, Any]) -> DatabaseBackend:
    kind = str(spec.get("kind") or "").lower()
    if kind == "sqlite":
        return SqliteBackend(spec.get("path") or "")
    if kind in {"sqlserver", "mssql", "sql"}:
        return SqlServerBackend(spec)
    raise ValueError(f"Unknown database kind: {kind}")


def pk_map_for_row(
    columns: list[str],
    row: list[Any],
    pk_names: list[str],
) -> dict[str, Any]:
    index = {name: i for i, name in enumerate(columns)}
    out: dict[str, Any] = {}
    for name in pk_names:
        if name in index:
            out[name] = row[index[name]]
        elif name == "rowid" and "__rowid" in index:
            out["rowid"] = row[index["__rowid"]]
    return out
