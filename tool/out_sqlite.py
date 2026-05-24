import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_INPUT_DEFAULT = _ROOT / "HtmlBatchRunner" / "output" / "mod_fetch.sqlite"
_OUTPUT_DEFAULT = _ROOT / "HtmlBatchRunner" / "output"
OUTPUT_NAME = "out.xlsx"


def _quote_table(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _excel_sheet_name(raw: str, used: set[str]) -> str:
    # Excel: max 31 chars; forbidden: \ / * ? : [ ]
    s = re.sub(r'[\[\]:*?/\\]', "_", raw)
    if not s:
        s = "Sheet"
    if len(s) > 31:
        s = s[:31]
    base = s
    n = 1
    while s in used:
        suffix = f"_{n}"
        s = (base[: 31 - len(suffix)] + suffix)[:31]
        n += 1
    used.add(s)
    return s


def export_sqlite_to_xlsx(db_path: str, out_dir: str, out_name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, out_name)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        used_sheet_names: set[str] = set()

        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for table in tables:
                q = _quote_table(table)
                df = pd.read_sql_query(f"SELECT * FROM {q}", conn)
                sheet = _excel_sheet_name(table, used_sheet_names)
                df.to_excel(writer, sheet_name=sheet, index=False)
    finally:
        conn.close()

    return out_path


if __name__ == "__main__":
    path = export_sqlite_to_xlsx(str(_INPUT_DEFAULT), str(_OUTPUT_DEFAULT), OUTPUT_NAME)
    print(path)
