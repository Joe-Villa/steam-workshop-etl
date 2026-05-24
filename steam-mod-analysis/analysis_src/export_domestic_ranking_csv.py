import argparse
import csv
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    from game_profile import default_db_path, default_result_dir

    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    parser = argparse.ArgumentParser(description="导出国模订阅排名 CSV。")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite 数据库路径（默认 data/table/mods.sqlite3）",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=default_result_dir(project_root) / "domestic_mods_ranking.csv",
        help="输出 CSV 路径（默认 data/result/domestic_mods_ranking.csv）",
    )
    ns = parser.parse_args()
    if ns.db_path is None:
        ns.db_path = default_db_path(project_root)
    return ns


def main() -> None:
    from data_paths import ensure_data_dirs

    ensure_data_dirs()
    args = parse_args()
    if not args.db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{args.db_path}")

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    query = """
    WITH all_ranked AS (
        SELECT
            m.mod_id,
            m.mod_name,
            m.subscribers,
            RANK() OVER (ORDER BY m.subscribers DESC) AS all_rank
        FROM mods m
    ),
    domestic_ranked AS (
        SELECT
            m.mod_id,
            m.mod_name,
            m.subscribers,
            RANK() OVER (ORDER BY m.subscribers DESC) AS domestic_rank
        FROM mods m
        JOIN mod_browse_info b ON b.mod_id = m.mod_id
        JOIN authors a ON a.username = b.author
        WHERE a.is_chinese = 1
    )
    SELECT
        d.mod_id,
        d.mod_name,
        d.subscribers,
        d.domestic_rank,
        a.all_rank
    FROM domestic_ranked d
    JOIN all_ranked a
      ON a.mod_id = d.mod_id
     AND a.mod_name = d.mod_name
    ORDER BY d.subscribers DESC, d.mod_id ASC, d.mod_name ASC;
    """

    with sqlite3.connect(args.db_path) as conn:
        rows = conn.execute(query).fetchall()

    headers = [
        "模组id",
        "模组名称",
        "订阅量",
        "在国模当中的排名顺位",
        "在所有模组当中的排名顺位",
    ]

    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"已导出：{args.output_csv}")
    print(f"总行数（不含表头）：{len(rows)}")


if __name__ == "__main__":
    main()
