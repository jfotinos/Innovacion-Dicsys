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


def _source_alias_map(expression: exp.Expression) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        alias_name = table.alias_or_name
        table_name = table.name
        if alias_name and table_name and alias_name.lower() != table_name.lower():
            alias_map[table_name.lower()] = alias_name
    return alias_map


def _table_qualifier_map(base_select: exp.Select, ref_select: exp.Select) -> Dict[str, str]:
    base_tables = list(base_select.find_all(exp.Table))
    ref_tables = list(ref_select.find_all(exp.Table))
    qualifier_map: Dict[str, str] = {}

    for base_table, ref_table in zip(base_tables, ref_tables):
        ref_qualifier = ref_table.alias_or_name or ref_table.name
        if not ref_qualifier:
            continue

        for candidate in (base_table.alias_or_name, base_table.name):
            if candidate:
                qualifier_map[candidate.lower()] = ref_qualifier

    return qualifier_map


def _is_in_where(node: exp.Expression, base_select: exp.Select) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return True
        if parent is base_select:
            return False
        parent = parent.parent
    return False


def _normalize_column_qualifiers(expression: exp.Expression, alias_map: Dict[str, str]) -> None:
    for column in expression.find_all(exp.Column):
        table_name = column.table
        if not table_name:
            continue

        normalized_table = alias_map.get(table_name.lower())
        if normalized_table and normalized_table.lower() != table_name.lower():
            column.set("table", exp.to_identifier(normalized_table))


def _replace_from_clause(base_select: exp.Select, ref_select: exp.Select, report: dict) -> None:
    ref_from = ref_select.args.get("from")
    if ref_from is None:
        return

    base_select.set("from", ref_from.copy())

    ref_joins = ref_select.args.get("joins")
    if ref_joins is not None:
        base_select.set("joins", [join.copy() for join in ref_joins])
    elif "joins" in base_select.args:
        base_select.set("joins", None)

    report["from_replaced"] = True
    report["from_sql"] = ref_from.sql(dialect=report["dialect"], pretty=False)


def _strip_project_from_from_clause(base_select: exp.Select, report: dict, project_prefix: str = "arcor") -> None:
    from_clause = base_select.args.get("from")
    if from_clause is None:
        return

    stripped: List[dict] = []
    for table in base_select.find_all(exp.Table):
        catalog = table.catalog
        if not catalog:
            continue

        if not catalog.lower().startswith(project_prefix.lower()):
            continue

        before = table.sql(dialect=report["dialect"], pretty=False)
        table.set("catalog", None)
        after = table.sql(dialect=report["dialect"], pretty=False)
        stripped.append({"from": before, "to": after})

    report["from_project_stripped"] = stripped


def _get_single_base_table(base_select: exp.Select) -> exp.Table:
    from_clause = base_select.args.get("from")
    if not from_clause:
        raise ValueError("La query base debe tener un FROM con una sola tabla")

    expressions = list(from_clause.expressions or [])
    if not expressions and from_clause.this is not None:
        expressions = [from_clause.this]

    if base_select.args.get("joins"):
        raise ValueError("Solo se soporta una tabla en la query base por ahora")

    if len(expressions) == 1 and isinstance(expressions[0], exp.Table):
        return expressions[0]

    def _is_in_subquery(node: exp.Expression) -> bool:
        parent = node.parent
        while parent is not None:
            if isinstance(parent, exp.Subquery):
                return True
            if parent is from_clause:
                return False
            parent = parent.parent
        return False

    tables = [table for table in from_clause.find_all(exp.Table) if not _is_in_subquery(table)]
    if len(tables) == 1:
        return tables[0]

    raise ValueError("La query base debe tener un FROM con una sola tabla")


def _inline_base_columns(
    base_select: exp.Select,
    ref_select: exp.Select,
    report: dict,
    dialect: str = "bigquery",
) -> None:
    base_table = _get_single_base_table(base_select)
    ref_projection_map = _select_projection_map(ref_select, dialect=dialect)

    qualifiers = {
        name.lower()
        for name in (base_table.alias_or_name, base_table.name)
        if name
    }

    inlined_columns: List[dict] = []
    unmapped: set[str] = set()
    where_skipped: List[dict] = []

    for column in base_select.find_all(exp.Column):
        if not _is_in_where(column, base_select):
            continue
        if column.name == "*":
            continue

        if column.table:
            if qualifiers and column.table.lower() not in qualifiers:
                continue
        elif not qualifiers:
            continue

        key = column.name.lower()
        ref_expr = ref_projection_map.get(key)
        if not ref_expr:
            unmapped.add(column.name)
            continue

        if not isinstance(ref_expr, exp.Column):
            where_skipped.append(
                {
                    "column": column.name,
                    "to": ref_expr.sql(dialect=dialect),
                }
            )
            continue

        column.replace(ref_expr.copy())
        inlined_columns.append(
            {
                "column": column.name,
                "to": ref_expr.sql(dialect=dialect),
            }
        )

    report["columns_inlined"] = inlined_columns
    report["unmapped_columns"] = sorted(unmapped)
    report["where_inline_skipped"] = where_skipped


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

    original_base_select = base_select.copy()

    ref_select = _pick_reference_select(reference_sql, dialect=dialect)

    ref_projection_map = _select_projection_map(ref_select, dialect=dialect)

    report = {
        "dialect": dialect,
        "columns_replaced": [],
        "where_conditions_added": [],
        "from_replaced": False,
        "columns_inlined": [],
        "unmapped_columns": [],
        "where_inline_skipped": [],
        "from_project_stripped": [],
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

    _inline_base_columns(base_select, ref_select, report, dialect=dialect)

    _replace_from_clause(base_select, ref_select, report)

    _strip_project_from_from_clause(base_select, report)

    qualifier_map = _table_qualifier_map(original_base_select, ref_select)
    _normalize_column_qualifiers(base_statement, qualifier_map)

    _combine_where_with_and(base_select, ref_select, report=report, dialect=dialect)

    _normalize_column_qualifiers(base_statement, _source_alias_map(base_statement))

    output_sql = base_statement.sql(dialect=dialect, pretty=True)
    return output_sql, report


def report_to_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=True)
