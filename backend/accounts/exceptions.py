import uuid

from rest_framework import status
from rest_framework.views import exception_handler

from reporting.audit import json_safe


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response
    details = response.data
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        message = "Sign in is required or your session has expired. Please sign in again."
    elif response.status_code == status.HTTP_404_NOT_FOUND:
        message = "The requested record was not found or is not available to your account."
    elif isinstance(details, dict) and details.get("detail"):
        message = str(details["detail"])
    elif isinstance(details, dict):
        field, value = next(iter(details.items()), ("", "Please correct the highlighted fields."))
        first_value = value[0] if isinstance(value, list) and value else value
        if field == "non_field_errors":
            message = str(first_value)
        else:
            field_label = field.replace("_", " ").capitalize() if field else ""
            message = f"{field_label}: {first_value}" if field_label else str(first_value)
    elif isinstance(details, list) and details:
        message = str(details[0])
    else:
        message = "Your request could not be completed. Please try again."
    request = context.get("request")
    correlation_id = getattr(request, "correlation_id", None) or str(uuid.uuid4())
    response.data = {
        "error": {
            "code": exc.__class__.__name__,
            "message": message,
            "fields": json_safe(details) if isinstance(details, dict) else {},
            "correlation_id": correlation_id,
        }
    }
    return response
