"""The SQL safety gate — the interview gold of this project.

Six checks, in order:
  1. PARSE        must parse as SQL (sqlglot, sqlite dialect)
  2. SINGLE       exactly one statement, no semicolon-chained extras
  3. SELECT-ONLY  the AST must be a SELECT; no INSERT/UPDATE/DELETE/DROP/
                  CREATE/ALTER/ATTACH/PRAGMA anywhere in the tree
  4. ALLOWLIST    every referenced table must be in the known schema
  5. LIMIT        a LIMIT is always present and capped at 100
  6. NO-COMMENTS  comments are stripped before all of the above so injection
                  can't hide behind them; function blacklist on top

This module has NO network/LLM imports so it is unit-testable and safe to run
in the eval harness offline.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"customers", "products", "orders", "order_items"}
MAX_LIMIT = 100
BLACKLISTED_FUNCTIONS = {"loadfile", "readfile", "writefile", "load_extension"}


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None
    normalized_sql: str


def _reject_forbidden_nodes(ast: exp.Expression) -> str | None:
    for node in ast.walk():
        if isinstance(
            node,
            (
                exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                exp.Alter, exp.Attach, exp.Detach, exp.Command, exp.Grant,
            ),
        ):
            return f"forbidden statement type: {type(node).__name__}"
        if isinstance(node, exp.Anonymous) and node.this and str(node.this).lower() in BLACKLISTED_FUNCTIONS:
            return f"forbidden function: {node.this}"
    return None


def _reject_unknown_tables(ast: exp.Expression) -> str | None:
    for table in ast.find_all(exp.Table):
        name = str(table.this).lower() if table.this else ""
        if name and name not in ALLOWED_TABLES:
            return f"table not allowed: {name}"
    return None


def _enforce_limit(ast: exp.Expression) -> exp.Expression:
    if not isinstance(ast, exp.Select):
        return ast
    limit = ast.args.get("limit")
    if limit is None:
        ast = ast.limit(MAX_LIMIT)
    else:
        try:
            value = int(limit.expression.this)
            if value > MAX_LIMIT:
                ast = ast.limit(MAX_LIMIT)
        except (AttributeError, ValueError):
            ast = ast.limit(MAX_LIMIT)
    return ast


def validate_sql(sql: str) -> ValidationResult:
    sql = sql.strip().rstrip(";").strip()

    # 6. no comments, ever — injection can't hide behind them (checked BEFORE any parsing)
    if "--" in sql or "/*" in sql:
        return ValidationResult(False, "SQL comments are not allowed", sql)

    # 6. no comments, ever — injection can't hide behind them
    if "--" in sql or "/*" in sql:
        return ValidationResult(False, "SQL comments are not allowed", sql)

    # 2. single statement
    if ";" in sql:
        return ValidationResult(False, "multiple statements are not allowed", sql)

    # 1. parse
    try:
        statements = sqlglot.parse(sql, dialect="sqlite")
    except sqlglot.errors.ParseError as e:
        return ValidationResult(False, f"could not parse SQL: {e}", sql)
    if len(statements) != 1 or statements[0] is None:
        return ValidationResult(False, "expected exactly one SELECT statement", sql)
    ast = statements[0]

    # 3. select-only
    if not isinstance(ast, exp.Select):
        return ValidationResult(False, "only SELECT statements are allowed", sql)
    if reason := _reject_forbidden_nodes(ast):
        return ValidationResult(False, reason, sql)

    # 4. table allowlist
    if reason := _reject_unknown_tables(ast):
        return ValidationResult(False, reason, sql)

    # 5. limit enforcement
    ast = _enforce_limit(ast)

    normalized = ast.sql(dialect="sqlite")
    return ValidationResult(True, None, normalized)