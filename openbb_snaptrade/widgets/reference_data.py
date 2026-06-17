import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import BROKER_INSTRUMENTS_CACHE_PREFIX, BROKER_INSTRUMENTS_CACHE_TTL_SECONDS
from ..context import resolve_workspace_context
from ..iframe import not_found_response
from ..snaptrade_client import USER_STORE, snaptrade_client
from ..transforms import (
    flatten_brokerage,
    flatten_broker_instrument,
    flatten_currency,
    flatten_exchange,
    flatten_fx_rate,
    flatten_security_type,
    flatten_symbol,
)


def register(app: FastAPI) -> None:
    @app.get("/snaptrade/brokerages")
    async def list_brokerages(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.list_all_brokerages)
        except Exception as exc:
            return JSONResponse(
                {"error": "brokerages_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_brokerage(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: str(r.get("display_name") or r.get("name") or "").lower())
        return JSONResponse(rows)

    @app.get("/snaptrade/symbol-search")
    async def symbol_search(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        query = (request.query_params.get("query") or "").strip()
        if not query:
            return JSONResponse([])

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(
                client.reference_data.get_symbols,
                substring=query,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "symbol_search_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_symbol(item) for item in body if isinstance(item, dict)]
        return JSONResponse(rows)

    @app.get("/snaptrade/reference/exchanges")
    async def reference_exchanges(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.get_stock_exchanges)
        except Exception as exc:
            return JSONResponse(
                {"error": "exchanges_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_exchange(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: str(r.get("code") or "").lower())
        return JSONResponse(rows)

    @app.get("/snaptrade/reference/security-types")
    async def reference_security_types(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.get_security_types)
        except Exception as exc:
            return JSONResponse(
                {"error": "security_types_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_security_type(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: str(r.get("code") or "").lower())
        return JSONResponse(rows)

    @app.get("/snaptrade/reference/currencies")
    async def reference_currencies(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.list_all_currencies)
        except Exception as exc:
            return JSONResponse(
                {"error": "currencies_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_currency(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: str(r.get("code") or "").lower())
        return JSONResponse(rows)

    @app.get("/snaptrade/reference/fx-rates")
    async def reference_fx_rates(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.list_all_currencies_rates)
        except Exception as exc:
            return JSONResponse(
                {"error": "fx_rates_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])

        src_filter = (request.query_params.get("src") or "").strip().upper()
        dst_filter = (request.query_params.get("dst") or "").strip().upper()

        rows = [flatten_fx_rate(item) for item in body if isinstance(item, dict)]
        if src_filter:
            rows = [r for r in rows if r.get("src_code") == src_filter]
        if dst_filter:
            rows = [r for r in rows if r.get("dst_code") == dst_filter]
        rows.sort(key=lambda r: (str(r.get("src_code") or ""), str(r.get("dst_code") or "")))
        return JSONResponse(rows)

    @app.get("/snaptrade/reference/broker-options")
    async def reference_broker_options(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(client.reference_data.list_all_brokerages)
        except Exception as exc:
            return JSONResponse(
                {"error": "broker_options_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if not isinstance(body, list):
            return JSONResponse([])
        options: list[dict] = []
        for b in body:
            if not isinstance(b, dict):
                continue
            slug = b.get("slug")
            if not slug:
                continue
            label = b.get("display_name") or b.get("name") or slug
            options.append({"label": str(label), "value": str(slug)})
        options.sort(key=lambda o: str(o["label"]).lower())
        return JSONResponse(options)

    @app.get("/snaptrade/reference/broker-instruments")
    async def reference_broker_instruments(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        slug = (request.query_params.get("slug") or "").strip()
        if not slug:
            return JSONResponse([])

        cache_key = BROKER_INSTRUMENTS_CACHE_PREFIX + slug.upper()
        try:
            cached = USER_STORE.redis.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                cached_rows = json.loads(cached)
                if isinstance(cached_rows, list):
                    return JSONResponse(cached_rows)
            except (TypeError, ValueError):
                pass

        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(
                client.reference_data.list_all_brokerage_instruments,
                slug=slug,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "broker_instruments_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if isinstance(body, dict):
            body = body.get("instruments") or []
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_broker_instrument(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: (str(r.get("exchange_mic") or ""), str(r.get("symbol") or "")))

        try:
            USER_STORE.redis.set(
                cache_key,
                json.dumps(rows),
                ex=BROKER_INSTRUMENTS_CACHE_TTL_SECONDS,
            )
        except Exception:
            pass

        return JSONResponse(rows)
