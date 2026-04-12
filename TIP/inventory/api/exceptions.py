from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def humanized_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return Response(
            {
                "code": "server_error",
                "detail": "Unexpected server error. Please retry or contact the administrator.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    payload = {
        "code": getattr(exc, "default_code", "error"),
        "detail": detail or "Request could not be completed.",
    }

    if isinstance(response.data, dict):
        field_errors = {key: value for key, value in response.data.items() if key != "detail"}
        if field_errors:
            payload["errors"] = field_errors

    response.data = payload
    return response
