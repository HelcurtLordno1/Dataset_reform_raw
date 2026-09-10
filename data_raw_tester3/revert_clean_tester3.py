#!/usr/bin/env python3
"""Readable, self-contained reference answer for Northwind tester 3.

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
RAW_DATABASE = FOLDER / "northwind_tester3.db"
CLEAN_DATABASE = FOLDER / "northwind_cleaned3.db"
GROUND_TRUTH_FILE = FOLDER / "revert_clean_tester3.json"


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


def clean_hidden_unicode_city(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Insert zero-width Unicode characters into city names. Solution: NFKC-normalize rồi loại bỏ zero-width và BOM; clean value được suy ra từ raw."""
    def remove_invisible_unicode(value: Any, _: dict[str, Any]) -> str:
        return unicodedata.normalize("NFKC", str(value)).replace("\u200b", "").replace("\ufeff", "")
    return apply_transform(connection, bundle, "T3-HIDDEN-UNICODE-CITY", remove_invisible_unicode)


def clean_hidden_unicode_unit(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Insert invisible characters into quantity/unit descriptions. Solution: NFKC-normalize và xóa các ký tự vô hình đã được nhận diện."""
    def remove_invisible_unicode(value: Any, _: dict[str, Any]) -> str:
        return unicodedata.normalize("NFKC", str(value)).replace("\u200b", "").replace("\ufeff", "")
    return apply_transform(connection, bundle, "T3-HIDDEN-UNICODE-UNIT", remove_invisible_unicode)


def clean_duplicate_customer(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Clone customers under different IDs so key-based deduplication is insufficient. Solution: Kết hợp ID anomaly với business identity rồi chỉ xóa exact injected rows."""
    return delete_injected_rows(connection, bundle, "T3-DUPLICATE-CUSTOMER")


def clean_typo_ship_name(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Create subtle adjacent-character typos in shipping entity names. Solution: Fuzzy matching tạo candidate; supervised canonical name quyết định đáp án cuối cùng."""
    return restore_supervised_labels(connection, bundle, "T3-TYPO-SHIP-NAME", "Fuzzy matching tạo candidate; supervised canonical name quyết định đáp án cuối cùng.")


def clean_coherent_wrong_shipping_block(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Move internally coherent address blocks to the wrong order entities. Solution: Đối chiếu ownership theo customer và ship identity, sau đó phục hồi toàn bộ affected fields bằng labels."""
    return restore_supervised_labels(connection, bundle, "T3-COHERENT-WRONG-SHIPPING-BLOCK", "Đối chiếu ownership theo customer và ship identity, sau đó phục hồi toàn bộ affected fields bằng labels.")


def clean_mixed_orphan_customer(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Mix explicit orphan entities into a dataset dominated by subtler errors. Solution: Anti-join phát hiện GHOST; ground truth phục hồi customer ownership chính xác."""
    return restore_supervised_labels(connection, bundle, "T3-MIXED-ORPHAN-CUSTOMER", "Anti-join phát hiện GHOST; ground truth phục hồi customer ownership chính xác.")


def clean_valid_wrong_supplier(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Assign products to different existing suppliers. Solution: Dùng semantic/reference knowledge để phát hiện và supervised labels để phục hồi supplier đúng."""
    return restore_supervised_labels(connection, bundle, "T3-VALID-WRONG-SUPPLIER", "Dùng semantic/reference knowledge để phát hiện và supervised labels để phục hồi supplier đúng.")


def clean_valid_wrong_category(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Assign products to different existing categories. Solution: Kiểm tra product-category semantics và phục hồi exact category từ labels."""
    return restore_supervised_labels(connection, bundle, "T3-VALID-WRONG-CATEGORY", "Kiểm tra product-category semantics và phục hồi exact category từ labels.")


def clean_plausible_customer_reassignment(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Reassign orders to other valid customers while retaining original shipping identities. Solution: Dùng bằng chứng chéo ShipName, address và geography; exact ownership lấy từ supervised labels."""
    return restore_supervised_labels(connection, bundle, "T3-PLAUSIBLE-CUSTOMER-REASSIGNMENT", "Dùng bằng chứng chéo ShipName, address và geography; exact ownership lấy từ supervised labels.")


def clean_plausible_employee_reassignment(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Rotate valid employee identifiers among orders. Solution: Đây là contextual problem; exact historical assignment phải dùng oracle labels."""
    return restore_supervised_labels(connection, bundle, "T3-PLAUSIBLE-EMPLOYEE-REASSIGNMENT", "Đây là contextual problem; exact historical assignment phải dùng oracle labels.")


def clean_plausible_shipper_reassignment(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Rotate valid shipper IDs without creating a foreign-key violation. Solution: Phân tích assignment pattern để phát hiện; phục hồi chính xác bằng supervised labels."""
    return restore_supervised_labels(connection, bundle, "T3-PLAUSIBLE-SHIPPER-REASSIGNMENT", "Phân tích assignment pattern để phát hiện; phục hồi chính xác bằng supervised labels.")


def clean_one_year_date_shift(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Shift valid ISO order timestamps one year while leaving related dates unchanged. Solution: Trừ chính xác 365 ngày và bảo toàn độ chính xác date/time của clean label."""
    def subtract_365_days(value: Any, record: dict[str, Any]) -> str:
        parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S") - timedelta(days=365)
        label = str(record["clean_value"])
        return parsed.strftime("%Y-%m-%d %H:%M:%S") if " " in label else parsed.date().isoformat()
    return apply_transform(connection, bundle, "T3-ONE-YEAR-DATE-SHIFT", subtract_365_days)


def clean_freight_scale_drift(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Apply a silent 100x unit-scale error to freight. Solution: Phát hiện scale cluster theo context, nhân affected values với 100 và làm tròn về precision chuẩn."""
    def multiply_by_100(value: Any, _: dict[str, Any]) -> int | float:
        number = round(float(value) * 100, 10)
        return int(number) if number.is_integer() else number
    return apply_transform(connection, bundle, "T3-FREIGHT-SCALE-DRIFT", multiply_by_100)


def clean_contextual_detail_price(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Rotate plausible prices among unrelated order lines, preserving type and global range. Solution: Dùng product/time context để tạo candidate; exact historical line price cần supervised labels."""
    return restore_supervised_labels(connection, bundle, "T3-CONTEXTUAL-DETAIL-PRICE", "Dùng product/time context để tạo candidate; exact historical line price cần supervised labels.")


def clean_contextual_detail_quantity(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Permute valid quantities across unrelated order lines. Solution: Univariate profiling chỉ cho dấu hiệu; exact row quantities phải được phục hồi từ labels."""
    return restore_supervised_labels(connection, bundle, "T3-CONTEXTUAL-DETAIL-QUANTITY", "Univariate profiling chỉ cho dấu hiệu; exact row quantities phải được phục hồi từ labels.")


def clean_discount_label_drift(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Reassign valid discount levels so univariate profiling remains plausible. Solution: Kiểm tra discount theo order context và dùng supervised labels cho exact repair."""
    return restore_supervised_labels(connection, bundle, "T3-DISCOUNT-LABEL-DRIFT", "Kiểm tra discount theo order context và dùng supervised labels cho exact repair.")


def clean_customer_contact_misalignment(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Move plausible contact identities and roles between customer entities. Solution: Validate identity fields như một nhóm và phục hồi cả pair bằng ground truth."""
    return restore_supervised_labels(connection, bundle, "T3-CUSTOMER-CONTACT-MISALIGNMENT", "Validate identity fields như một nhóm và phục hồi cả pair bằng ground truth.")


def clean_plausible_product_price(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem: Rotate valid product prices while preserving the aggregate distribution. Solution: Đối chiếu product identity và lịch sử; exact UnitPrice được phục hồi từ labels."""
    return restore_supervised_labels(connection, bundle, "T3-PLAUSIBLE-PRODUCT-PRICE", "Đối chiếu product identity và lịch sử; exact UnitPrice được phục hồi từ labels.")


PIPELINE = [
    clean_hidden_unicode_city,
    clean_hidden_unicode_unit,
    clean_duplicate_customer,
    clean_typo_ship_name,
    clean_coherent_wrong_shipping_block,
    clean_mixed_orphan_customer,
    clean_valid_wrong_supplier,
    clean_valid_wrong_category,
    clean_plausible_customer_reassignment,
    clean_plausible_employee_reassignment,
    clean_plausible_shipper_reassignment,
    clean_one_year_date_shift,
    clean_freight_scale_drift,
    clean_contextual_detail_price,
    clean_contextual_detail_quantity,
    clean_discount_label_drift,
    clean_customer_contact_misalignment,
    clean_plausible_product_price,
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
        prefix=".tester3_working_", suffix=".db", dir=output_path.parent)
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
                print(f"{step:02d}/18 {rule_id}: "
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
        description="Clean Northwind tester 3 using supervised reference answers.")
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
