import json
from typing import Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp


def _normalize_sql(expression: exp.Expression, dialect: str = "bigquery") -> str:
    return expression.sql(dialect=dialect, pretty=False).strip().lower()


def _extract_select_statement(statement: exp.Expression) -> Optional[exp.Select]:
    if isinstance(statement, exp.Select):
        return statement

    if isinstance(statement, exp.Create) and isinstance(statement.expression, exp.Select):
        return statement.expression

    if isinstance(statement, exp.Insert) and isinstance(statement.expression, exp.Select):
        return statement.expression

    if isinstance(statement, exp.Subqueryable):
        select = statement.find(exp.Select)
        if isinstance(select, exp.Select):
            return select

    return None


def _pick_reference_select(reference_sql: str, dialect: str = "bigquery") -> exp.Select:
    statements = sqlglot.parse(reference_sql, read=dialect)
    for statement in reversed(statements):
        select = _extract_select_statement(statement)
        if select is not None:
            return select
    raise ValueError("No se encontro un SELECT util en el SQL de referencia")


def _alias_key(projection: exp.Expression, dialect: str = "bigquery") -> str:
    alias_or_name = projection.alias_or_name
    if alias_or_name:
        return alias_or_name.lower()
    return _normalize_sql(projection, dialect=dialect)


def _projection_expression(projection: exp.Expression) -> exp.Expression:
    if isinstance(projection, exp.Alias):
        return projection.this
    return projection


def _select_projection_map(select_stmt: exp.Select, dialect: str = "bigquery") -> Dict[str, exp.Expression]:
    mapping: Dict[str, exp.Expression] = {}
    for projection in select_stmt.expressions:
        key = _alias_key(projection, dialect=dialect)
        mapping[key] = _projection_expression(projection)
    return mapping


def _flatten_and_conditions(expression: exp.Expression) -> List[exp.Expression]:
    if isinstance(expression, exp.And):
        return _flatten_and_conditions(expression.left) + _flatten_and_conditions(expression.right)
    return [expression]


def _where_conditions(select_stmt: exp.Select) -> List[exp.Expression]:
    where_clause = select_stmt.args.get("where")
    if not where_clause:
        return []
    return _flatten_and_conditions(where_clause.this)


def _combine_where_with_and(base_select: exp.Select, ref_select: exp.Select, report: dict, dialect: str = "bigquery") -> None:
    base_conditions = _where_conditions(base_select)
    ref_conditions = _where_conditions(ref_select)

    if not ref_conditions:
        return

    serialized_base = {_normalize_sql(cond, dialect=dialect) for cond in base_conditions}
    added_conditions: List[str] = []

    combined: Optional[exp.Expression] = None
    for condition in base_conditions:
        combined = condition.copy() if combined is None else exp.and_(combined, condition.copy())

    for condition in ref_conditions:
        normalized = _normalize_sql(condition, dialect=dialect)
        if normalized in serialized_base:
            continue
        serialized_base.add(normalized)
        added_conditions.append(condition.sql(dialect=dialect))
        combined = condition.copy() if combined is None else exp.and_(combined, condition.copy())

    if combined is not None:
        base_select.set("where", exp.Where(this=combined))

    report["where_conditions_added"] = added_conditions


def rewrite_query(base_sql: str, reference_sql: str, dialect: str = "bigquery") -> Tuple[str, dict]:
    base_statement = sqlglot.parse_one(base_sql, read=dialect)
    base_select = _extract_select_statement(base_statement)
    if base_select is None:
        raise ValueError("La query base debe contener un SELECT valido")

    ref_select = _pick_reference_select(reference_sql, dialect=dialect)

    ref_projection_map = _select_projection_map(ref_select, dialect=dialect)

    report = {
        "dialect": dialect,
        "columns_replaced": [],
        "where_conditions_added": [],
    }

    for i, projection in enumerate(base_select.expressions):
        key = _alias_key(projection, dialect=dialect)
        if key not in ref_projection_map:
            continue

        current_expr = _projection_expression(projection)
        new_expr = ref_projection_map[key]

        if _normalize_sql(current_expr, dialect=dialect) == _normalize_sql(new_expr, dialect=dialect):
            continue

        alias_name = projection.alias_or_name or key
        report["columns_replaced"].append(
            {
                "column": alias_name,
                "from": current_expr.sql(dialect=dialect),
                "to": new_expr.sql(dialect=dialect),
            }
        )

        if isinstance(projection, exp.Alias):
            projection.set("this", new_expr.copy())
            base_select.expressions[i] = projection
        else:
            # If original projection had no explicit alias and replacement is complex,
            # preserve output compatibility by aliasing with previous output name.
            if isinstance(new_expr, exp.Column):
                base_select.expressions[i] = new_expr.copy()
            else:
                base_select.expressions[i] = exp.alias_(new_expr.copy(), alias_name)

    _combine_where_with_and(base_select, ref_select, report=report, dialect=dialect)

    output_sql = base_statement.sql(dialect=dialect, pretty=True)
    return output_sql, report


def report_to_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=True)
