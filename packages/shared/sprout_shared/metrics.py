from __future__ import annotations

import time

from prometheus_client import Counter, Histogram, make_asgi_app
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "sprout_http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "sprout_http_request_duration_seconds",
    "HTTP request duration",
    ["service", "method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def mount_metrics(app, service_name: str = "unknown") -> None:
    class _MetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            if request.url.path == "/metrics":
                return await call_next(request)
            start = time.perf_counter()
            response = await call_next(request)
            duration = time.perf_counter() - start
            path_root = "/" + request.url.path.strip("/").split("/")[0] if request.url.path != "/" else "/"
            REQUEST_DURATION.labels(service_name, request.method, path_root).observe(duration)
            REQUEST_COUNT.labels(service_name, request.method, path_root, response.status_code).inc()
            return response

    app.add_middleware(_MetricsMiddleware)
    app.mount("/metrics", make_asgi_app())
