from agent_eval.database_audit import compare_database_schema


def _table(*, selectable=True, columns=None, indexes=None):
    return {
        "schema": "public",
        "name": "LiteLLM_SpendLogs",
        "selectable": selectable,
        "columns": columns or [],
        "indexes": indexes or [],
    }


def test_schema_comparison_reports_missing_table_with_migration_guidance():
    result = compare_database_schema(
        {"tables": []},
        {"source": "test", "tables": [_table()]},
    )

    assert result["compatible"] is False
    assert result["missing_tables"] == ["public.LiteLLM_SpendLogs"]
    assert result["issues"][0]["category"] == "database_table_missing"
    assert "migrations" in result["issues"][0]["suggested_action"]


def test_schema_comparison_reports_column_permission_and_index_differences():
    expected_column = {
        "name": "request_id", "data_type": "text", "udt_name": "text", "nullable": False
    }
    actual_column = {
        "name": "request_id", "data_type": "integer", "udt_name": "int4", "nullable": False
    }
    baseline = {
        "source": "test",
        "tables": [_table(
            columns=[expected_column, {
                "name": "model", "data_type": "text", "udt_name": "text", "nullable": False
            }],
            indexes=[{"name": "required_idx", "definition": "expected"}],
        )],
    }
    inventory = {
        "tables": [_table(selectable=False, columns=[actual_column], indexes=[])]
    }

    result = compare_database_schema(inventory, baseline)

    assert result["compatible"] is False
    categories = {issue["category"] for issue in result["issues"]}
    assert categories == {
        "database_select_permission_missing",
        "database_columns_missing",
        "database_column_type_mismatch",
        "database_index_difference",
    }
    difference = result["table_differences"][0]
    assert difference["missing_columns"] == ["model"]
    assert difference["missing_indexes"] == ["required_idx"]
