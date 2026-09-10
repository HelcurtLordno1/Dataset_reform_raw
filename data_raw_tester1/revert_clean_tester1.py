#!/usr/bin/env python3
"""Readable reference answer for Northwind tester 1.

The script turns ``northwind_tester1.db`` into ``northwind_cleaned1.db``.
There is one solution function per data-quality problem. Deterministic defects
are fixed from raw values. When corruption destroyed information, the function
explicitly restores supervised labels from the adjacent JSON ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


FOLDER = Path(__file__).resolve().parent
RAW_DATABASE = FOLDER / "northwind_tester1.db"
CLEAN_DATABASE = FOLDER / "northwind_cleaned1.db"
GROUND_TRUTH_FILE = FOLDER / "revert_clean_tester1.json"

# Candidate queries explain how each situation is detected. Exact target rows
# remain in JSON so legitimate unusual values are not accidentally modified.
DETECTION_SQL = {
    "T1-TEXT-FREIGHT": """SELECT OrderID, Freight, typeof(Freight) AS storage_type
        FROM Orders WHERE typeof(Freight) = 'text'""",
    "T1-TEXT-PRODUCT-PRICE": """SELECT ProductID, ProductName, UnitPrice,
        typeof(UnitPrice) AS storage_type FROM Products
        WHERE typeof(UnitPrice) = 'text'""",
    "T1-PERCENT-DISCOUNT": """SELECT OrderID, ProductID, Discount,
        typeof(Discount) AS storage_type FROM "Order Details"
        WHERE typeof(Discount) = 'text'
        AND substr(trim(Discount), -1) = '%'""",
    "T1-MIXED-ORDER-DATE": """SELECT OrderID, OrderDate FROM Orders
        WHERE OrderDate NOT GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'""",
    "T1-PHONE-PLACEHOLDERS": """SELECT CustomerID, Phone FROM Customers
        WHERE lower(trim(Phone)) IN ('n/a', 'na', 'unknown', '-', '')""",
    "T1-WHITESPACE-SHIP-NAME": """SELECT OrderID, ShipName FROM Orders
        WHERE ShipName != trim(ShipName) OR ShipName LIKE '%  %'
        OR instr(ShipName, char(9)) > 0""",
    "T1-WHITESPACE-PRODUCT": """SELECT ProductID, ProductName FROM Products
        WHERE ProductName != trim(ProductName) OR ProductName LIKE '%  %'
        OR instr(ProductName, char(9)) > 0""",
    "T1-CASE-SHIP-CITY": """SELECT OrderID, ShipCity FROM Orders
        WHERE ShipCity = upper(ShipCity) OR ShipCity = lower(ShipCity)""",
    "T1-COUNTRY-ALIASES": """SELECT CustomerID, Country FROM Customers
        WHERE Country IN ('México','United Kingdom','sweden','DE','FRANCE ',
        'España','CA','switzerland','austria','Brasil','U.S.A.','Venez.',
        'belgium','portugal','argentina','italy','norway','denmark','finland','poland')""",
    "T1-MISSING-SHIP-POSTAL": """SELECT OrderID, CustomerID, ShipCity,
        ShipCountry, ShipPostalCode FROM Orders WHERE ShipPostalCode IS NULL""",
    "T1-QUANTITY-OUTLIER": """SELECT OrderID, ProductID, Quantity
        FROM "Order Details" WHERE Quantity >= 100""",
    "T1-DUPLICATE-CUSTOMER": """SELECT duplicate.CustomerID,
        duplicate.CompanyName, duplicate.ContactName
        FROM Customers AS duplicate
        WHERE duplicate.CustomerID GLOB 'D[0-9][0-9][0-9][0-9]'
        AND EXISTS (SELECT 1 FROM Customers AS original
            WHERE original.CustomerID != duplicate.CustomerID
            AND original.CompanyName = duplicate.CompanyName
            AND original.ContactName = duplicate.ContactName)""",
}

COUNTRY_ALIASES = {
    "México": "Mexico", "United Kingdom": "UK", "sweden": "Sweden",
    "DE": "Germany", "FRANCE ": "France", "España": "Spain",
    "CA": "Canada", "switzerland": "Switzerland", "austria": "Austria",
    "Brasil": "Brazil", "U.S.A.": "USA", "Venez.": "Venezuela",
    "belgium": "Belgium", "portugal": "Portugal", "argentina": "Argentina",
    "italy": "Italy", "norway": "Norway", "denmark": "Denmark",
    "finland": "Finland", "poland": "Poland",
}


class GroundTruthError(RuntimeError):
    """The input or repaired output does not match the training specification."""


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
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def quote_name(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def records_for(bundle: dict[str, Any], rule_id: str) -> list[dict[str, Any]]:
    return [record for record in bundle["repair_records"] if record["rule_id"] == rule_id]


def primary_key_filter(primary_key: dict[str, Any]) -> tuple[str, list[Any]]:
    clause = " AND ".join(f"{quote_name(column)} IS ?" for column in primary_key)
    return clause, list(primary_key.values())


def find_row(
    connection: sqlite3.Connection, table: str, primary_key: dict[str, Any]
) -> sqlite3.Row | None:
    where, parameters = primary_key_filter(primary_key)
    return connection.execute(
        f"SELECT * FROM {quote_name(table)} WHERE {where}", parameters
    ).fetchone()


def count_candidates(connection: sqlite3.Connection, rule_id: str) -> int:
    query = DETECTION_SQL[rule_id].strip().rstrip(";")
    return connection.execute(f"SELECT COUNT(*) FROM ({query})").fetchone()[0]


def count_unresolved(
    connection: sqlite3.Connection, bundle: dict[str, Any], rule_id: str
) -> int:
    unresolved = 0
    for record in records_for(bundle, rule_id):
        row = find_row(connection, record["table"], record["primary_key"])
        if record["operation"] == "insert_row":
            unresolved += int(row is not None)
        else:
            unresolved += int(row is None or row[record["column"]] != record["clean_value"])
    return unresolved


def apply_transform(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    rule_id: str,
    transform: Callable[[Any, dict[str, Any]], Any],
) -> dict[str, Any]:
    """Derive values from raw data and confirm them against supervised labels."""
    before = count_unresolved(connection, bundle, rule_id)
    updated = 0
    for record in records_for(bundle, rule_id):
        row = find_row(connection, record["table"], record["primary_key"])
        if row is None:
            raise GroundTruthError(f"Missing row for {rule_id}: {record['primary_key']}")
        raw_value = row[record["column"]]
        clean_value = transform(raw_value, record)
        if clean_value != record["clean_value"]:
            raise GroundTruthError(
                f"{rule_id}: derived {clean_value!r}, expected {record['clean_value']!r}"
            )
        where, parameters = primary_key_filter(record["primary_key"])
        connection.execute(
            f"UPDATE {quote_name(record['table'])} "
            f"SET {quote_name(record['column'])} = ? WHERE {where}",
            [clean_value, *parameters],
        )
        updated += 1
    return {"rule_id": rule_id, "before": before, "updated": updated,
            "after": count_unresolved(connection, bundle, rule_id)}


def restore_supervised_labels(
    connection: sqlite3.Connection,
    bundle: dict[str, Any],
    rule_id: str,
    reason: str,
) -> dict[str, Any]:
    """Restore values that cannot be uniquely inferred after information loss."""
    before = count_unresolved(connection, bundle, rule_id)
    updated = 0
    for record in records_for(bundle, rule_id):
        where, parameters = primary_key_filter(record["primary_key"])
        cursor = connection.execute(
            f"UPDATE {quote_name(record['table'])} "
            f"SET {quote_name(record['column'])} = ? WHERE {where}",
            [record["clean_value"], *parameters],
        )
        if cursor.rowcount != 1:
            raise GroundTruthError(f"Missing supervised row: {record['primary_key']}")
        updated += 1
    return {"rule_id": rule_id, "method": "supervised_ground_truth",
            "reason": reason, "before": before, "updated": updated,
            "after": count_unresolved(connection, bundle, rule_id)}


# Twelve readable reference solutions ---------------------------------------

def clean_text_freight(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 1: convert values such as 'USD 30.50' back to numbers."""
    def parse_currency(value: Any, _: dict[str, Any]) -> int | float:
        number = float(re.sub(r"[^0-9,.-]", "", str(value)).replace(",", "."))
        return int(number) if number.is_integer() else number
    return apply_transform(connection, bundle, "T1-TEXT-FREIGHT", parse_currency)


def clean_text_product_price(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 2: convert currency-prefixed product prices back to numbers."""
    def parse_currency(value: Any, _: dict[str, Any]) -> int | float:
        number = float(re.sub(r"[^0-9,.-]", "", str(value)).replace(",", "."))
        return int(number) if number.is_integer() else number
    return apply_transform(connection, bundle, "T1-TEXT-PRODUCT-PRICE", parse_currency)


def clean_percent_discount(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 3: convert values such as '15%' to the fraction 0.15."""
    def parse_percent(value: Any, _: dict[str, Any]) -> float:
        return float(str(value).strip().removesuffix("%")) / 100
    return apply_transform(connection, bundle, "T1-PERCENT-DISCOUNT", parse_percent)


def clean_mixed_order_dates(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 4: parse non-ISO dates; use labels only for discarded seconds."""
    def parse_date(value: Any, record: dict[str, Any]) -> str:
        raw, label = str(value), str(record["clean_value"])
        for date_format in ("%d/%m/%Y %H:%M", "%m-%d-%Y %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(raw, date_format)
            except ValueError:
                continue
            if "%S" in date_format:
                full = parsed.strftime("%Y-%m-%d %H:%M:%S")
                return full if " " in label else full[:10]
            retained = parsed.strftime("%Y-%m-%d %H:%M")
            if label == retained[:10] or label.startswith(retained):
                return label
            raise GroundTruthError(f"Date components disagree for {raw!r}")
        raise GroundTruthError(f"Unsupported date format: {raw!r}")
    return apply_transform(connection, bundle, "T1-MIXED-ORDER-DATE", parse_date)


def clean_phone_placeholders(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 5: restore phones replaced by missing-value placeholders."""
    return restore_supervised_labels(connection, bundle, "T1-PHONE-PLACEHOLDERS",
        "A placeholder reveals missingness, but not the original phone number.")


def clean_ship_name_whitespace(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 6: trim and collapse whitespace in shipping names."""
    return apply_transform(connection, bundle, "T1-WHITESPACE-SHIP-NAME",
        lambda value, _: " ".join(str(value).split()))


def clean_product_name_whitespace(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 7: trim and collapse whitespace in product names."""
    return apply_transform(connection, bundle, "T1-WHITESPACE-PRODUCT",
        lambda value, _: " ".join(str(value).split()))


def clean_ship_city_case(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 8: restore canonical city capitalization, including accents."""
    def canonical_case(value: Any, record: dict[str, Any]) -> str:
        canonical = str(record["clean_value"])
        if str(value).casefold() != canonical.casefold():
            raise GroundTruthError(f"Not a case-only difference: {value!r}")
        return canonical
    return apply_transform(connection, bundle, "T1-CASE-SHIP-CITY", canonical_case)


def clean_country_aliases(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 9: map country aliases to canonical dataset labels."""
    def map_country(value: Any, _: dict[str, Any]) -> str:
        if str(value) not in COUNTRY_ALIASES:
            raise GroundTruthError(f"Unknown country alias: {value!r}")
        return COUNTRY_ALIASES[str(value)]
    return apply_transform(connection, bundle, "T1-COUNTRY-ALIASES", map_country)


def clean_missing_ship_postal_codes(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 10: restore postal codes that were replaced by NULL."""
    return restore_supervised_labels(connection, bundle, "T1-MISSING-SHIP-POSTAL",
        "NULL does not contain the removed postal code; exact labels are required.")


def clean_quantity_outliers(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 11: undo injected quantities that were multiplied by 100."""
    def divide_by_100(value: Any, _: dict[str, Any]) -> int:
        number = int(value)
        if number % 100:
            raise GroundTruthError(f"Not an injected 100x quantity: {value!r}")
        return number // 100
    return apply_transform(connection, bundle, "T1-QUANTITY-OUTLIER", divide_by_100)


def clean_duplicate_customers(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Problem 12: remove only customer clones recorded as injected rows."""
    rule_id = "T1-DUPLICATE-CUSTOMER"
    before, deleted = count_unresolved(connection, bundle, rule_id), 0
    for record in records_for(bundle, rule_id):
        where, parameters = primary_key_filter(record["primary_key"])
        deleted += connection.execute(
            f"DELETE FROM {quote_name(record['table'])} WHERE {where}", parameters
        ).rowcount
    return {"rule_id": rule_id, "before": before, "deleted": deleted,
            "after": count_unresolved(connection, bundle, rule_id)}


PIPELINE = [
    clean_text_freight, clean_text_product_price, clean_percent_discount,
    clean_mixed_order_dates, clean_phone_placeholders,
    clean_ship_name_whitespace, clean_product_name_whitespace,
    clean_ship_city_case, clean_country_aliases,
    clean_missing_ship_postal_codes, clean_quantity_outliers,
    clean_duplicate_customers,
]


# Self-contained validation --------------------------------------------------

def primary_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = connection.execute(f"PRAGMA table_info({quote_name(table)})").fetchall()
    return [row["name"] for row in sorted(
        (row for row in columns if row["pk"]), key=lambda row: row["pk"])]


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


def score_ground_truth(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
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


def validate_connection(connection: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    """Prove the result from JSON fingerprints; no external database is needed."""
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


def reset_sqlite_sequence(connection: sqlite3.Connection, bundle: dict[str, Any]) -> None:
    connection.execute("DELETE FROM sqlite_sequence")
    connection.executemany("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
        [(item["name"], item["seq"])
         for item in bundle["validation_expectations"]["sqlite_sequence"]])


def build_clean_database(raw_path: Path, output_path: Path,
                         ground_truth_path: Path, *, force: bool = False) -> dict[str, Any]:
    """Copy raw, apply all twelve answers, validate, and publish clean output."""
    bundle = load_ground_truth(ground_truth_path)
    if sha256_file(raw_path) != bundle["dataset"]["raw_sha256"]:
        raise GroundTruthError("Raw database checksum does not match this ground truth")
    if raw_path.resolve() == output_path.resolve():
        raise GroundTruthError("Output must not overwrite the raw database")
    if output_path.exists() and not force:
        raise GroundTruthError(f"Output already exists: {output_path}; use --force")

    descriptor, name = tempfile.mkstemp(
        prefix=".tester1_working_", suffix=".db", dir=output_path.parent)
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
                candidates = count_candidates(connection, rule_id)
                report = solution(connection, bundle)
                report["candidates_detected"] = candidates
                reports.append(report)
                print(f"{step:02d}/12 {rule_id}: candidates={candidates}, remaining={report['after']}")
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
        description="Clean Northwind tester 1 using the supervised reference answers.")
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
