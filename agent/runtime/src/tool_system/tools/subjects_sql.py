from __future__ import annotations

import os
import re
import hashlib
import json
import importlib.util
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlparse

from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolOutcomeStatus, ToolResult
from ..preflight import PreflightDecision
from ..registry import ToolCapability, ToolDependencies, ToolExecutionPolicy, ToolSpec


DEFAULT_DATABASE_URL = "mysql+pymysql://chassis:chassis_dev_password@127.0.0.1:3306/chassis_platform"
MAX_SQL_LIMIT = 500
DANGEROUS_SQL = re.compile(r"\b(ALTER|CREATE|DELETE|DROP|GRANT|INSERT|REPLACE|TRUNCATE|UPDATE)\b", re.IGNORECASE)
DEFAULT_ALLOWED_TABLES = frozenset({"entity_type", "entity_attribute", "vehicle_instance", "instance_attribute_value"})
CORE_TABLE_HINTS = {
    "entity_type": "entity categories/types.",
    "entity_attribute": "attribute definitions; search by name/code before querying values.",
    "vehicle_instance": "vehicle/version records; join by id to instance_attribute_value.target_id.",
    "instance_attribute_value": "attribute values; inspect distinct target_type first, then join by target_id and attribute_id. In this database target_type may be 'vehicle' for car specs and 'component' for parts.",
}
TABLE_ALIASES = {
    "attribute": "entity_attribute",
    "attributes": "entity_attribute",
    "attribute_value": "instance_attribute_value",
    "attribute_values": "instance_attribute_value",
    "vehicle_attribute": "instance_attribute_value",
    "vehicle_attributes": "instance_attribute_value",
    "subject": "vehicle_instance",
    "subjects": "vehicle_instance",
    "vehicle": "vehicle_instance",
    "vehicles": "vehicle_instance",
}

_UNIT_RE = re.compile(r"[\(\（][^)\）]{0,20}[\)\）]")
_SEPARATOR_RE = re.compile(r"[\s_/\-\\·、，,。:：;；|]+")
_DIRECTION_TERMS = ("前", "后", "左", "右", "上", "下")
_SEMANTIC_EQUIV_CHARS = str.maketrans({
    "箱": "厢",
    "臺": "台",
    "臺": "台",
    "裏": "里",
})


def _normalize_field_text(value: str, *, drop_unit: bool = True) -> str:
    text = str(value or "").strip().lower()
    if drop_unit:
        text = _UNIT_RE.sub("", text)
    text = text.translate(_SEMANTIC_EQUIV_CHARS)
    text = _SEPARATOR_RE.sub("", text)
    return text


def _char_ngrams(value: str, n: int = 2) -> set[str]:
    text = _normalize_field_text(value)
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[index:index + n] for index in range(0, len(text) - n + 1)}


def _direction_terms(value: str) -> set[str]:
    text = _normalize_field_text(value)
    return {term for term in _DIRECTION_TERMS if term in text}


def _field_similarity(query: str, candidate_name: str, candidate_code: str = "") -> tuple[float, list[str]]:
    query_norm = _normalize_field_text(query)
    name_norm = _normalize_field_text(candidate_name)
    code_norm = _normalize_field_text(candidate_code)
    if not query_norm:
        return 0.0, []
    reasons: list[str] = []
    score = 0.0
    haystacks = [name_norm, code_norm]
    if query_norm in haystacks:
        score += 120.0
        reasons.append("normalized_exact")
    elif any(query_norm and query_norm in item for item in haystacks):
        score += 90.0
        reasons.append("normalized_contains")
    elif any(item and item in query_norm for item in haystacks):
        score += 65.0
        reasons.append("query_contains_field")

    query_chars = set(query_norm)
    name_chars = set(name_norm)
    if query_chars and name_chars:
        overlap = len(query_chars & name_chars) / len(query_chars | name_chars)
        score += overlap * 45.0
        if overlap >= 0.5:
            reasons.append("char_overlap")

    query_bigrams = _char_ngrams(query_norm, 2)
    name_bigrams = _char_ngrams(name_norm, 2)
    if query_bigrams and name_bigrams:
        ngram_overlap = len(query_bigrams & name_bigrams) / len(query_bigrams | name_bigrams)
        score += ngram_overlap * 55.0
        if ngram_overlap >= 0.35:
            reasons.append("ngram_overlap")

    query_dirs = _direction_terms(query)
    name_dirs = _direction_terms(candidate_name)
    if query_dirs and name_dirs:
        if query_dirs & name_dirs:
            score += 25.0
            reasons.append("direction_match")
        else:
            score -= 80.0
            reasons.append("direction_conflict")
    elif query_dirs and not name_dirs:
        score -= 8.0
        reasons.append("direction_missing")

    return max(0.0, round(score, 3)), reasons


def _candidate_attribute_rows(query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    query_norm = _normalize_field_text(query)
    if not query_norm:
        return []
    chars = [ch for ch in dict.fromkeys(query_norm) if re.match(r"[\w\u4e00-\u9fff]", ch)]
    if not chars:
        return []
    where = " OR ".join(["ea.name LIKE %s OR ea.code LIKE %s"] * len(chars))
    params: list[Any] = []
    for ch in chars:
        params.extend([f"%{ch}%", f"%{ch}%"])
    rows = _query(
        f"""
        SELECT
            ea.id AS attribute_id,
            ea.code AS attribute_code,
            ea.name AS attribute_name,
            ea.unit AS attribute_unit,
            COUNT(DISTINCT vi.id) AS covered_vehicle_count,
            COUNT(vi.id) AS populated_value_count
        FROM entity_attribute ea
        LEFT JOIN instance_attribute_value iav
            ON iav.attribute_id = ea.id
            AND iav.target_type IN ('vehicle', 'vehicle_instance')
        LEFT JOIN vehicle_instance vi
            ON vi.id = iav.target_id
        WHERE {where}
        GROUP BY ea.id, ea.code, ea.name, ea.unit
        """,
        tuple(params),
    )
    scored: list[dict[str, Any]] = []
    for row in rows:
        similarity, reasons = _field_similarity(query, str(row.get("attribute_name") or ""), str(row.get("attribute_code") or ""))
        if similarity <= 0:
            continue
        coverage = min(float(row.get("populated_value_count") or 0), 5000.0) / 5000.0
        row = dict(row)
        row["match_score"] = round(similarity + coverage * 8.0, 3)
        row["match_reasons"] = reasons
        scored.append(row)
    scored.sort(key=lambda item: (-float(item.get("match_score") or 0), -int(item.get("populated_value_count") or 0), int(item.get("attribute_id") or 0)))
    return scored[:limit]


def _relevant_attribute_candidates(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if not candidates:
        return []
    top_score = float(candidates[0].get("match_score") or 0)
    if top_score <= 0:
        return []
    cutoff = max(45.0, top_score * 0.75)
    relevant = [row for row in candidates if float(row.get("match_score") or 0) >= cutoff]
    return relevant[:limit]


def _candidate_filter_attribute_rows_by_value(value_keyword: str, *, limit: int = 10) -> list[dict[str, Any]]:
    keyword = str(value_keyword or "").strip()
    if not keyword:
        return []
    rows = _query(
        """
        SELECT
            ea.id AS attribute_id,
            ea.code AS attribute_code,
            ea.name AS attribute_name,
            ea.unit AS attribute_unit,
            COUNT(DISTINCT iav.target_id) AS matched_vehicle_count,
            COUNT(iav.id) AS matched_value_count,
            MIN(iav.value_text) AS sample_value
        FROM instance_attribute_value iav
        JOIN entity_attribute ea ON ea.id = iav.attribute_id
        WHERE
            iav.target_type IN ('vehicle', 'vehicle_instance')
            AND iav.value_text LIKE %s COLLATE utf8mb4_bin
        GROUP BY ea.id, ea.code, ea.name, ea.unit
        ORDER BY matched_vehicle_count DESC, matched_value_count DESC, ea.id
        LIMIT %s
        """,
        (f"%{keyword}%", limit),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["match_score"] = min(100.0, 40.0 + float(item.get("matched_vehicle_count") or 0) ** 0.5)
        item["match_reasons"] = ["value_distribution_match"]
        out.append(item)
    return out


def _database_url() -> str:
    return (
        os.getenv("SUBJECTS_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


def _parse_mysql_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise ToolInputError(f"unsupported database URL scheme: {parsed.scheme}")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
        "charset": "utf8mb4",
        "connect_timeout": 3,
        "read_timeout": 10,
        "write_timeout": 10,
        "autocommit": True,
    }


def _connect():
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover
        raise ToolInputError("PyMySQL is not installed") from exc

    return pymysql.connect(
        **_parse_mysql_url(_database_url()),
        cursorclass=pymysql.cursors.DictCursor,
    )


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SET SESSION MAX_EXECUTION_TIME={_max_execution_time_ms()}")
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return list(cur.fetchall())


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _is_select_only(sql: str) -> bool:
    stripped = sql.strip().rstrip(";")
    if not re.match(r"^SELECT\b", stripped, re.IGNORECASE):
        return False
    if ";" in stripped:
        return False
    return DANGEROUS_SQL.search(stripped) is None


def _validate_sql_ast(sql: str) -> tuple[bool, str | None, set[str]]:
    """Validate a single read-only statement with sqlglot when available."""
    stripped = sql.strip().rstrip(";")
    if ";" in stripped or "/*" in stripped or "--" in stripped or "#" in stripped:
        return False, "comments and multiple statements are not allowed", set()
    try:
        from sqlglot import exp, parse
    except ImportError:
        allowed = _is_select_only(stripped)
        if not allowed:
            return False, "only one read-only SELECT statement is allowed", set()
        # Fail closed on table authorization even in a lightweight local
        # environment where sqlglot was not installed. This fallback supports
        # simple SELECT/JOIN statements; production should still use sqlglot.
        tables = {
            match.lower()
            for match in re.findall(
                r"\b(?:FROM|JOIN)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
                stripped,
                flags=re.IGNORECASE,
            )
        }
        denied = sorted(table for table in tables if table not in _allowed_tables())
        if denied:
            return False, f"query references unauthorized or unknown tables: {', '.join(denied)}", tables
        return True, None, tables
    try:
        statements = parse(stripped, read="mysql")
    except Exception as exc:
        return False, f"SQL parse failed: {exc}", set()
    if len(statements) != 1:
        return False, "exactly one SQL statement is required", set()
    root = statements[0]
    forbidden = tuple(
        kind for name in ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Merge")
        if (kind := getattr(exp, name, None)) is not None
    )
    if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)) or any(root.find(kind) for kind in forbidden):
        return False, "only read-only SELECT queries are allowed", set()
    cte_names = {
        str(cte.alias_or_name).lower()
        for cte in root.find_all(exp.CTE)
        if cte.alias_or_name
    }
    tables = {
        table.name.lower()
        for table in root.find_all(exp.Table)
        if table.name and table.name.lower() not in cte_names
    }
    denied = sorted(table for table in tables if table not in _allowed_tables())
    if denied:
        return False, f"query references unauthorized tables: {', '.join(denied)}", tables
    return True, None, tables


def _allowed_tables() -> set[str]:
    raw = os.getenv("SUBJECTS_SQL_ALLOWED_TABLES", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()} or set(DEFAULT_ALLOWED_TABLES)


def _max_execution_time_ms() -> int:
    return max(100, min(int(os.getenv("SUBJECTS_SQL_MAX_EXECUTION_MS", "5000")), 60_000))


def _max_estimated_rows() -> int:
    return max(1, int(os.getenv("SUBJECTS_SQL_MAX_ESTIMATED_ROWS", "200000")))


def _explain_cost(sql: str) -> tuple[int, list[dict[str, Any]]]:
    plan = _query(f"EXPLAIN {sql}")
    estimated = sum(int(row.get("rows") or 0) for row in plan)
    return estimated, plan


def _vehicle_scope_filter(context: ToolContext, alias: str) -> tuple[str, tuple[Any, ...], str | None]:
    scope = context.data_scope or {}
    if scope.get("scope") == "all" and {"admin", "platform_admin"}.intersection(context.role_ids):
        return "", (), None
    clauses = [f"{alias}.status = 'active'"]
    params: list[Any] = []
    vehicle_ids = [int(item) for item in scope.get("vehicle_ids", []) if str(item).isdigit()]
    if vehicle_ids:
        clauses.append(f"{alias}.id IN ({', '.join(['%s'] * len(vehicle_ids))})")
        params.extend(vehicle_ids)
    owner_user_id = scope.get("owner_user_id")
    if owner_user_id is not None and str(owner_user_id).isdigit():
        clauses.append(f"{alias}.owner_user_id = %s")
        params.append(int(owner_user_id))
    if scope.get("systems"):
        return "", (), "system-scoped requests must use a system-specific business query tool"
    return " AND " + " AND ".join(clauses), tuple(params), None


def _generic_sql_scope_error(sql: str, tables: set[str], context: ToolContext) -> str | None:
    scope = context.data_scope or {}
    if not scope:
        return None
    if scope.get("scope") == "all" and {"admin", "platform_admin"}.intersection(context.role_ids):
        return None
    if scope.get("vehicle_ids") or scope.get("owner_user_id") or scope.get("systems"):
        return "generic SQL is disabled for restrictive data scopes; use an authorized business query tool"
    if "vehicle_instance" in tables:
        normalized = re.sub(r"\s+", " ", sql.lower())
        if "status" not in normalized or "active" not in normalized:
            return "vehicle queries must explicitly restrict vehicle_instance.status to active"
    return None


def _ensure_limit(sql: str, default_limit: int) -> str:
    stripped = sql.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        return stripped
    return f"{stripped} LIMIT {default_limit}"


def _query_exception_outcome(exc: Exception) -> tuple[ToolOutcomeStatus, bool, str]:
    text = str(exc).lower()
    transient_markers = (
        "connection refused",
        "can't connect",
        "lost connection",
        "server has gone away",
        "lock wait timeout",
        "too many connections",
        "timed out",
        "timeout",
    )
    if any(marker in text for marker in transient_markers):
        return ToolOutcomeStatus.TRANSIENT_FAILURE, True, "SQL_DEPENDENCY_TRANSIENT_FAILURE"
    return ToolOutcomeStatus.INVALID_INPUT, False, "SQL_EXECUTION_REJECTED"


def _preflight_mysql_dependency() -> PreflightDecision:
    configured_url = os.getenv("SUBJECTS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if os.getenv("APP_ENV", "local").lower() != "local" and not configured_url:
        return PreflightDecision.reject(
            "DEPENDENCY_NOT_CONFIGURED",
            "Subjects MySQL is not configured for this environment.",
            disable_tool_for_run=True,
        )
    try:
        _parse_mysql_url(configured_url or DEFAULT_DATABASE_URL)
    except Exception as exc:
        return PreflightDecision.reject(
            "DEPENDENCY_CONFIGURATION_INVALID",
            f"Subjects MySQL configuration is invalid: {exc}",
            disable_tool_for_run=True,
        )
    if importlib.util.find_spec("pymysql") is None:
        return PreflightDecision.reject(
            "DEPENDENCY_CLIENT_MISSING",
            "PyMySQL is not installed in the Runtime environment.",
            disable_tool_for_run=True,
        )
    return PreflightDecision.allow("SUBJECTS_MYSQL_CONFIGURED")


def preflight_subjects_read_query(sql: str, context: ToolContext) -> PreflightDecision:
    dependency = _preflight_mysql_dependency()
    if not dependency.can_execute:
        return dependency
    sql = str(sql or "").strip()
    if not sql:
        return PreflightDecision.reject("EMPTY_SQL_QUERY", "query must be a non-empty string")
    valid, reason, tables = _validate_sql_ast(sql)
    if not valid:
        return PreflightDecision.reject(
            "SQL_POLICY_REJECTED",
            reason or "Only one read-only SELECT statement is allowed.",
            diagnostics={"query": sql},
        )
    scope_error = _generic_sql_scope_error(sql, tables, context)
    if scope_error:
        return PreflightDecision.reject(
            "SQL_DATA_SCOPE_REJECTED",
            scope_error,
            disable_tool_for_run=False,
            alternative_capabilities=("data.vehicle.query",),
            diagnostics={"tables": sorted(tables)},
        )
    return PreflightDecision.allow("SQL_STATIC_PREFLIGHT_PASSED")


def execute_subjects_read_query(
    sql: str,
    context: ToolContext,
    *,
    limit: int = MAX_SQL_LIMIT,
    result_name: str = "SubjectsSqlQuery",
) -> ToolResult:
    """The single governed execution path for Runtime-generated Subjects SQL."""

    sql = str(sql or "").strip()
    static_preflight = preflight_subjects_read_query(sql, context)
    if not static_preflight.can_execute:
        return ToolResult(
            name=result_name,
            output={
                "error": static_preflight.message,
                "query": sql,
                "reason_code": static_preflight.reason_code,
                "alternative_capabilities": list(static_preflight.alternative_capabilities),
            },
            is_error=True,
            outcome_status=(
                ToolOutcomeStatus.PERMISSION_DENIED
                if static_preflight.reason_code == "SQL_DATA_SCOPE_REJECTED"
                else ToolOutcomeStatus.INVALID_INPUT
            ),
            reason_code=static_preflight.reason_code,
            diagnostics=static_preflight.diagnostics,
        )
    _, _, tables = _validate_sql_ast(sql)

    effective_limit = max(1, min(int(limit or MAX_SQL_LIMIT), MAX_SQL_LIMIT))
    sql = _ensure_limit(sql, effective_limit)
    try:
        estimated_rows, plan = _explain_cost(sql)
        if estimated_rows > _max_estimated_rows():
            return ToolResult(
                name=result_name,
                output={
                    "error": "query cost exceeds the configured scan budget",
                    "estimated_rows": estimated_rows,
                    "max_estimated_rows": _max_estimated_rows(),
                    "advice": "add selective filters or aggregate before returning rows",
                },
                is_error=True,
                outcome_status=ToolOutcomeStatus.INVALID_INPUT,
                reason_code="SQL_SCAN_BUDGET_EXCEEDED",
            )
        rows = _query(sql)
    except Exception as exc:
        status, retryable, reason_code = _query_exception_outcome(exc)
        return ToolResult(
            name=result_name,
            output={"error": str(exc)[:2000], "query": sql},
            is_error=True,
            outcome_status=status,
            reason_code=reason_code,
            retryable=retryable,
        )

    safe_rows = _json_safe(rows[:MAX_SQL_LIMIT])
    evidence_hash = hashlib.sha256(
        json.dumps({"query": sql, "rows": safe_rows}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return ToolResult(
        name=result_name,
        output={
            "query": sql,
            "row_count": len(rows),
            "rows": safe_rows,
            "truncated": len(rows) > MAX_SQL_LIMIT,
            "estimated_rows": estimated_rows,
            "tables": sorted(tables),
            "execution_timeout_ms": _max_execution_time_ms(),
            "evidence_hash": evidence_hash,
            "query_plan": plan,
        },
        outcome_status=ToolOutcomeStatus.SUCCESS if rows else ToolOutcomeStatus.NO_DATA,
        reason_code=None if rows else "SQL_NO_ROWS",
    )


class SubjectsSqlSchemaTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsSqlSchema",
            description=(
                "Inspect the SubjectsDetection MySQL database schema. "
                "Use this before writing SQL when you need table names or columns. "
                "Call with a specific table/table_name for full columns; the default response is intentionally compact. "
                "Core tables: entity_type, entity_attribute, vehicle_instance, instance_attribute_value. "
                "Do not guess generic names like attribute, attribute_value, subject, vehicles, or vehicle_attributes."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "table": {"type": "string", "description": "Optional table name. If omitted, list all tables."},
                    "include_columns": {"type": "boolean", "description": "Include columns for all tables when table is omitted. Defaults to false to control context cost."},
                    "limit": {"type": "integer", "description": "Maximum tables to return when table is omitted."},
                },
            },
            input_aliases={"table_name": "table"},
            is_read_only=True,
            max_result_size_chars=20_000,
            capability=ToolCapability(
                namespace="data.catalog",
                actions=("discover",),
                entity_types=("database_schema",),
                input_modes=("schema_request",),
                output_modes=("schema",),
                limitations=("Exposes physical MySQL schema, not governed business semantics or data coverage.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                concurrency_pool="sql",
                supports_parallel=True,
                cache_policy="short_ttl",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL",),
                health_probe="subjects_mysql_health",
            ),
            preflight_checks=("tool_authorized", "subjects_mysql_healthy"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        table = str(tool_input.get("table") or "").strip()
        include_columns = bool(tool_input.get("include_columns", False))
        limit = int(tool_input.get("limit") or 80)
        limit = max(1, min(limit, 200))
        requested_table = table
        table = TABLE_ALIASES.get(table.lower(), table) if table else table

        if table:
            rows = _query(
                """
                SELECT
                    column_name AS column_name,
                    column_type AS column_type,
                    is_nullable AS is_nullable,
                    column_key AS column_key,
                    column_default AS column_default,
                    column_comment AS column_comment
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            if not rows:
                return ToolResult(
                    name="SubjectsSqlSchema",
                    output={
                        "error": f"table not found: {requested_table}",
                        "core_tables": CORE_TABLE_HINTS,
                        "advice": "Call SubjectsSqlSchema with no table and include_columns=true, or use SubjectsSqlGlob to search table/column names.",
                    },
                    is_error=True,
                )
            output = {"table": table, "columns": rows}
            if requested_table and requested_table != table:
                output["resolved_from"] = requested_table
                output["note"] = f"'{requested_table}' is treated as alias for '{table}'."
            return ToolResult(name="SubjectsSqlSchema", output=_json_safe(output))

        tables = _query(
            """
            SELECT table_name AS table_name, table_rows AS table_rows
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            ORDER BY table_name
            LIMIT %s
            """,
            (limit,),
        )
        if not include_columns:
            return ToolResult(
                name="SubjectsSqlSchema",
                output=_json_safe(
                    {
                        "tables": tables,
                        "count": len(tables),
                        "core_tables": CORE_TABLE_HINTS,
                        "advice": "For full columns, call SubjectsSqlSchema with table set to one specific table.",
                    }
                ),
            )

        out: list[dict[str, Any]] = []
        for row in tables:
            name = row["table_name"]
            cols = _query(
                """
                SELECT
                    column_name AS column_name,
                    column_type AS column_type,
                    is_nullable AS is_nullable,
                    column_key AS column_key
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = %s
                ORDER BY ordinal_position
                """,
                (name,),
            )
            out.append({"table": name, "estimated_rows": row.get("table_rows"), "columns": cols})
        return ToolResult(name="SubjectsSqlSchema", output=_json_safe({"tables": out, "count": len(out)}))

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()


class SubjectsSqlGlobTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsSqlGlob",
            description="Find SubjectsDetection database tables or columns by SQL LIKE pattern, e.g. vehicle%, %attribute%, %image%.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pattern": {"type": "string"},
                    "scope": {"type": "string", "enum": ["tables", "columns", "all"]},
                    "limit": {"type": "integer"},
                },
                "required": ["pattern"],
            },
            input_aliases={"query": "pattern", "keyword": "pattern"},
            is_read_only=True,
            max_result_size_chars=40_000,
            capability=ToolCapability(
                namespace="data.catalog",
                actions=("discover",),
                entity_types=("database_table", "database_column"),
                input_modes=("name_pattern",),
                output_modes=("catalog_matches",),
                limitations=("Name matching does not establish business meaning, quality, freshness, or coverage.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                concurrency_pool="sql",
                supports_parallel=True,
                cache_policy="short_ttl",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL",),
                health_probe="subjects_mysql_health",
            ),
            preflight_checks=("tool_authorized", "subjects_mysql_healthy"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = str(tool_input.get("pattern") or "").replace("*", "%").strip()
        if not pattern:
            raise ToolInputError("pattern must be a non-empty string")
        scope = tool_input.get("scope") or "all"
        limit = max(1, min(int(tool_input.get("limit") or 100), 500))
        matches: list[dict[str, Any]] = []

        if scope in {"tables", "all"}:
            matches.extend(
                {"type": "table", "name": row["table_name"]}
                for row in _query(
                    """
                    SELECT table_name AS table_name
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name LIKE %s
                    ORDER BY table_name
                    LIMIT %s
                    """,
                    (pattern, limit),
                )
            )

        if scope in {"columns", "all"} and len(matches) < limit:
            remaining = limit - len(matches)
            matches.extend(
                {"type": "column", "table": row["table_name"], "name": row["column_name"], "column_type": row["column_type"]}
                for row in _query(
                    """
                    SELECT
                        table_name AS table_name,
                        column_name AS column_name,
                        column_type AS column_type
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE() AND column_name LIKE %s
                    ORDER BY table_name, ordinal_position
                    LIMIT %s
                    """,
                    (pattern, remaining),
                )
            )

        return ToolResult(name="SubjectsSqlGlob", output=_json_safe({"pattern": pattern, "scope": scope, "matches": matches, "count": len(matches)}))

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()


class SubjectsDataCatalogSearchTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsDataCatalogSearch",
            description=(
                "Search the governed vehicle business-field catalog and report actual value coverage before writing exploratory SQL. "
                "Returns matching attribute definitions, units, scoped vehicle counts, and a concrete coverage boundary."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "description": "Business field keyword such as 前悬架, 减震器, 阀系, or supplier."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
            input_aliases={"keyword": "query", "field": "query"},
            is_read_only=True,
            strict=True,
            max_result_size_chars=40_000,
            capability=ToolCapability(
                namespace="data.catalog",
                actions=("search", "coverage"),
                entity_types=("vehicle_attribute",),
                input_modes=("business_field_keyword",),
                output_modes=("field_catalog", "coverage_report"),
                limitations=(
                    "Reports coverage in the governed Subjects vehicle dataset, not real-world feature availability.",
                    "Component and system-profile coverage require dedicated catalogs.",
                ),
                positive_examples=("Check whether damper supplier or front suspension fields exist before querying values.",),
                negative_examples=("Do not infer that a feature does not exist in reality when catalog coverage is zero.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                concurrency_pool="sql",
                supports_parallel=True,
                cache_policy="short_ttl",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL",),
                health_probe="subjects_mysql_health",
                coverage_probe="subjects_vehicle_attribute_coverage",
            ),
            preflight_checks=("tool_authorized", "subjects_mysql_healthy", "vehicle_data_scope"),
        )

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        dependency = _preflight_mysql_dependency()
        if not dependency.can_execute:
            return dependency
        query = str(tool_input.get("query") or "").strip()
        if not query:
            return PreflightDecision.reject("CATALOG_QUERY_EMPTY", "query must be a non-empty business field keyword.")
        _, _, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return PreflightDecision.reject(
                "CATALOG_DATA_SCOPE_UNSUPPORTED",
                scope_error,
                alternative_capabilities=("data.system.catalog",),
            )
        return PreflightDecision.allow("CATALOG_STATIC_PREFLIGHT_PASSED")

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = str(tool_input.get("query") or "").strip()
        limit = max(1, min(int(tool_input.get("limit") or 20), 50))
        scope_sql, scope_params, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return ToolResult(
                name="SubjectsDataCatalogSearch",
                output={"error": scope_error},
                is_error=True,
                outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                reason_code="CATALOG_DATA_SCOPE_UNSUPPORTED",
            )
        try:
            candidate_rows = _candidate_attribute_rows(query, limit=max(limit * 4, 50))
            relevant_candidates = _relevant_attribute_candidates(candidate_rows, limit=max(limit * 2, 20))
            attribute_ids = [int(row["attribute_id"]) for row in relevant_candidates]
            if attribute_ids:
                placeholders = ", ".join(["%s"] * len(attribute_ids))
                coverage_rows = _query(
                    f"""
                    SELECT
                        ea.id AS attribute_id,
                        ea.code AS attribute_code,
                        ea.name AS attribute_name,
                        ea.unit AS attribute_unit,
                        COUNT(DISTINCT vi.id) AS covered_vehicle_count,
                        COUNT(vi.id) AS populated_value_count
                    FROM entity_attribute ea
                    LEFT JOIN instance_attribute_value iav
                        ON iav.attribute_id = ea.id
                        AND iav.target_type IN ('vehicle', 'vehicle_instance')
                    LEFT JOIN vehicle_instance vi
                        ON vi.id = iav.target_id
                        {scope_sql}
                    WHERE ea.id IN ({placeholders})
                    GROUP BY ea.id, ea.code, ea.name, ea.unit
                    """,
                    (*scope_params, *attribute_ids),
                )
                score_by_id = {
                    int(row["attribute_id"]): {
                        "match_score": row.get("match_score"),
                        "match_reasons": row.get("match_reasons"),
                    }
                    for row in candidate_rows
                }
                rows = []
                for row in coverage_rows:
                    enriched = dict(row)
                    enriched.update(score_by_id.get(int(row.get("attribute_id") or 0), {}))
                    rows.append(enriched)
                rows.sort(key=lambda item: (-float(item.get("match_score") or 0), -int(item.get("populated_value_count") or 0), int(item.get("attribute_id") or 0)))
                rows = rows[:limit]
            else:
                rows = []
        except Exception as exc:
            status, retryable, reason_code = _query_exception_outcome(exc)
            return ToolResult(
                name="SubjectsDataCatalogSearch",
                output={"error": str(exc)[:2000], "query": query},
                is_error=True,
                outcome_status=status,
                reason_code=reason_code,
                retryable=retryable,
            )
        safe_rows = _json_safe(rows)
        populated = sum(int(row.get("populated_value_count") or 0) for row in safe_rows)
        if not rows:
            boundary = "No matching business field definition exists in the governed vehicle catalog."
            status = ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT
            reason_code = "CATALOG_FIELD_NOT_COVERED"
        elif populated <= 0:
            boundary = "Matching field definitions exist, but no governed vehicle values are populated in the current data scope."
            status = ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT
            reason_code = "CATALOG_VALUES_NOT_COVERED"
        else:
            boundary = "Coverage counts describe only the governed Subjects dataset and current authorization scope."
            status = ToolOutcomeStatus.SUCCESS
            reason_code = None
        return ToolResult(
            name="SubjectsDataCatalogSearch",
            output={
                "query": query,
                "candidate_count": len(candidate_rows),
                "match_count": len(safe_rows),
                "matches": safe_rows,
                "populated_value_count": populated,
                "coverage_boundary": boundary,
            },
            outcome_status=status,
            reason_code=reason_code,
        )


class SubjectsAttributeLookupTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsAttributeLookup",
            description=(
                "Look up structured attribute values for matching SubjectsDetection entities in one step. "
                "Use this before manual SQL for common vehicle facts such as dimensions, wheelbase, mass, price, range, "
                "suspension, battery, or configuration. It accepts broad entity and attribute keywords, resolves matching "
                "vehicle instances and entity attributes, and joins instance_attribute_value internally."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entity_keyword": {
                        "type": "string",
                        "description": "Optional entity/version keyword, e.g. 小米SU7 or 小鹏X9. Omit or use * for all vehicles.",
                    },
                    "attribute_keyword": {
                        "type": "string",
                        "description": "Attribute keyword, e.g. 轴距, 车长, 前悬架类型, 续航.",
                    },
                    "filter_attribute_keyword": {
                        "type": "string",
                        "description": "Optional vehicle attribute used as a cohort filter, e.g. 级别 or 车身结构.",
                    },
                    "filter_value_keyword": {
                        "type": "string",
                        "description": "Optional cohort value keyword. If filter_attribute_keyword is omitted, the tool resolves the filter field from governed value distributions.",
                    },
                    "entity_type": {
                        "type": "string",
                        "description": "Entity family. Currently vehicle/car/车型 all resolve to vehicle records. Defaults to vehicle.",
                    },
                    "limit": {"type": "integer", "description": "Maximum rows to return, max 100."},
                },
                "required": ["attribute_keyword"],
            },
            input_aliases={
                "entity": "entity_keyword",
                "vehicle_keyword": "entity_keyword",
                "attribute": "attribute_keyword",
                "keyword": "attribute_keyword",
                "query": "attribute_keyword",
            },
            is_read_only=True,
            strict=True,
            max_result_size_chars=40_000,
            capability=ToolCapability(
                namespace="data.vehicle",
                actions=("query", "lookup"),
                entity_types=("vehicle",),
                input_modes=("entity_attribute_keywords",),
                output_modes=("tabular", "evidence"),
                limitations=(
                    "Supports vehicle records only; component and system-profile attributes require another business tool.",
                    "A zero-row result does not prove that the real-world attribute does not exist.",
                ),
                positive_examples=(
                    "Look up front suspension type for a named vehicle.",
                    "Look up front trunk volume for all vehicles where level/级别 equals MPV.",
                ),
                negative_examples=("Do not use for component supplier or damper valve details.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                concurrency_pool="sql",
                supports_parallel=True,
                supports_batch=True,
                max_batch_size=8,
                cache_policy="request",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL",),
                health_probe="subjects_mysql_health",
                coverage_probe="subjects_vehicle_attribute_coverage",
            ),
            preflight_checks=("tool_authorized", "subjects_mysql_healthy", "vehicle_attribute_coverage"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        entity_keyword = str(tool_input.get("entity_keyword") or "*").strip()
        attribute_keyword = str(tool_input.get("attribute_keyword") or "").strip()
        filter_attribute_keyword = str(tool_input.get("filter_attribute_keyword") or "").strip()
        filter_value_keyword = str(tool_input.get("filter_value_keyword") or "").strip()
        entity_type = str(tool_input.get("entity_type") or "vehicle").strip().lower()
        limit = max(1, min(int(tool_input.get("limit") or 50), 100))
        if not attribute_keyword:
            raise ToolInputError("attribute_keyword must be a non-empty string")
        if entity_type not in {"vehicle", "vehicles", "car", "cars", "车型", "车辆"}:
            raise ToolInputError("entity_type currently supports vehicle/car/车型")

        all_entities = entity_keyword.lower() in {"*", "全部", "所有", "all"}
        entity_like = "%" if all_entities else f"%{entity_keyword}%"
        attr_candidates = _candidate_attribute_rows(attribute_keyword, limit=20)
        relevant_attr_candidates = _relevant_attribute_candidates(attr_candidates, limit=10)
        attribute_ids = [int(row["attribute_id"]) for row in relevant_attr_candidates]
        filter_attr_candidates: list[dict[str, Any]] = []
        relevant_filter_attr_candidates: list[dict[str, Any]] = []
        filter_attribute_ids: list[int] = []
        if filter_attribute_keyword:
            filter_attr_candidates = _candidate_attribute_rows(filter_attribute_keyword, limit=20)
            relevant_filter_attr_candidates = _relevant_attribute_candidates(filter_attr_candidates, limit=10)
            filter_attribute_ids = [int(row["attribute_id"]) for row in relevant_filter_attr_candidates]
        elif filter_value_keyword:
            relevant_filter_attr_candidates = _candidate_filter_attribute_rows_by_value(filter_value_keyword, limit=10)
            filter_attribute_ids = [int(row["attribute_id"]) for row in relevant_filter_attr_candidates]
        scope_sql, scope_params, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return ToolResult(
                name="SubjectsAttributeLookup",
                output={"error": scope_error},
                is_error=True,
                outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                reason_code="VEHICLE_LOOKUP_DATA_SCOPE_REJECTED",
            )
        try:
            filter_join_sql = ""
            filter_where_sql = ""
            filter_params: tuple[Any, ...] = ()
            if filter_attribute_ids and filter_value_keyword:
                filter_placeholders = ", ".join(["%s"] * len(filter_attribute_ids))
                filter_join_sql = """
                    JOIN instance_attribute_value filter_iav
                        ON filter_iav.target_id = vi.id
                        AND filter_iav.target_type IN ('vehicle', 'vehicle_instance')
                    JOIN entity_attribute filter_ea ON filter_ea.id = filter_iav.attribute_id
                """
                filter_where_sql = f"""
                        AND filter_ea.id IN ({filter_placeholders})
                        AND (
                            filter_iav.value_text LIKE %s COLLATE utf8mb4_bin
                            OR CAST(filter_iav.value_number AS CHAR) LIKE %s COLLATE utf8mb4_bin
                        )
                """
                filter_params = (*filter_attribute_ids, f"%{filter_value_keyword}%", f"%{filter_value_keyword}%")

            filtered_entity_count: int | None = None
            if filter_value_keyword:
                if filter_attribute_ids:
                    count_rows = _query(
                        f"""
                        SELECT COUNT(DISTINCT vi.id) AS filtered_entity_count
                        FROM vehicle_instance vi
                        {filter_join_sql}
                        WHERE
                            (vi.vehicle_name LIKE %s OR vi.vehicle_code LIKE %s)
                            {filter_where_sql}
                            {scope_sql}
                        """,
                        (entity_like, entity_like, *filter_params, *scope_params),
                    )
                    filtered_entity_count = int((count_rows[0] or {}).get("filtered_entity_count") or 0) if count_rows else 0
                else:
                    filtered_entity_count = 0

            if attribute_ids:
                placeholders = ", ".join(["%s"] * len(attribute_ids))
                rows = _query(
                    f"""
                    SELECT
                        vi.id AS vehicle_id,
                        vi.vehicle_code AS vehicle_code,
                        vi.vehicle_name AS vehicle_name,
                        ea.id AS attribute_id,
                        ea.code AS attribute_code,
                        ea.name AS attribute_name,
                        ea.unit AS attribute_unit,
                        iav.value_number AS value_number,
                        iav.value_text AS value_text,
                        COALESCE(iav.unit, ea.unit) AS unit,
                        iav.target_type AS target_type
                    FROM vehicle_instance vi
                    {filter_join_sql}
                    JOIN instance_attribute_value iav
                        ON iav.target_id = vi.id
                        AND iav.target_type IN ('vehicle', 'vehicle_instance')
                    JOIN entity_attribute ea ON ea.id = iav.attribute_id
                    WHERE
                        (vi.vehicle_name LIKE %s OR vi.vehicle_code LIKE %s)
                        AND ea.id IN ({placeholders})
                        {filter_where_sql}
                        {scope_sql}
                    ORDER BY
                        CASE
                            WHEN vi.vehicle_name LIKE %s THEN 0
                            WHEN vi.vehicle_code LIKE %s THEN 1
                            ELSE 2
                        END,
                        vi.id,
                        FIELD(ea.id, {placeholders})
                    LIMIT %s
                    """,
                    (
                        entity_like,
                        entity_like,
                        *attribute_ids,
                        *filter_params,
                        *scope_params,
                        entity_like,
                        entity_like,
                        *attribute_ids,
                        limit,
                    ),
                )
            else:
                rows = []
        except Exception as exc:
            status, retryable, reason_code = _query_exception_outcome(exc)
            return ToolResult(
                name="SubjectsAttributeLookup",
                output={"error": str(exc)[:2000], "entity_keyword": entity_keyword, "attribute_keyword": attribute_keyword},
                is_error=True,
                outcome_status=status,
                reason_code=reason_code,
                retryable=retryable,
            )

        return ToolResult(
            name="SubjectsAttributeLookup",
            output=_json_safe(
                {
                    "entity_keyword": entity_keyword,
                    "attribute_keyword": attribute_keyword,
                    "attribute_candidates": _json_safe(relevant_attr_candidates[:10]),
                    "filter_attribute_keyword": filter_attribute_keyword or None,
                    "filter_value_keyword": filter_value_keyword or None,
                    "filter_attribute_candidates": _json_safe(relevant_filter_attr_candidates[:10]),
                    "filtered_entity_count": filtered_entity_count,
                    "entity_type": "vehicle",
                    "row_count": len(rows),
                    "rows": rows,
                    "advice": (
                        "If row_count is zero, broaden the keywords or fall back to SubjectsSqlGlob/SubjectsSqlQuery "
                        "to inspect attribute names and available entity records."
                    ),
                    "coverage_boundary": (
                        None
                        if rows
                        else "No matching vehicle-attribute rows were found in the governed Subjects dataset; "
                        "this does not establish that the real-world attribute is unavailable."
                    ),
                }
            ),
            outcome_status=ToolOutcomeStatus.SUCCESS if rows else ToolOutcomeStatus.NO_DATA,
            reason_code=None if rows else "VEHICLE_ATTRIBUTE_NO_ROWS",
        )

    def run_batch(self, tool_inputs: list[dict[str, Any]], context: ToolContext) -> list[ToolResult]:
        return [self.run(tool_input, context) for tool_input in tool_inputs]

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        dependency = _preflight_mysql_dependency()
        if not dependency.can_execute:
            return dependency
        entity_keyword = str(tool_input.get("entity_keyword") or "*").strip()
        attribute_keyword = str(tool_input.get("attribute_keyword") or "").strip()
        entity_type = str(tool_input.get("entity_type") or "vehicle").strip().lower()
        if not attribute_keyword:
            return PreflightDecision.reject(
                "VEHICLE_LOOKUP_KEYWORD_MISSING",
                "attribute_keyword must be a non-empty string; entity_keyword may be omitted to query all vehicles.",
            )
        if entity_type not in {"vehicle", "vehicles", "car", "cars", "车型", "车辆"}:
            return PreflightDecision.reject(
                "CAPABILITY_ENTITY_UNSUPPORTED",
                "SubjectsAttributeLookup supports vehicle entities only.",
                alternative_capabilities=("data.component.query", "data.system.query"),
                diagnostics={"requested_entity_type": entity_type},
            )
        _, _, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return PreflightDecision.reject(
                "VEHICLE_LOOKUP_DATA_SCOPE_REJECTED",
                scope_error,
                alternative_capabilities=("data.system.query",),
            )
        return PreflightDecision.allow("VEHICLE_LOOKUP_STATIC_PREFLIGHT_PASSED")


class SubjectsAttributeStatsTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsAttributeStats",
            description=(
                "Compute governed numeric statistics for vehicle attributes without hand-written SQL. "
                "Use for all-vehicle/cohort statistics such as average/min/max/distribution of wheelbase, front/rear track, "
                "mass, dimensions, price, range, or battery capacity. This tool resolves entity_attribute and joins "
                "active vehicle_instance records internally."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "attribute_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Vehicle attribute keywords to aggregate, e.g. ['前轮距', '后轮距'].",
                    },
                    "attribute_keyword": {
                        "type": "string",
                        "description": "Single vehicle attribute keyword; use attribute_keywords for multiple fields.",
                    },
                    "entity_keyword": {
                        "type": "string",
                        "description": "Optional vehicle keyword. Omit or use * for all vehicles.",
                    },
                    "filter_attribute_keyword": {"type": "string"},
                    "filter_value_keyword": {"type": "string"},
                    "sample_limit": {"type": "integer", "description": "Rows to include as examples, max 20."},
                },
            },
            input_aliases={"attributes": "attribute_keywords", "attribute": "attribute_keyword", "query": "attribute_keyword"},
            is_read_only=True,
            strict=True,
            max_result_size_chars=60_000,
            capability=ToolCapability(
                namespace="data.vehicle",
                actions=("aggregate", "stats"),
                entity_types=("vehicle",),
                input_modes=("vehicle_attribute_keywords",),
                output_modes=("statistics", "evidence"),
                limitations=("Numeric value statistics only; categorical distributions should use a dedicated grouped query path.",),
                positive_examples=("Compute statistics for all vehicles' front and rear track values.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                concurrency_pool="sql",
                supports_parallel=True,
                cache_policy="request",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL",),
                health_probe="subjects_mysql_health",
                coverage_probe="subjects_vehicle_attribute_coverage",
            ),
            preflight_checks=("tool_authorized", "subjects_mysql_healthy", "vehicle_attribute_coverage"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        raw_keywords = tool_input.get("attribute_keywords")
        if isinstance(raw_keywords, list):
            keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
        else:
            keywords = []
        single = str(tool_input.get("attribute_keyword") or "").strip()
        if single:
            keywords.append(single)
        keywords = list(dict.fromkeys(keywords))
        if not keywords:
            raise ToolInputError("attribute_keywords or attribute_keyword must include at least one non-empty string")

        entity_keyword = str(tool_input.get("entity_keyword") or "*").strip()
        all_entities = entity_keyword.lower() in {"*", "全部", "所有", "all"}
        entity_like = "%" if all_entities else f"%{entity_keyword}%"
        filter_attribute_keyword = str(tool_input.get("filter_attribute_keyword") or "").strip()
        filter_value_keyword = str(tool_input.get("filter_value_keyword") or "").strip()
        sample_limit = max(1, min(int(tool_input.get("sample_limit") or 5), 20))

        scope_sql, scope_params, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return ToolResult(
                name="SubjectsAttributeStats",
                output={"error": scope_error},
                is_error=True,
                outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                reason_code="VEHICLE_STATS_DATA_SCOPE_REJECTED",
            )

        filter_attr_candidates: list[dict[str, Any]] = []
        filter_attribute_ids: list[int] = []
        if filter_attribute_keyword:
            filter_attr_candidates = _relevant_attribute_candidates(_candidate_attribute_rows(filter_attribute_keyword, limit=20), limit=10)
            filter_attribute_ids = [int(row["attribute_id"]) for row in filter_attr_candidates]
        elif filter_value_keyword:
            filter_attr_candidates = _candidate_filter_attribute_rows_by_value(filter_value_keyword, limit=10)
            filter_attribute_ids = [int(row["attribute_id"]) for row in filter_attr_candidates]

        filter_join_sql = ""
        filter_where_sql = ""
        filter_params: tuple[Any, ...] = ()
        if filter_attribute_ids and filter_value_keyword:
            filter_placeholders = ", ".join(["%s"] * len(filter_attribute_ids))
            filter_join_sql = """
                JOIN instance_attribute_value filter_iav
                    ON filter_iav.target_id = vi.id
                    AND filter_iav.target_type IN ('vehicle', 'vehicle_instance')
                JOIN entity_attribute filter_ea ON filter_ea.id = filter_iav.attribute_id
            """
            filter_where_sql = f"""
                    AND filter_ea.id IN ({filter_placeholders})
                    AND (
                        filter_iav.value_text LIKE %s COLLATE utf8mb4_bin
                        OR CAST(filter_iav.value_number AS CHAR) LIKE %s COLLATE utf8mb4_bin
                    )
            """
            filter_params = (*filter_attribute_ids, f"%{filter_value_keyword}%", f"%{filter_value_keyword}%")

        stats: list[dict[str, Any]] = []
        try:
            for keyword in keywords:
                candidates = _relevant_attribute_candidates(_candidate_attribute_rows(keyword, limit=20), limit=10)
                attribute_ids = [int(row["attribute_id"]) for row in candidates]
                if not attribute_ids:
                    stats.append(
                        {
                            "attribute_keyword": keyword,
                            "attribute_candidates": [],
                            "row_count": 0,
                            "coverage_boundary": "No matching numeric vehicle attribute definition exists in the governed catalog.",
                        }
                    )
                    continue

                placeholders = ", ".join(["%s"] * len(attribute_ids))
                stat_rows = _query(
                    f"""
                    SELECT
                        ea.id AS attribute_id,
                        ea.code AS attribute_code,
                        ea.name AS attribute_name,
                        ea.unit AS attribute_unit,
                        COUNT(DISTINCT vi.id) AS vehicle_count,
                        COUNT(iav.value_number) AS numeric_value_count,
                        MIN(iav.value_number) AS min_value,
                        MAX(iav.value_number) AS max_value,
                        AVG(iav.value_number) AS avg_value,
                        STDDEV_POP(iav.value_number) AS stddev_pop
                    FROM vehicle_instance vi
                    {filter_join_sql}
                    JOIN instance_attribute_value iav
                        ON iav.target_id = vi.id
                        AND iav.target_type IN ('vehicle', 'vehicle_instance')
                    JOIN entity_attribute ea ON ea.id = iav.attribute_id
                    WHERE
                        (vi.vehicle_name LIKE %s OR vi.vehicle_code LIKE %s)
                        AND ea.id IN ({placeholders})
                        AND iav.value_number IS NOT NULL
                        {filter_where_sql}
                        {scope_sql}
                    GROUP BY ea.id, ea.code, ea.name, ea.unit
                    ORDER BY numeric_value_count DESC, ea.id
                    """,
                    (entity_like, entity_like, *attribute_ids, *filter_params, *scope_params),
                )
                sample_rows = _query(
                    f"""
                    SELECT
                        vi.id AS vehicle_id,
                        vi.vehicle_code AS vehicle_code,
                        vi.vehicle_name AS vehicle_name,
                        ea.id AS attribute_id,
                        ea.code AS attribute_code,
                        ea.name AS attribute_name,
                        COALESCE(iav.unit, ea.unit) AS unit,
                        iav.value_number AS value_number
                    FROM vehicle_instance vi
                    {filter_join_sql}
                    JOIN instance_attribute_value iav
                        ON iav.target_id = vi.id
                        AND iav.target_type IN ('vehicle', 'vehicle_instance')
                    JOIN entity_attribute ea ON ea.id = iav.attribute_id
                    WHERE
                        (vi.vehicle_name LIKE %s OR vi.vehicle_code LIKE %s)
                        AND ea.id IN ({placeholders})
                        AND iav.value_number IS NOT NULL
                        {filter_where_sql}
                        {scope_sql}
                    ORDER BY ea.id, vi.id
                    LIMIT %s
                    """,
                    (entity_like, entity_like, *attribute_ids, *filter_params, *scope_params, sample_limit),
                )
                stats.append(
                    {
                        "attribute_keyword": keyword,
                        "attribute_candidates": _json_safe(candidates[:10]),
                        "row_count": len(stat_rows),
                        "stats": _json_safe(stat_rows),
                        "sample_rows": _json_safe(sample_rows),
                        "coverage_boundary": (
                            "Statistics cover active governed vehicle records in the current authorization scope."
                            if stat_rows
                            else "Matching fields exist, but no numeric values are populated in the current scope."
                        ),
                    }
                )
        except Exception as exc:
            status, retryable, reason_code = _query_exception_outcome(exc)
            return ToolResult(
                name="SubjectsAttributeStats",
                output={"error": str(exc)[:2000], "attribute_keywords": keywords},
                is_error=True,
                outcome_status=status,
                reason_code=reason_code,
                retryable=retryable,
            )

        populated = sum(int(row.get("numeric_value_count") or 0) for item in stats for row in item.get("stats", []) if isinstance(row, dict))
        return ToolResult(
            name="SubjectsAttributeStats",
            output={
                "entity_keyword": entity_keyword,
                "attribute_keywords": keywords,
                "filter_attribute_keyword": filter_attribute_keyword or None,
                "filter_value_keyword": filter_value_keyword or None,
                "filter_attribute_candidates": _json_safe(filter_attr_candidates[:10]),
                "results": stats,
                "populated_numeric_value_count": populated,
            },
            outcome_status=ToolOutcomeStatus.SUCCESS if populated else ToolOutcomeStatus.NO_DATA,
            reason_code=None if populated else "VEHICLE_ATTRIBUTE_STATS_NO_NUMERIC_VALUES",
        )

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        dependency = _preflight_mysql_dependency()
        if not dependency.can_execute:
            return dependency
        raw_keywords = tool_input.get("attribute_keywords")
        has_list = isinstance(raw_keywords, list) and any(str(item).strip() for item in raw_keywords)
        has_single = bool(str(tool_input.get("attribute_keyword") or "").strip())
        if not has_list and not has_single:
            return PreflightDecision.reject(
                "VEHICLE_STATS_KEYWORD_MISSING",
                "attribute_keywords or attribute_keyword must include at least one non-empty string.",
            )
        _, _, scope_error = _vehicle_scope_filter(context, "vi")
        if scope_error:
            return PreflightDecision.reject(
                "VEHICLE_STATS_DATA_SCOPE_REJECTED",
                scope_error,
                alternative_capabilities=("data.system.query",),
            )
        return PreflightDecision.allow("VEHICLE_STATS_STATIC_PREFLIGHT_PASSED")


class SubjectsSqlQueryTool:
    def eligibility(self, context: ToolContext) -> PreflightDecision:
        return _preflight_mysql_dependency()

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="SubjectsSqlQuery",
            description=(
                "Run a read-only SQL query against the SubjectsDetection MySQL database. "
                "Only one SELECT query over approved tables is allowed. A LIMIT is added automatically when missing. "
                "Use SubjectsSqlSchema or SubjectsSqlGlob for schema discovery; do not send SHOW/DESCRIBE here. "
                "Prefer aggregate SQL (COUNT/GROUP BY/MIN/MAX/AVG) that answers the user's question in one query. "
                "When the returned rows are sufficient, stop calling tools and summarize. "
                "For structured vehicle queries, verify the database's actual target_type literals before joining "
                "instance_attribute_value to vehicle records."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Default LIMIT to add when query has none, max 500."},
                },
                "required": ["query"],
            },
            input_aliases={"sql": "query", "statement": "query"},
            is_read_only=True,
            strict=True,
            max_result_size_chars=80_000,
            capability=ToolCapability(
                namespace="data.sql",
                actions=("query", "aggregate"),
                entity_types=("vehicle", "approved_subjects_table"),
                input_modes=("mysql_select",),
                output_modes=("tabular", "evidence"),
                limitations=(
                    "Only one governed read-only SELECT over approved tables is allowed.",
                    "Restrictive data scopes must use a scope-aware business query tool.",
                ),
                positive_examples=("Aggregate approved vehicle attributes in one selective query.",),
                negative_examples=("Do not use for writes, system tables, or unrestricted schema exploration.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=10,
                retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                max_attempts=2,
                concurrency_pool="sql",
                supports_parallel=True,
                cache_policy="request",
            ),
            dependencies=ToolDependencies(
                services=("subjects_mysql",),
                required_config=("SUBJECTS_DATABASE_URL", "SUBJECTS_SQL_ALLOWED_TABLES"),
                health_probe="subjects_mysql_health",
                coverage_probe="subjects_sql_catalog_coverage",
            ),
            preflight_checks=(
                "tool_authorized",
                "subjects_mysql_healthy",
                "sql_ast_policy",
                "sql_data_scope",
                "sql_explain_budget",
            ),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        sql = str(tool_input.get("query") or "").strip()
        limit = max(1, min(int(tool_input.get("limit") or MAX_SQL_LIMIT), MAX_SQL_LIMIT))
        return execute_subjects_read_query(sql, context, limit=limit, result_name="SubjectsSqlQuery")

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        return preflight_subjects_read_query(str(tool_input.get("query") or ""), context)
