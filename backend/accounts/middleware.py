import contextvars
import logging
import uuid


_correlation_id = contextvars.ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = _correlation_id.get()
        return True


class CorrelationIdMiddleware:
    """Attach a safe request identifier without logging request bodies or PII."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        candidate = request.headers.get("X-Correlation-ID", "")
        correlation_id = candidate[:64] if candidate and all(char.isalnum() or char in "-_." for char in candidate) else str(uuid.uuid4())
        token = _correlation_id.set(correlation_id)
        request.correlation_id = correlation_id
        try:
            response = self.get_response(request)
            response["X-Correlation-ID"] = correlation_id
            return response
        finally:
            _correlation_id.reset(token)


class ApiNoStoreMiddleware:
    """Prevent browsers/proxies from replaying stale API list/detail responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response
