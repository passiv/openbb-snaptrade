import asyncio
import inspect

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from snaptrade_client import SnapTrade

from ..context import resolve_workspace_context
from ..iframe import not_found_response
from ..snaptrade_client import (
    ensure_mapping,
    is_personal_client,
    missing_user_secret_response,
    snaptrade_client,
    snaptrade_credentials,
)
from ..transforms import camel_to_snake, response_body
from ..user_store import UserMapping


def _public_operations(client: SnapTrade) -> dict[str, list[str]]:
    catalog: dict[str, list[str]] = {}
    for namespace in dir(client):
        if namespace.startswith("_"):
            continue
        namespace_obj = getattr(client, namespace, None)
        if namespace_obj is None:
            continue
        methods: list[str] = []
        for operation in dir(namespace_obj):
            if operation.startswith("_"):
                continue
            target = getattr(namespace_obj, operation, None)
            if callable(target):
                methods.append(operation)
        if methods:
            catalog[namespace] = sorted(methods)
    return dict(sorted(catalog.items()))


def _resolve_operation_name(namespace_obj, requested_operation: str) -> str | None:
    candidates = [
        requested_operation,
        requested_operation.replace("-", "_"),
        camel_to_snake(requested_operation),
    ]
    for candidate in candidates:
        operation = getattr(namespace_obj, candidate, None)
        if callable(operation):
            return candidate
    return None


async def _invoke_sdk_operation(
    *,
    context,
    mapping: UserMapping,
    namespace: str,
    operation: str,
    args: list,
    kwargs: dict,
):
    client = snaptrade_client(context)
    namespace_obj = getattr(client, namespace, None)
    if namespace_obj is None:
        return None, JSONResponse(
            {
                "error": "unknown_namespace",
                "detail": f"Unknown SnapTrade namespace: {namespace}",
            },
            status_code=404,
        )

    resolved_operation = _resolve_operation_name(namespace_obj, operation)
    if not resolved_operation:
        return None, JSONResponse(
            {
                "error": "unknown_operation",
                "detail": f"Unknown operation '{operation}' for namespace '{namespace}'",
            },
            status_code=404,
        )

    target = getattr(namespace_obj, resolved_operation)

    try:
        signature = inspect.signature(target)
        parameters = signature.parameters
    except Exception:
        parameters = {}

    user_id, user_secret = snaptrade_credentials(context, mapping)
    personal = is_personal_client(context)

    if "user_id" in parameters and "user_id" not in kwargs:
        kwargs["user_id"] = user_id
    if "user_secret" in parameters and "user_secret" not in kwargs:
        if not personal and not user_secret:
            return None, missing_user_secret_response()
        kwargs["user_secret"] = user_secret

    try:
        response = await asyncio.to_thread(target, *args, **kwargs)
    except TypeError as exc:
        return None, JSONResponse(
            {
                "error": "invalid_arguments",
                "detail": str(exc),
                "namespace": namespace,
                "operation": resolved_operation,
            },
            status_code=400,
        )
    except Exception as exc:
        return None, JSONResponse(
            {
                "error": "sdk_call_failed",
                "detail": getattr(exc, "body", str(exc)),
                "namespace": namespace,
                "operation": resolved_operation,
            },
            status_code=502,
        )

    return {
        "namespace": namespace,
        "operation": resolved_operation,
        "data": response_body(response),
    }, None


def register(app: FastAPI) -> None:
    @app.get("/snaptrade/endpoints")
    async def snaptrade_endpoints(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        return JSONResponse(
            {
                "catalog": _public_operations(client),
                "usage": {
                    "get": "/snaptrade/api/{namespace}/{operation}?param=value",
                    "post": "/snaptrade/api/{namespace}/{operation}",
                    "post_body": {"args": [], "kwargs": {}},
                },
            }
        )

    @app.get("/snaptrade/api/{namespace}/{operation}")
    async def snaptrade_api_get(namespace: str, operation: str, request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        query_kwargs = dict(request.query_params)
        payload, error_response = await _invoke_sdk_operation(
            context=context,
            mapping=mapping,
            namespace=namespace,
            operation=operation,
            args=[],
            kwargs=query_kwargs,
        )
        if error_response:
            return error_response
        return JSONResponse(payload)

    @app.post("/snaptrade/api/{namespace}/{operation}")
    async def snaptrade_api_post(namespace: str, operation: str, request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "invalid_request_body", "detail": "Body must be a JSON object."},
                status_code=400,
            )

        args = body.get("args", [])
        kwargs = body.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            return JSONResponse(
                {
                    "error": "invalid_arguments",
                    "detail": 'Use JSON body: {"args": [], "kwargs": {}}',
                },
                status_code=400,
            )

        payload, error_response = await _invoke_sdk_operation(
            context=context,
            mapping=mapping,
            namespace=namespace,
            operation=operation,
            args=args,
            kwargs=kwargs,
        )
        if error_response:
            return error_response
        return JSONResponse(payload)

    @app.get("/snaptrade/status")
    async def snaptrade_status(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.api_status.check)
        except Exception as exc:
            return JSONResponse(
                {"error": "status_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        payload = getattr(response, "body", {})
        if isinstance(payload, dict):
            return JSONResponse([payload])
        if isinstance(payload, list):
            return JSONResponse(payload)
        return JSONResponse([])
