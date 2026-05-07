import pytest
import json
from daemon.helpers.responses import error_response

def test_error_response_hides_details_on_500():
    # Technical detail that should be hidden
    secret_detail = "Database connection string leaked!"

    response = error_response("Internal server error", status_code=500, detail=secret_detail)
    data = json.loads(response.body)

    assert response.status_code == 500
    assert data["error"] == "Internal server error"
    assert "detail" not in data

def test_error_response_shows_details_on_400():
    # User-facing detail that should be shown
    validation_error = "Invalid email format"

    response = error_response("Bad request", status_code=400, detail=validation_error)
    data = json.loads(response.body)

    assert response.status_code == 400
    assert data["error"] == "Bad request"
    assert data["detail"] == validation_error

def test_error_response_hides_details_on_all_server_errors():
    for status in [500, 501, 502, 503, 504]:
        response = error_response("Error", status_code=status, detail="Sensitive")
        data = json.loads(response.body)
        assert "detail" not in data, f"Detail leaked for status {status}"
