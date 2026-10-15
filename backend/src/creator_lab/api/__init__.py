from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from ..auth import check_request

bp = Blueprint("api", __name__, url_prefix="/api")
bp.before_request(check_request)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@bp.errorhandler(ApiError)
def _api_error(err: ApiError):
    return jsonify(error=err.message), err.status


@bp.errorhandler(HTTPException)
def _http_error(err: HTTPException):
    return jsonify(error=err.description or err.name), err.code


def body() -> dict:
    """The JSON request body, or a 400."""
    from flask import request

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("expected a JSON object body")
    return data


# Register route modules (they attach to bp on import).
from . import brain, channels, discover, jobs, owner, session, system, videos  # noqa: E402,F401
