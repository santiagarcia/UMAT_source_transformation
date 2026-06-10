from __future__ import annotations

from typing import Any


def status_row(label: str, status: str) -> dict[str, str]:
    return {"stage": label, "status": status}


def show_status_message(st_module, status: str, message: str) -> None:
    if status == "Complete":
        st_module.success(message)
    elif status == "Warning":
        st_module.warning(message)
    elif status == "Error":
        st_module.error(message)
    else:
        st_module.info(message)


def normalize_editor_rows(rows: Any) -> list[dict[str, object]]:
    if rows is None:
        return []
    if isinstance(rows, list):
        return [dict(row) for row in rows]
    if hasattr(rows, "to_dict"):
        records = rows.to_dict("records")
        return [dict(row) for row in records]
    return []


def merge_rows_by_key(
    existing: list[dict[str, object]], edited: list[dict[str, object]], key: str
) -> list[dict[str, object]]:
    edited_by_key = {str(row.get(key, "")): row for row in edited}
    merged = []
    for row in existing:
        row_key = str(row.get(key, ""))
        merged.append(dict(edited_by_key.get(row_key, row)))
    return merged
