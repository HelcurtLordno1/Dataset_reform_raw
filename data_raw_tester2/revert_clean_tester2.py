#!/usr/bin/env python3
"""Readable, self-contained reference answer for Northwind tester 2.

The raw database is copied before repair. Each problem has one named solution.
Deterministic defects are transformed from raw values; information-destroying
or contextual defects are explicitly restored from supervised JSON labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import shutil
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

FOLDER = Path(__file__).resolve().parent
RAW_DATABASE = FOLDER / "northwind_tester2.db"
CLEAN_DATABASE = FOLDER / "northwind_cleaned2.db"
GROUND_TRUTH_FILE = FOLDER / "revert_clean_tester2.json"


class GroundTruthError(RuntimeError):
    """Input or output does not match the training ground truth."""


def load_ground_truth(path: Path = GROUND_TRUTH_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def quote_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def records_for(bundle: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [record for record in bundle["repair_records"] if record["rule_id"] == rule_id]


def primary_key_filter(primary_key: dict[str, Any]) -> tuple[str, list[Any]]:
    clause = " AND ".join(f"{quote_name(column)} IS ?" for column in primary_key)
    return clause, list(primary_key.values())


def find_row(connection: sqlite3.Connection, table: str,
             primary_key: dict[str, Any]) -> sqlite3.Row | None:
    where, parameters = primary_key_filter(primary_key)
    return connection.execute(
        f"SELECT * FROM {quote_name(table)} WHERE {where}", parameters
    ).fetchone()


def current_locator(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("corrupted_primary_key", record["primary_key"])


def count_candidates(connection: sqlite3.Connection, bundle: dict[str, Any],
                     rule_id: str) -> int:
    plan = next(item for item in bundle["preprocessing_plan"] if item["rule_id"] == rule_id)
    query = plan["detection"]["candidate_sql"].strip().rstrip(";")
    return connection.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]


def count_unresolved(connection: sqlite3.Connection, bundle: dict[str, Any],
                     rule_id: str) -> int:
    unresolved = 0
    for record in records_for(bundle, rule_id):
        row = find_row(connection, record["table"], record["primary_key"])
        if record["operation"] == "insert_row":
            unresolved += int(row is not None)
        else:
            unresolved += int(row is None or row[record["column"]] != record["clean_value"])
    return unresolved


def apply_transform(connection: sqlite3.Connection, bundle: dict[str, Any],
                    rule_id: str,
                    transform: Callable[[Any, dict[str, Any]], Any]) -> dict[str, Any]:
    """Derive clean values from raw values and verify them against labels."""
    before, updated = count_unresolved(connection, bundle, rule_id), 0
    for record in records_for(bundle, rule_id):
        locator = current_locator(record)
        row = find_row(connection, record["table"], locator)
        if row is None:
            raise GroundTruthError(f"Missing row for {rule_id}: {locator}")
        clean_value = transform(row[record["column"]], record)
        if clean_value != record["clean_value"]:
            raise GroundTruthError(
                f"{rule_id} derived {clean_value!r}, expected {record['clean_value']!r}")
        where, parameters = primary_key_filter(locator)
        connection.execute(
            f"UPDATE {quote_name(record['table'])} "
            f"SET {quote_name(record['column'])} = ? WHERE {where}",
            [clean_value, *parameters],
        )
        updated += 1
    return {"rule_id": rule_id, "before": before, "updated": updated,
            "after": count_unresolved(connection, bundle, rule_id)}


def restore_supervised_labels(connection: sqlite3.Connection,
                              bundle: dict[str, Any], rule_id: str,
                              reason: str) -> dict[str, Any]:
    """Restore exact values when raw data does not contain enough information."""
    before, updated = count_unresolved(connection, bundle, rule_id), 0
    for record in records_for(bundle, rule_id):
        locator = current_locator(record)
        where, parameters = primary_key_filter(locator)
        cursor = connection.execute(
            f"UPDATE {quote_name(record['table'])} "
            f"SET {quote_name(record['column'])} = ? WHERE {where}",
            [record["clean_value"], *parameters],
        )
        if cursor.rowcount != 1:
            raise GroundTruthError(f"Missing supervised row: {locator}")
        updated += 1
    return {"rule_id": rule_id, "method": "supervised_ground_truth",
            "reason": reason, "before": before, "updated": updated,
            "after": count_unresolved(connection, bundle, rule_id)}


def delete_injected_rows(connection: sqlite3.Connection, bundle: dict[str, Any],
                         rule_id: str) -> dict[str, Any]:
    before, deleted = count_unresolved(connection, bundle, rule_id), 0
    for record in records_for(bundle, rule_id):
        where, parameters = primary_key_filter(record["primary_key"])
        deleted += connection.execute(
            f"DELETE FROM {quote_name(record['table'])} WHERE {where}", parameters
        ).rowcount
    return {"rule_id": rule_id, "before": before, "deleted": deleted,
            "after": count_unresolved(connection, bundle, rule_id)}


def reverse_column_swap(connection: sqlite3.Connection, bundle: dict[str, Any],
                        rule_id: str, first: str, second: str) -> dict[str, Any]:
    """Swap two columns back once per affected row."""
    before = count_unresolved(connection, bundle, rule_id)
    primary_keys = {json.dumps(record["primary_key"], sort_keys=True): record["primary_key"]
                    for record in records_for(bundle, rule_id)}
    table = records_for(bundle, rule_id)[0]["table"]
    for primary_key in primary_keys.values():
        row = find_row(connection, table, primary_key)
        where, parameters = primary_key_filter(primary_key)
        connection.execute(
            f"UPDATE {quote_name(table)} SET {quote_name(first)} = ?, "
            f"{quote_name(second)} = ? WHERE {where}",
            [row[second], row[first], *parameters],
        )
    after = count_unresolved(connection, bundle, rule_id)
    if after:
        raise GroundTruthError(f"{rule_id} still has {after} unresolved labels")
    return {"rule_id": rule_id, "before": before,
            "rows_updated": len(primary_keys), "after": after}


# Named reference solutions -------------------------------------------------


def clean_duplicate_order(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Clone orders under new keys without matching details. Solution: Dùng ID anomaly kết hợp các trường nhận dạng order, sau đó chỉ xóa những insert_row được ground truth xác nhận."""
    return delete_injected_rows(connection, bundle, "T2-DUPLICATE-ORDER")


def clean_swapped_ship_location(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Swap city and country values within selected orders. Solution: Đổi lại hai cột trong cùng một UPDATE cho từng order bị ảnh hưởng và kiểm tra cả hai clean labels."""
    return reverse_column_swap(connection, bundle, "T2-SWAPPED-SHIP-LOCATION", "ShipCity", "ShipCountry")


def clean_swapped_customer_location(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Swap customer city and country fields. Solution: Hoán đổi City và Country nguyên tử trên đúng các customer rows đã được xác nhận."""
    return reverse_column_swap(connection, bundle, "T2-SWAPPED-CUSTOMER-LOCATION", "City", "Country")


def clean_orphan_customer(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Break Orders-to-Customers references. Solution: Phục hồi quan hệ CustomerID bằng supervised labels sau khi phát hiện bằng anti-join hoặc foreign_key_check."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-CUSTOMER", "Phục hồi quan hệ CustomerID bằng supervised labels sau khi phát hiện bằng anti-join hoặc foreign_key_check.")


def clean_orphan_employee(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Break Orders-to-Employees references. Solution: Dùng anti-join để phát hiện và supervised label để khôi phục employee assignment chính xác."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-EMPLOYEE", "Dùng anti-join để phát hiện và supervised label để khôi phục employee assignment chính xác.")


def clean_orphan_shipper(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Break Orders-to-Shippers references. Solution: Phát hiện bằng anti-join, sau đó phục hồi ShipVia gốc từ ground truth."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-SHIPPER", "Phát hiện bằng anti-join, sau đó phục hồi ShipVia gốc từ ground truth.")


def clean_orphan_detail_product(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Replace valid product keys with unique nonexistent keys. Solution: Dùng corrupted_primary_key để định vị raw row rồi phục hồi ProductID gốc từ supervised label."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-DETAIL-PRODUCT", "Dùng corrupted_primary_key để định vị raw row rồi phục hồi ProductID gốc từ supervised label.")


def clean_orphan_product_supplier(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Break Product-to-Supplier references. Solution: Phát hiện bằng anti-join và phục hồi supplier relationship từ ground truth."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-PRODUCT-SUPPLIER", "Phát hiện bằng anti-join và phục hồi supplier relationship từ ground truth.")


def clean_orphan_product_category(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Break Product-to-Category references. Solution: Phát hiện bằng anti-join và phục hồi category relationship từ ground truth."""
    return restore_supervised_labels(connection, bundle, "T2-ORPHAN-PRODUCT-CATEGORY", "Phát hiện bằng anti-join và phục hồi category relationship từ ground truth.")


def clean_required_before_order(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Set required dates before their order dates. Solution: Rule thời gian phát hiện lỗi nhưng không suy ra được ngày hẹn gốc; dùng supervised date để phục hồi chính xác."""
    return restore_supervised_labels(connection, bundle, "T2-REQUIRED-BEFORE-ORDER", "Rule thời gian phát hiện lỗi nhưng không suy ra được ngày hẹn gốc; dùng supervised date để phục hồi chính xác.")


def clean_shipped_before_order(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Set shipped dates before their order dates. Solution: Dùng comparison giữa hai timestamp để phát hiện và supervised label để phục hồi ngày giao thực tế."""
    return restore_supervised_labels(connection, bundle, "T2-SHIPPED-BEFORE-ORDER", "Dùng comparison giữa hai timestamp để phát hiện và supervised label để phục hồi ngày giao thực tế.")


def clean_negative_freight(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Create negative freight charges. Solution: Xác nhận Freight nhỏ hơn 0 rồi lấy trị tuyệt đối; phép sửa được suy ra trực tiếp từ raw."""
    def absolute_number(value: Any, _: dict[str, Any]) -> int | float:
        number = abs(value)
        return int(number) if isinstance(number, float) and number.is_integer() else number
    return apply_transform(connection, bundle, "T2-NEGATIVE-FREIGHT", absolute_number)


def clean_negative_quantity(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Create impossible negative order quantities. Solution: Lấy trị tuyệt đối nguyên của đúng affected rows và đối chiếu clean labels."""
    def absolute_integer(value: Any, _: dict[str, Any]) -> int:
        return abs(int(value))
    return apply_transform(connection, bundle, "T2-NEGATIVE-QUANTITY", absolute_integer)


def clean_discount_outside_range(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Convert fractional discounts into invalid whole percentages. Solution: Chia injected value cho 100 rồi kiểm tra kết quả nằm trong miền hợp lệ."""
    def divide_by_100(value: Any, _: dict[str, Any]) -> float:
        return float(value) / 100
    return apply_transform(connection, bundle, "T2-DISCOUNT-OUTSIDE-RANGE", divide_by_100)


def clean_negative_stock(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Create negative inventory levels. Solution: Phát hiện bằng điều kiện nhỏ hơn 0 và dùng supervised labels cho exact restoration."""
    return restore_supervised_labels(connection, bundle, "T2-NEGATIVE-STOCK", "Phát hiện bằng điều kiện nhỏ hơn 0 và dùng supervised labels cho exact restoration.")


PIPELINE = [
    clean_duplicate_order,
    clean_swapped_ship_location,
    clean_swapped_customer_location,
    clean_orphan_customer,
    clean_orphan_employee,
    clean_orphan_shipper,
    clean_orphan_detail_product,
    clean_orphan_product_supplier,
    clean_orphan_product_category,
    clean_required_before_order,
    clean_shipped_before_order,
    clean_negative_freight,
    clean_negative_quantity,
    clean_discount_outside_range,
    clean_negative_stock,
]


def primary_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({quote_name(table)})").fetchall()
    return [row["name"] for row in sorted(
        (row for row in rows if row["pk"]), key=lambda row: row["pk"])]


def table_fingerprint(connection: sqlite3.Connection, table: str) -> str:
    keys = primary_key_columns(connection, table)
    query = f"SELECT * FROM {quote_name(table)}"
    if keys:
        query += " ORDER BY " + ", ".join(quote_name(key) for key in keys)
    digest = hashlib.sha256()
    for row in connection.execute(query):
        values = [{"blob_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                  if isinstance(value, bytes) else value for value in row]
        line = json.dumps(values, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        digest.update(line.encode("utf-8") + b"\n")
    return digest.hexdigest()


def score_ground_truth(connection: sqlite3.Connection,
                       bundle: dict[str, Any]) -> dict[str, Any]:
    repaired = remaining = preserved = false_positive = 0
    for record in bundle["repair_records"]:
        row = find_row(connection, record["table"], record["primary_key"])
        if record["operation"] == "insert_row":
            repaired += int(row is None)
            remaining += int(row is not None)
        elif row is not None and row[record["column"]] == record["clean_value"]:
            repaired += 1
        else:
            remaining += 1
    for control in bundle["clean_controls"]:
        row = find_row(connection, control["table"], control["primary_key"])
        if row is not None and row[control["column"]] == control["clean_value"]:
            preserved += 1
        else:
            false_positive += 1
    total = repaired + remaining + preserved + false_positive
    return {"quality_score": round(100 * (repaired + preserved) / total, 4),
            "repaired_faults": repaired, "remaining_faults": remaining,
            "preserved_clean_controls": preserved,
            "false_positive_controls": false_positive}


def validate_connection(connection: sqlite3.Connection,
                        bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate from JSON fingerprints without any external database."""
    expected, table_results = bundle["validation_expectations"], {}
    for table, wanted in expected["tables"].items():
        rows = connection.execute(f"SELECT COUNT(*) FROM {quote_name(table)}").fetchone()[0]
        table_results[table] = {
            "rows": rows,
            "match": rows == wanted["rows"]
            and table_fingerprint(connection, table) == wanted["fingerprint"],
        }
    sequence = [dict(row) for row in connection.execute(
        "SELECT name, seq FROM sqlite_sequence ORDER BY name")]
    result = score_ground_truth(connection, bundle)
    result.update({
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
        "matching_tables": sum(item["match"] for item in table_results.values()),
        "table_count": len(table_results),
        "sqlite_sequence_match": sequence == expected["sqlite_sequence"],
    })
    result["passed"] = (
        result["quality_score"] == 100.0 and result["remaining_faults"] == 0
        and result["false_positive_controls"] == 0
        and result["integrity_check"] == "ok"
        and result["foreign_key_violations"] == 0
        and result["matching_tables"] == result["table_count"]
        and result["sqlite_sequence_match"])
    return result


def reset_sqlite_sequence(connection: sqlite3.Connection,
                          bundle: dict[str, Any]) -> None:
    connection.execute("DELETE FROM sqlite_sequence")
    connection.executemany("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
        [(item["name"], item["seq"])
         for item in bundle["validation_expectations"]["sqlite_sequence"]])


def build_clean_database(raw_path: Path, output_path: Path,
                         ground_truth_path: Path, *, force: bool = False) -> dict[str, Any]:
    bundle = load_ground_truth(ground_truth_path)
    if sha256_file(raw_path) != bundle["dataset"]["raw_sha256"]:
        raise GroundTruthError("Raw database checksum does not match this ground truth")
    if raw_path.resolve() == output_path.resolve():
        raise GroundTruthError("Output must not overwrite the raw database")
    if output_path.exists() and not force:
        raise GroundTruthError(f"Output already exists: {output_path}; use --force")
    descriptor, name = tempfile.mkstemp(
        prefix=".tester2_working_", suffix=".db", dir=output_path.parent)
    os.close(descriptor)
    temporary_path = Path(name)
    try:
        shutil.copy2(raw_path, temporary_path)
        connection = connect(temporary_path)
        try:
            connection.execute("BEGIN")
            reports = []
            for step, solution in enumerate(PIPELINE, start=1):
                rule_id = bundle["execution_order"][step - 1]
                candidates = count_candidates(connection, bundle, rule_id)
                report = solution(connection, bundle)
                report["candidates_detected"] = candidates
                reports.append(report)
                print(f"{step:02d}/15 {rule_id}: "
                      f"candidates={candidates}, remaining={report['after']}")
            reset_sqlite_sequence(connection, bundle)
            connection.commit()
            validation = validate_connection(connection, bundle)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if not validation["passed"]:
            raise GroundTruthError(f"Validation failed: {validation}")
        os.replace(temporary_path, output_path)
        return {"input": str(raw_path), "output": str(output_path),
                "output_sha256": sha256_file(output_path),
                "steps": reports, "validation": validation}
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_database(path: Path, ground_truth_path: Path) -> dict[str, Any]:
    bundle = load_ground_truth(ground_truth_path)
    connection = connect(path, read_only=True)
    try:
        return validate_connection(connection, bundle)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean Northwind tester 2 using supervised reference answers.")
    parser.add_argument("--input", type=Path, default=RAW_DATABASE)
    parser.add_argument("--output", type=Path, default=CLEAN_DATABASE)
    parser.add_argument("--ground-truth", type=Path, default=GROUND_TRUTH_FILE)
    parser.add_argument("--force", action="store_true", help="replace existing clean output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate_only:
            result = validate_database(args.output.resolve(), args.ground_truth.resolve())
        else:
            result = build_clean_database(args.input.resolve(), args.output.resolve(),
                                          args.ground_truth.resolve(), force=args.force)
        validation = result.get("validation", result)
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0 if validation["passed"] else 1
    except (GroundTruthError, FileNotFoundError, sqlite3.Error, ValueError) as error:
        print(f"ERROR: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
