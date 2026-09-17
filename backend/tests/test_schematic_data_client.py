from app.schematic_data_client import _records


def test_records_accepts_single_document():
    rows, total = _records({"sessionId": "s-1", "status": "completed"})
    assert rows == [{"sessionId": "s-1", "status": "completed"}]
    assert total is None


def test_records_accepts_nested_page():
    rows, total = _records({"data": {"records": [{"sessionId": "s-1"}], "total": 7}})
    assert rows == [{"sessionId": "s-1"}]
    assert total == 7
