#!/usr/bin/env python3
"""Build, restore, profile, and score the Northwind cleaning benchmarks.

The implementation deliberately uses only Python's standard library.  The two
protected source files are verified before every operation that can create an
output.  The clean baseline is never opened in write mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "northwind.db"
PROTECTED_FILE = ROOT / "protected_assets.json"
SCENARIOS_FILE = ROOT / "benchmark_scenarios.json"
SCHEMA_VERSION = "1.0"


class BenchmarkError(RuntimeError):
    """Raised for a controlled benchmark failure."""


def q(identifier: str) -> str:
    """Quote an SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any, *, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=False, indent=indent)
            stream.write("\n")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def verify_protected_assets() -> dict[str, Any]:
    policy = load_json(PROTECTED_FILE)
    problems: list[str] = []
    for item in policy["assets"]:
        path = ROOT / item["path"]
        if not path.is_file():
            problems.append(f"missing protected file: {item['path']}")
            continue
        actual = sha256_file(path)
        if actual != item["sha256"]:
            problems.append(
                f"protected checksum mismatch: {item['path']} expected={item['sha256']} actual={actual}"
            )
    if problems:
        raise BenchmarkError("\n".join(problems))
    return policy


def tester_dir(tester: int) -> Path:
    if tester not in (1, 2, 3):
        raise BenchmarkError("tester must be 1, 2, or 3")
    return ROOT / f"data_raw_tester{tester}"


def load_scenario(tester: int) -> dict[str, Any]:
    return load_json(SCENARIOS_FILE)["testers"][str(tester)]


def raw_db_path(tester: int) -> Path:
    return tester_dir(tester) / f"northwind_tester{tester}.db"


def cleaned_db_path(tester: int) -> Path:
    return tester_dir(tester) / f"northwind_cleaned{tester}.db"


def manifest_path(tester: int) -> Path:
    return tester_dir(tester) / f"revert_clean_tester{tester}.json"


def repair_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return manifest.get("repair_records", manifest.get("operations", []))


def primary_key_columns(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({q(table)})").fetchall()
    cols = [r for r in rows if r["pk"]]
    cols.sort(key=lambda r: r["pk"])
    if not cols:
        raise BenchmarkError(f"table {table!r} has no primary key")
    return [r["name"] for r in cols]


def table_columns(con: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return con.execute(f"PRAGMA table_info({q(table)})").fetchall()


def pk_dict(row: sqlite3.Row | dict[str, Any], pk_cols: Sequence[str]) -> dict[str, Any]:
    return {col: row[col] for col in pk_cols}


def pk_token(table: str, pk: dict[str, Any]) -> str:
    return table + "|" + canonical_json(pk)


def where_pk(pk: dict[str, Any]) -> tuple[str, list[Any]]:
    return " AND ".join(f"{q(col)} IS ?" for col in pk), list(pk.values())


def reservoir_rows(
    con: sqlite3.Connection,
    table: str,
    selected_columns: Sequence[str],
    where: str,
    count: int,
    rng: random.Random,
    unavailable: set[tuple[str, str, str]],
    mutation_columns: Sequence[str],
) -> list[dict[str, Any]]:
    """Deterministically sample eligible rows without ORDER BY RANDOM()."""
    pk_cols = primary_key_columns(con, table)
    wanted = list(dict.fromkeys([*pk_cols, *selected_columns]))
    sql = f"SELECT {', '.join(q(c) for c in wanted)} FROM {q(table)}"
    if where.strip():
        sql += f" WHERE {where}"
    sql += " ORDER BY " + ", ".join(q(c) for c in pk_cols)
    sample: list[dict[str, Any]] = []
    eligible_seen = 0
    for raw in con.execute(sql):
        row = dict(raw)
        pk = pk_dict(row, pk_cols)
        token = pk_token(table, pk)
        if any((table, token, col) in unavailable for col in mutation_columns):
            continue
        eligible_seen += 1
        if len(sample) < count:
            sample.append(row)
        else:
            idx = rng.randrange(eligible_seen)
            if idx < count:
                sample[idx] = row
    if len(sample) < count:
        raise BenchmarkError(
            f"rule requested {count} rows from {table}, but only {len(sample)} were eligible"
        )
    sample.sort(key=lambda r: canonical_json(pk_dict(r, pk_cols)))
    return sample


def parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def typo(value: str) -> str:
    if len(value) < 2:
        return value + "?"
    pos = max(0, min(len(value) - 2, len(value) // 2 - 1))
    chars = list(value)
    chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
    candidate = "".join(chars)
    return candidate if candidate != value else value + "?"


class MutationEngine:
    def __init__(self, con: sqlite3.Connection, scenario: dict[str, Any]):
        self.con = con
        self.scenario = scenario
        self.rng = random.Random(int(scenario["seed"]))
        self.operations: list[dict[str, Any]] = []
        self.used: set[tuple[str, str, str]] = set()
        self.rule_stats: list[dict[str, Any]] = []
        self.op_counter = 0

    def record_update(
        self,
        rule: dict[str, Any],
        table: str,
        pk: dict[str, Any],
        column: str,
        clean_value: Any,
        dirty_value: Any,
    ) -> bool:
        if clean_value == dirty_value:
            return False
        clause, params = where_pk(pk)
        cur = self.con.execute(
            f"UPDATE {q(table)} SET {q(column)}=? WHERE {clause}",
            [dirty_value, *params],
        )
        if cur.rowcount != 1:
            raise BenchmarkError(f"update did not identify exactly one row: {table} {pk}")
        self.op_counter += 1
        self.operations.append(
            {
                "operation_id": f"{self.scenario['id']}-OP-{self.op_counter:06d}",
                "operation": "update_cell",
                "rule_id": rule["rule_id"],
                "table": table,
                "primary_key": pk,
                "column": column,
                "clean_value": clean_value,
                "corrupted_value": dirty_value,
                "error_type": rule["error_type"],
                "severity": rule["severity"],
                "expected_action": rule["expected_action"],
                "recoverability": rule["recoverability"],
            }
        )
        pk_cols = primary_key_columns(self.con, table)
        if column in pk_cols:
            corrupted_pk = dict(pk)
            corrupted_pk[column] = dirty_value
            self.operations[-1]["corrupted_primary_key"] = corrupted_pk
            # A clean-control assertion cannot address another cell in this row
            # by the original key while that key is corrupted.
            self.used.add((table, pk_token(table, pk), "*"))
        self.used.add((table, pk_token(table, pk), column))
        return True

    def record_insert(
        self,
        rule: dict[str, Any],
        table: str,
        row: dict[str, Any],
        pk_cols: Sequence[str],
    ) -> None:
        cols = list(row)
        self.con.execute(
            f"INSERT INTO {q(table)} ({', '.join(q(c) for c in cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
        pk = {c: row[c] for c in pk_cols}
        self.op_counter += 1
        self.operations.append(
            {
                "operation_id": f"{self.scenario['id']}-OP-{self.op_counter:06d}",
                "operation": "insert_row",
                "rule_id": rule["rule_id"],
                "table": table,
                "primary_key": pk,
                "column": None,
                "clean_value": None,
                "corrupted_value": row,
                "error_type": rule["error_type"],
                "severity": rule["severity"],
                "expected_action": rule["expected_action"],
                "recoverability": rule["recoverability"],
            }
        )

    def transform_value(
        self,
        transform: str,
        value: Any,
        row: dict[str, Any],
        index: int,
        rule: dict[str, Any],
    ) -> Any:
        params = rule.get("params", {})
        if transform == "null":
            return None
        if transform == "placeholder":
            values = params.get("values", ["N/A", "unknown", "-"])
            return values[index % len(values)]
        if transform == "pad_whitespace":
            return "  " + str(value).replace(" ", "  ") + "   "
        if transform == "case_noise":
            return str(value).lower() if index % 2 == 0 else str(value).upper()
        if transform == "date_format":
            dt = parse_iso(str(value))
            patterns = ["%d/%m/%Y %H:%M", "%m-%d-%Y %H:%M:%S", "%Y/%m/%d %H:%M"]
            return dt.strftime(patterns[index % len(patterns)])
        if transform == "prefixed_numeric":
            prefix = params.get("prefix", "USD ")
            return prefix + str(value)
        if transform == "multiply":
            factor = params.get("factor", 100)
            result = float(value) * factor
            return int(result) if isinstance(value, int) and result.is_integer() else result
        if transform == "add":
            increment = params.get("increment", 100000)
            result = float(value) + increment
            return int(result) if isinstance(value, int) and result.is_integer() else result
        if transform == "scale":
            factor = float(params.get("factor", 0.01))
            return float(value) * factor
        if transform == "percent_string":
            return f"{float(value) * 100:g}%"
        if transform == "country_alias":
            aliases = {
                "USA": "U.S.A.", "UK": "United Kingdom", "Germany": "DE",
                "France": "FRANCE ", "Brazil": "Brasil", "Mexico": "México",
                "Venezuela": "Venez.", "Spain": "España", "Canada": "CA",
            }
            return aliases.get(str(value), str(value).lower())
        if transform == "negative":
            number = float(value)
            result = -abs(number) if number else -1
            return int(result) if isinstance(value, int) else result
        if transform == "date_before":
            source = parse_iso(str(row[params["source_column"]]))
            return (source - timedelta(days=int(params.get("days", 30)))).strftime("%Y-%m-%d %H:%M:%S")
        if transform == "date_shift":
            source = parse_iso(str(value))
            return (source + timedelta(days=int(params.get("days", 365)))).strftime("%Y-%m-%d %H:%M:%S")
        if transform == "invalid_fk":
            return params["value"]
        if transform == "zero_width":
            text = str(value)
            pos = max(1, len(text) // 2)
            return text[:pos] + "\u200b" + text[pos:]
        if transform == "typo":
            return typo(str(value))
        raise BenchmarkError(f"unknown transform: {transform}")

    def apply_standard_rule(self, rule: dict[str, Any]) -> int:
        table = rule["table"]
        columns = rule["columns"]
        extra = rule.get("extra_columns", [])
        rows = reservoir_rows(
            self.con,
            table,
            [*columns, *extra],
            rule.get("where", ""),
            int(rule["count"]),
            self.rng,
            self.used,
            columns,
        )
        pk_cols = primary_key_columns(self.con, table)
        applied = 0
        for idx, row in enumerate(rows):
            pk = pk_dict(row, pk_cols)
            for column in columns:
                dirty = self.transform_value(rule["transform"], row[column], row, idx, rule)
                applied += int(self.record_update(rule, table, pk, column, row[column], dirty))
        return applied

    def apply_swap_columns(self, rule: dict[str, Any]) -> int:
        table = rule["table"]
        left, right = rule["columns"]
        rows = reservoir_rows(
            self.con, table, [left, right], rule.get("where", ""), int(rule["count"]),
            self.rng, self.used, [left, right]
        )
        pk_cols = primary_key_columns(self.con, table)
        applied = 0
        for row in rows:
            pk = pk_dict(row, pk_cols)
            if row[left] == row[right]:
                continue
            applied += int(self.record_update(rule, table, pk, left, row[left], row[right]))
            applied += int(self.record_update(rule, table, pk, right, row[right], row[left]))
        return applied

    def apply_rotate(self, rule: dict[str, Any]) -> int:
        table = rule["table"]
        columns = rule["columns"]
        rows = reservoir_rows(
            self.con, table, columns, rule.get("where", ""), int(rule["count"]),
            self.rng, self.used, columns
        )
        if len(rows) < 2:
            return 0
        pk_cols = primary_key_columns(self.con, table)
        applied = 0
        shift = int(rule.get("params", {}).get("shift", 1)) % len(rows) or 1
        for idx, row in enumerate(rows):
            donor = rows[(idx + shift) % len(rows)]
            pk = pk_dict(row, pk_cols)
            for column in columns:
                dirty = donor[column]
                if dirty == row[column]:
                    donor = rows[(idx + shift + 1) % len(rows)]
                    dirty = donor[column]
                applied += int(self.record_update(rule, table, pk, column, row[column], dirty))
        return applied

    def apply_duplicate_rows(self, rule: dict[str, Any]) -> int:
        table = rule["table"]
        columns = [r["name"] for r in table_columns(self.con, table)]
        rows = reservoir_rows(
            self.con, table, columns, rule.get("where", ""), int(rule["count"]),
            self.rng, self.used, []
        )
        pk_cols = primary_key_columns(self.con, table)
        if len(pk_cols) != 1:
            raise BenchmarkError("duplicate_rows currently supports a single-column primary key")
        pk_col = pk_cols[0]
        declared = next(r["type"].upper() for r in table_columns(self.con, table) if r["name"] == pk_col)
        base = int(rule.get("params", {}).get("new_pk_base", 900000))
        prefix = str(rule.get("params", {}).get("new_pk_prefix", "Z"))
        applied = 0
        for idx, source in enumerate(rows, 1):
            clone = dict(source)
            clone[pk_col] = base + idx if "INT" in declared else f"{prefix}{idx:04d}"
            self.record_insert(rule, table, clone, pk_cols)
            applied += 1
        return applied

    def apply(self) -> None:
        for rule in self.scenario["rules"]:
            before = len(self.operations)
            transform = rule["transform"]
            if transform == "swap_columns":
                self.apply_swap_columns(rule)
            elif transform == "rotate_values":
                self.apply_rotate(rule)
            elif transform == "duplicate_rows":
                self.apply_duplicate_rows(rule)
            else:
                self.apply_standard_rule(rule)
            actual = len(self.operations) - before
            self.rule_stats.append(
                {
                    "rule_id": rule["rule_id"],
                    "description": rule["description"],
                    "requested_rows": int(rule["count"]),
                    "generated_assertions": actual,
                    "expected_action": rule["expected_action"],
                    "severity": rule["severity"],
                }
            )


def iter_control_candidates(
    con: sqlite3.Connection,
    scope: Sequence[dict[str, Any]],
    used: set[tuple[str, str, str]],
) -> Iterator[dict[str, Any]]:
    for item in scope:
        table = item["table"]
        columns = item["columns"]
        pk_cols = primary_key_columns(con, table)
        selected = list(dict.fromkeys([*pk_cols, *columns]))
        sql = f"SELECT {', '.join(q(c) for c in selected)} FROM {q(table)} ORDER BY " + ", ".join(q(c) for c in pk_cols)
        for raw in con.execute(sql):
            row = dict(raw)
            pk = pk_dict(row, pk_cols)
            token = pk_token(table, pk)
            for column in columns:
                if (table, token, column) in used or (table, token, "*") in used:
                    continue
                yield {"table": table, "primary_key": pk, "column": column, "clean_value": row[column]}


def generate_clean_controls(
    baseline: sqlite3.Connection,
    scenario: dict[str, Any],
    used: set[tuple[str, str, str]],
    mutation_assertions: int,
) -> list[dict[str, Any]]:
    target = float(scenario["target_quality_score"])
    wanted = int(round(mutation_assertions * target / (100.0 - target)))
    rng = random.Random(int(scenario["seed"]) + 9173)
    reservoir: list[dict[str, Any]] = []
    seen = 0
    for candidate in iter_control_candidates(baseline, scenario["control_scope"], used):
        seen += 1
        if len(reservoir) < wanted:
            reservoir.append(candidate)
        else:
            idx = rng.randrange(seen)
            if idx < wanted:
                reservoir[idx] = candidate
    if len(reservoir) < wanted:
        raise BenchmarkError(f"not enough clean controls: wanted={wanted}, available={len(reservoir)}")
    for idx, item in enumerate(reservoir, 1):
        item["control_id"] = f"{scenario['id']}-CTRL-{idx:06d}"
    return reservoir


def database_objects(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    ]


def row_count(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])


def sqlite_sequences(con: sqlite3.Connection) -> list[dict[str, Any]]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone()
    if not exists:
        return []
    return [dict(row) for row in con.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name")]


def table_fingerprint(con: sqlite3.Connection, table: str) -> str:
    pk_cols = primary_key_columns(con, table)
    digest = hashlib.sha256()
    sql = f"SELECT * FROM {q(table)} ORDER BY " + ", ".join(q(c) for c in pk_cols)
    cur = con.execute(sql)
    for row in cur:
        normalized: list[Any] = []
        for value in row:
            if isinstance(value, bytes):
                normalized.append({"blob_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)})
            else:
                normalized.append(value)
        digest.update(canonical_json(normalized).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def profile_database(path: Path, *, scenario: dict[str, Any] | None = None) -> dict[str, Any]:
    con = connect(path, readonly=True)
    try:
        tables = [r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        target_map: dict[str, set[str]] = {}
        if scenario:
            for rule in scenario["rules"]:
                for column in rule.get("columns", []):
                    target_map.setdefault(rule["table"], set()).add(column)
        result: dict[str, Any] = {
            "file": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(con.execute("PRAGMA foreign_key_check").fetchall()),
            "object_counts": {},
            "tables": {},
        }
        for kind, count in con.execute(
            "SELECT type,COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' GROUP BY type"
        ):
            result["object_counts"][kind] = count
        for table in tables:
            n = row_count(con, table)
            entry: dict[str, Any] = {
                "rows": n,
                "primary_key": primary_key_columns(con, table),
                "fingerprint": table_fingerprint(con, table),
                "columns": {},
            }
            selected = target_map.get(table, set())
            for col in table_columns(con, table):
                name = col["name"]
                cdata: dict[str, Any] = {
                    "declared_type": col["type"],
                    "not_null": bool(col["notnull"]),
                    "null_count": int(con.execute(
                        f"SELECT COUNT(*) FROM {q(table)} WHERE {q(name)} IS NULL"
                    ).fetchone()[0]),
                }
                if name in selected:
                    cdata["storage_types"] = {
                        typ: count for typ, count in con.execute(
                            f"SELECT typeof({q(name)}),COUNT(*) FROM {q(table)} GROUP BY typeof({q(name)})"
                        )
                    }
                    cdata["distinct_count"] = int(con.execute(
                        f"SELECT COUNT(DISTINCT {q(name)}) FROM {q(table)}"
                    ).fetchone()[0])
                entry["columns"][name] = cdata
            result["tables"][table] = entry
        return result
    finally:
        con.close()


def fetch_by_pk(con: sqlite3.Connection, table: str, pk: dict[str, Any]) -> sqlite3.Row | None:
    clause, params = where_pk(pk)
    return con.execute(f"SELECT * FROM {q(table)} WHERE {clause}", params).fetchone()


def current_cell(con: sqlite3.Connection, op: dict[str, Any]) -> tuple[bool, Any]:
    row = fetch_by_pk(con, op["table"], op["primary_key"])
    if row is None and op.get("corrupted_primary_key"):
        row = fetch_by_pk(con, op["table"], op["corrupted_primary_key"])
    if row is None:
        return False, None
    if op["operation"] == "insert_row":
        return True, dict(row)
    return True, row[op["column"]]


def score_candidate(candidate: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    con = connect(candidate, readonly=True)
    try:
        repaired = 0
        remaining = 0
        missing_rows = 0
        for op in repair_records(manifest):
            exists, current = current_cell(con, op)
            if op["operation"] == "insert_row":
                if not exists:
                    repaired += 1
                else:
                    remaining += 1
                continue
            if not exists:
                missing_rows += 1
                remaining += 1
            elif current == op["clean_value"]:
                repaired += 1
            else:
                remaining += 1
        preserved = 0
        false_positive = 0
        for control in manifest["clean_controls"]:
            row = fetch_by_pk(con, control["table"], control["primary_key"])
            if row is not None and row[control["column"]] == control["clean_value"]:
                preserved += 1
            else:
                false_positive += 1
        total = repaired + remaining + preserved + false_positive
        passed = repaired + preserved
        return {
            "candidate": str(candidate),
            "quality_score": round(100.0 * passed / total, 4) if total else 100.0,
            "repair_accuracy": round(100.0 * repaired / (repaired + remaining), 4) if repaired + remaining else 100.0,
            "clean_control_preservation": round(100.0 * preserved / (preserved + false_positive), 4) if preserved + false_positive else 100.0,
            "repaired_fault_assertions": repaired,
            "remaining_fault_assertions": remaining,
            "preserved_clean_controls": preserved,
            "false_positive_controls": false_positive,
            "missing_expected_rows": missing_rows,
            "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(con.execute("PRAGMA foreign_key_check").fetchall()),
        }
    finally:
        con.close()


def logical_compare(left: Path, right: Path) -> dict[str, Any]:
    a = connect(left, readonly=True)
    b = connect(right, readonly=True)
    try:
        objects_a = database_objects(a)
        objects_b = database_objects(b)
        schema_match = objects_a == objects_b
        sequences_a = sqlite_sequences(a)
        sequences_b = sqlite_sequences(b)
        sequence_match = sequences_a == sequences_b
        tables = [o["name"] for o in objects_a if o["type"] == "table"]
        details: dict[str, Any] = {}
        all_match = schema_match and sequence_match
        for table in tables:
            ac, bc = row_count(a, table), row_count(b, table)
            ah, bh = table_fingerprint(a, table), table_fingerprint(b, table)
            same = ac == bc and ah == bh
            details[table] = {
                "match": same,
                "left_rows": ac,
                "right_rows": bc,
                "left_fingerprint": ah,
                "right_fingerprint": bh,
            }
            all_match = all_match and same
        return {
            "logical_match": all_match,
            "schema_match": schema_match,
            "sqlite_sequence_match": sequence_match,
            "left_sqlite_sequence": sequences_a,
            "right_sqlite_sequence": sequences_b,
            "tables": details,
        }
    finally:
        a.close()
        b.close()


def build_tester(tester: int, *, force: bool) -> dict[str, Any]:
    verify_protected_assets()
    scenario = load_scenario(tester)
    raw = raw_db_path(tester)
    clean = cleaned_db_path(tester)
    if not manifest_path(tester).exists():
        raise BenchmarkError("the learnable ground-truth JSON is required as a build template")
    learning_bundle = load_json(manifest_path(tester))
    for path in (raw, clean):
        if path.exists() and not force:
            raise BenchmarkError(f"refusing to overwrite {path}; pass --force")
    raw.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".northwind_tester{tester}.", suffix=".db", dir=raw.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(BASELINE, tmp)
        con = connect(tmp)
        try:
            con.execute("PRAGMA foreign_keys=OFF")
            # SQLite still enforces CHECK constraints when foreign-key checks are
            # disabled.  Raw import files may violate declared domains while the
            # schema itself must remain identical to the baseline.
            con.execute("PRAGMA ignore_check_constraints=ON")
            con.execute("BEGIN IMMEDIATE")
            engine = MutationEngine(con, scenario)
            engine.apply()
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        base_con = connect(BASELINE, readonly=True)
        try:
            controls = generate_clean_controls(
                base_con, scenario, engine.used, len(engine.operations)
            )
            baseline_sequences = sqlite_sequences(base_con)
        finally:
            base_con.close()
        manifest = learning_bundle
        manifest["dataset"].update({
            "benchmark_id": scenario["id"], "tester": tester,
            "difficulty": scenario["difficulty"], "seed": scenario["seed"],
            "raw_database": raw.name, "clean_output": clean.name,
            "target_raw_quality": scenario["target_quality_score"],
            "baseline_sha256": sha256_file(BASELINE),
        })
        manifest["repair_records"] = engine.operations
        manifest["clean_controls"] = controls
        manifest["validation_expectations"]["sqlite_sequence"] = baseline_sequences
        by_rule = {rule["rule_id"]: [] for rule in scenario["rules"]}
        for operation in engine.operations:
            by_rule[operation["rule_id"]].append(operation)
        for plan in manifest["preprocessing_plan"]:
            values = by_rule[plan["rule_id"]]
            plan["affected_assertions"] = len(values)
            plan["examples"] = [
                {
                    "primary_key": op["primary_key"], "column": op.get("column"),
                    "raw_value": op["corrupted_value"], "clean_value": op["clean_value"],
                }
                for op in values[:3]
            ]
        os.replace(tmp, raw)
        manifest["dataset"]["raw_sha256"] = sha256_file(raw)
        atomic_json(manifest_path(tester), manifest, indent=2)
        restore_tester(tester, input_path=raw, output_path=clean, force=True, verify_assets=False)
        raw_score = score_candidate(raw, manifest)
        clean_score = score_candidate(clean, manifest)
        comparison = logical_compare(BASELINE, clean)
        raw_profile = profile_database(raw, scenario=scenario)
        report = {
            "benchmark_id": scenario["id"],
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "target_quality_score": scenario["target_quality_score"],
            "raw_score": raw_score,
            "reference_clean_score": clean_score,
            "reference_clean_vs_baseline": comparison,
            "raw_profile": raw_profile,
            "rule_statistics": engine.rule_stats,
            "artifact_hashes": {raw.name: sha256_file(raw), clean.name: sha256_file(clean)},
        }
        if not comparison["logical_match"]:
            raise BenchmarkError(f"tester {tester} reference repair does not match baseline")
        return report
    finally:
        tmp.unlink(missing_ok=True)


def restore_tester(
    tester: int,
    *,
    input_path: Path | None = None,
    output_path: Path | None = None,
    force: bool,
    verify_assets: bool = True,
) -> dict[str, Any]:
    if verify_assets:
        verify_protected_assets()
    source = (input_path or raw_db_path(tester)).resolve()
    output = (output_path or cleaned_db_path(tester)).resolve()
    manifest = load_json(manifest_path(tester))
    if output.exists() and not force:
        raise BenchmarkError(f"refusing to overwrite {output}; pass --force")
    if source == output:
        raise BenchmarkError("input and output must be different; in-place repair is prohibited")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(source, tmp)
        con = connect(tmp)
        try:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("PRAGMA ignore_check_constraints=ON")
            con.execute("BEGIN IMMEDIATE")
            for op in reversed(repair_records(manifest)):
                locator = op.get("corrupted_primary_key", op["primary_key"])
                clause, params = where_pk(locator)
                if op["operation"] == "insert_row":
                    con.execute(f"DELETE FROM {q(op['table'])} WHERE {clause}", params)
                elif op["operation"] == "update_cell":
                    con.execute(
                        f"UPDATE {q(op['table'])} SET {q(op['column'])}=? WHERE {clause}",
                        [op["clean_value"], *params],
                    )
                else:
                    raise BenchmarkError(f"unknown manifest operation {op['operation']}")
            sequences = manifest.get("validation_expectations", {}).get(
                "sqlite_sequence", manifest.get("baseline", {}).get("sqlite_sequence")
            )
            if sequences is not None:
                con.execute("DELETE FROM sqlite_sequence")
                con.executemany(
                    "INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)",
                    [(item["name"], item["seq"]) for item in sequences],
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
        os.replace(tmp, output)
        score = score_candidate(output, manifest)
        comparison = logical_compare(BASELINE, output)
        return {"output": str(output), "score": score, "comparison": comparison}
    finally:
        tmp.unlink(missing_ok=True)


def validate_tester(tester: int, candidate: Path) -> dict[str, Any]:
    verify_protected_assets()
    manifest = load_json(manifest_path(tester))
    score = score_candidate(candidate, manifest)
    comparison = logical_compare(BASELINE, candidate)
    return {"benchmark_id": manifest["dataset"]["benchmark_id"], "score": score, "baseline_comparison": comparison}


def score_recommendations(tester: int, response_path: Path) -> dict[str, Any]:
    expected_payload = load_json(manifest_path(tester))
    expected = [
        {
            "rule_id": item["rule_id"],
            "table": item["location"]["table"],
            "columns": item["location"]["columns"],
            "error_type": item["error_type"],
            "expected_action": item["preprocessing"]["expected_action"],
        }
        for item in expected_payload["preprocessing_plan"]
    ]
    response = load_json(response_path)
    provided = response.get("recommendations", [])
    expected_by_id = {r["rule_id"]: r for r in expected}
    signature_to_id = {
        (r["table"], tuple(sorted(r["columns"])), r["error_type"]): r["rule_id"]
        for r in expected
    }
    matched_pairs: list[tuple[dict[str, Any], str]] = []
    unknown: list[str] = []
    for idx, item in enumerate(provided, 1):
        supplied_id = item.get("rule_id")
        matched_id = supplied_id if supplied_id in expected_by_id else None
        if matched_id is None:
            signature = (
                item.get("table"),
                tuple(sorted(item.get("columns", []))),
                item.get("error_type"),
            )
            matched_id = signature_to_id.get(signature)
        if matched_id:
            matched_pairs.append((item, matched_id))
        else:
            unknown.append(str(supplied_id or f"response-item-{idx}"))
    detected = {rule_id for _, rule_id in matched_pairs}
    precision = len(detected) / len(provided) if provided else 0.0
    recall = len(detected) / len(expected_by_id) if expected_by_id else 1.0
    action_matches = 0
    action_seen: set[str] = set()
    for item, rule_id in matched_pairs:
        actual = str(item.get("recommended_action", "")).strip().casefold()
        wanted = str(expected_by_id[rule_id]["expected_action"]).strip().casefold()
        if actual == wanted and rule_id not in action_seen:
            action_matches += 1
            action_seen.add(rule_id)
    return {
        "benchmark_id": expected_payload["dataset"]["benchmark_id"],
        "detection_precision": round(precision, 4),
        "detection_recall": round(recall, 4),
        "action_accuracy_on_expected": round(action_matches / len(expected_by_id), 4) if expected_by_id else 1.0,
        "detected_rule_ids": sorted(detected),
        "missed_rule_ids": sorted(set(expected_by_id) - detected),
        "unknown_recommendations": sorted(unknown),
    }


def verify_project() -> dict[str, Any]:
    policy = verify_protected_assets()
    result: dict[str, Any] = {"protected_assets": "ok", "assets": policy["assets"], "testers": {}}
    for tester in (1, 2, 3):
        raw = raw_db_path(tester)
        clean = cleaned_db_path(tester)
        manifest = manifest_path(tester)
        if not all(p.exists() for p in (raw, clean, manifest)):
            result["testers"][str(tester)] = {"complete": False}
            continue
        validation = validate_tester(tester, clean)
        result["testers"][str(tester)] = {
            "complete": True,
            "raw_sha256": sha256_file(raw),
            "clean_sha256": sha256_file(clean),
            "clean_quality_score": validation["score"]["quality_score"],
            "logical_match": validation["baseline_comparison"]["logical_match"],
        }
    result["ok"] = all(v.get("complete") and v.get("logical_match") for v in result["testers"].values())
    return result


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild one raw tester and its reference-clean output")
    build.add_argument("--tester", type=int, choices=(1, 2, 3), required=True)
    build.add_argument("--force", action="store_true")
    build_all = sub.add_parser("build-all", help="rebuild all three testers")
    build_all.add_argument("--force", action="store_true")
    restore = sub.add_parser("restore", help="repair a tester using the private oracle")
    restore.add_argument("--tester", type=int, choices=(1, 2, 3), required=True)
    restore.add_argument("--input", type=Path)
    restore.add_argument("--output", type=Path)
    restore.add_argument("--force", action="store_true")
    validate = sub.add_parser("validate", help="score a cleaned candidate database")
    validate.add_argument("--tester", type=int, choices=(1, 2, 3), required=True)
    validate.add_argument("--candidate", type=Path, required=True)
    profile = sub.add_parser("profile", help="profile a database")
    profile.add_argument("--db", type=Path, required=True)
    profile.add_argument("--output", type=Path)
    score_rec = sub.add_parser("score-recommendations", help="score an Agent recommendation JSON")
    score_rec.add_argument("--tester", type=int, choices=(1, 2, 3), required=True)
    score_rec.add_argument("--response", type=Path, required=True)
    sub.add_parser("verify-project", help="verify protected files and all generated artifacts")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "build":
            print_json(build_tester(args.tester, force=args.force))
        elif args.command == "build-all":
            reports = {str(i): build_tester(i, force=args.force) for i in (1, 2, 3)}
            print_json({"testers": reports})
        elif args.command == "restore":
            print_json(
                restore_tester(
                    args.tester,
                    input_path=args.input,
                    output_path=args.output,
                    force=args.force,
                )
            )
        elif args.command == "validate":
            print_json(validate_tester(args.tester, args.candidate.resolve()))
        elif args.command == "profile":
            payload = profile_database(args.db.resolve())
            if args.output:
                atomic_json(args.output.resolve(), payload)
            print_json(payload)
        elif args.command == "score-recommendations":
            print_json(score_recommendations(args.tester, args.response.resolve()))
        elif args.command == "verify-project":
            payload = verify_project()
            print_json(payload)
            return 0 if payload["ok"] else 1
        return 0
    except (BenchmarkError, FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
