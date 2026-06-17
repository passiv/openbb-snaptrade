import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pywry.models import HtmlContent

from ..config import IFRAME_HEADERS, STATIC_DIR
from ..context import resolve_workspace_context
from ..auth import SESSION_MANAGER
from ..iframe import (
    not_found_response,
    render_html_content,
)
from ..snaptrade_client import (
    ensure_mapping,
    fetch_accounts,
    fetch_connections,
    is_personal_client,
    missing_user_secret_response,
    snaptrade_client,
    snaptrade_credentials,
)
from ..transforms import (
    account_label,
    as_float,
    derive_asset_classes,
    flatten_account_quote,
    flatten_crypto_instrument,
    flatten_symbol,
)


_TEMPLATE = STATIC_DIR / "snaptrade_trade.html"


def build_trade_content() -> HtmlContent:
    return HtmlContent(
        html=_TEMPLATE.read_text(encoding="utf-8"),
        css_files=[STATIC_DIR / "snaptrade_trade.css"],
        script_files=[
            STATIC_DIR / "openbb_iframe_bridge.js",
            STATIC_DIR / "snaptrade_trade.js",
        ],
    )


TRADE_CONTENT = build_trade_content()


async def _crypto_order(request: Request, place: bool):
    context = await resolve_workspace_context(request)
    if not context:
        return not_found_response()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    account_id = str(payload.get("accountId") or "").strip()
    instrument_symbol = str(payload.get("instrumentSymbol") or "").strip()
    instrument_type = str(payload.get("instrumentType") or "CRYPTOCURRENCY_PAIR").strip()
    side = str(payload.get("side") or "").strip().upper()
    order_type = str(payload.get("orderType") or "MARKET").strip().upper()
    time_in_force = str(payload.get("timeInForce") or "GTC").strip().upper()
    amount = payload.get("amount")
    limit_price = payload.get("limitPrice")
    stop_price = payload.get("stopPrice")
    post_only = payload.get("postOnly")

    if not account_id or not instrument_symbol or side not in {"BUY", "SELL"} or amount is None:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    mapping = await ensure_mapping(context)
    if not is_personal_client(context) and not mapping.snaptrade_user_secret:
        return missing_user_secret_response()
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)

    instrument = {"symbol": instrument_symbol, "type": instrument_type}
    kwargs = {
        "user_id": user_id,
        "user_secret": user_secret,
        "account_id": account_id,
        "instrument": instrument,
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
        "amount": str(amount),
    }
    if limit_price not in (None, ""):
        kwargs["limit_price"] = str(limit_price)
    if stop_price not in (None, ""):
        kwargs["stop_price"] = str(stop_price)
    if post_only is not None:
        kwargs["post_only"] = bool(post_only)

    method = client.trading.place_crypto_order if place else client.trading.preview_crypto_order
    try:
        response = await asyncio.to_thread(method, **kwargs)
    except Exception as exc:
        return JSONResponse(
            {"error": "crypto_order_failed", "detail": getattr(exc, "body", str(exc))},
            status_code=502,
        )
    body = getattr(response, "body", {})
    return JSONResponse(body if isinstance(body, (dict, list)) else {})


async def _options_order(request: Request, place: bool):
    context = await resolve_workspace_context(request)
    if not context:
        return not_found_response()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    account_id = str(payload.get("accountId") or "").strip()
    order_type = str(payload.get("orderType") or "MARKET").strip().upper()
    time_in_force = str(payload.get("timeInForce") or "Day").strip()
    price_effect = str(payload.get("priceEffect") or "").strip().upper() or None
    limit_price = payload.get("limitPrice")
    stop_price = payload.get("stopPrice")
    legs_in = payload.get("legs") or []

    if not account_id or not isinstance(legs_in, list) or not legs_in:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)
    if order_type not in {"MARKET", "LIMIT", "STOP_LOSS_MARKET", "STOP_LOSS_LIMIT"}:
        return JSONResponse({"error": "invalid_order_type"}, status_code=400)
    if time_in_force not in {"FOK", "Day", "GTC", "IOC"}:
        return JSONResponse({"error": "invalid_time_in_force"}, status_code=400)

    legs: list[dict] = []
    for raw in legs_in:
        if not isinstance(raw, dict):
            continue
        sym = str(raw.get("symbol") or raw.get("instrumentSymbol") or "").strip()
        action = str(raw.get("action") or "").strip().upper()
        units = raw.get("units")
        units_i = None
        try:
            if units is not None:
                units_i = int(float(units))
        except (TypeError, ValueError):
            units_i = None
        if (
            not sym
            or action not in {"BUY", "SELL", "BUY_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_OPEN", "SELL_TO_CLOSE"}
            or not units_i
            or units_i <= 0
        ):
            return JSONResponse(
                {"error": "invalid_leg", "detail": "Each leg requires symbol, action, and units (>0)."},
                status_code=400,
            )
        instrument_type = str(raw.get("instrumentType") or "OPTION").strip().upper()
        legs.append(
            {
                "instrument": {"symbol": sym, "instrument_type": instrument_type},
                "action": action,
                "units": units_i,
            }
        )

    mapping = await ensure_mapping(context)
    if not is_personal_client(context) and not mapping.snaptrade_user_secret:
        return missing_user_secret_response()
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)

    kwargs = {
        "user_id": user_id,
        "user_secret": user_secret,
        "account_id": account_id,
        "order_type": order_type,
        "time_in_force": time_in_force,
        "legs": legs,
    }
    if limit_price not in (None, ""):
        kwargs["limit_price"] = str(limit_price)
    if stop_price not in (None, ""):
        kwargs["stop_price"] = str(stop_price)
    if price_effect in {"CREDIT", "DEBIT", "EVEN"}:
        kwargs["price_effect"] = price_effect

    method = client.trading.place_mleg_order if place else client.trading.get_option_impact
    try:
        response = await asyncio.to_thread(method, **kwargs)
    except Exception as exc:
        return JSONResponse(
            {"error": "options_order_failed", "detail": getattr(exc, "body", str(exc))},
            status_code=502,
        )
    body = getattr(response, "body", {})
    return JSONResponse(body if isinstance(body, (dict, list)) else {})


def register(app: FastAPI) -> None:
    @app.get("/trade", response_class=HTMLResponse)
    async def trade_route(request: Request):
        return HTMLResponse(
            render_html_content(TRADE_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/trade/s/{token}", response_class=HTMLResponse)
    async def trade_session_route(token: str):
        ctx = await SESSION_MANAGER.resolve(token)
        if not ctx:
            return not_found_response()
        return HTMLResponse(
            render_html_content(TRADE_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/snaptrade/trade/accounts")
    async def trade_accounts(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        connections, conn_error = await fetch_connections(context, mapping)
        if conn_error:
            connections = []
        brokerage_by_auth: dict[str, dict] = {}
        for conn in connections or []:
            if not isinstance(conn, dict):
                continue
            auth_id = str(conn.get("id") or "")
            brokerage = conn.get("brokerage") if isinstance(conn.get("brokerage"), dict) else {}
            if auth_id and brokerage:
                brokerage_by_auth[auth_id] = brokerage

        rows: list[dict] = []
        for account in accounts or []:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id") or "")
            if not account_id:
                continue
            auth_id = str(
                account.get("brokerage_authorization")
                or account.get("connection_id")
                or account.get("connectionId")
                or ""
            )
            brokerage = brokerage_by_auth.get(auth_id, {})
            slug = str(brokerage.get("slug") or "").upper()
            allows_trading = bool(brokerage.get("allows_trading"))
            allows_fractional = bool(brokerage.get("allows_fractional_units"))
            asset_classes = derive_asset_classes(slug)
            rows.append(
                {
                    "id": account_id,
                    "label": account_label(account),
                    "name": account.get("name") or "",
                    "institution_name": account.get("institution_name") or "",
                    "number": account.get("number") or "",
                    "brokerage_slug": slug,
                    "brokerage_name": brokerage.get("display_name") or brokerage.get("name") or "",
                    "allows_trading": allows_trading,
                    "allows_fractional_units": allows_fractional,
                    "asset_classes": asset_classes,
                }
            )
        rows.sort(key=lambda r: str(r.get("label") or "").lower())
        return JSONResponse(rows)

    @app.get("/snaptrade/trade/symbol-search")
    async def trade_symbol_search(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        account_id = (request.query_params.get("accountId") or "").strip()
        query = (request.query_params.get("q") or "").strip()
        asset_class = (request.query_params.get("assetClass") or "equity").strip().lower()
        if not account_id or not query:
            return JSONResponse([])

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        if asset_class == "crypto":
            base_token = query.split("-")[0].split("/")[0].upper()
            try:
                response = await asyncio.to_thread(
                    client.trading.search_cryptocurrency_pair_instruments,
                    user_id=user_id,
                    user_secret=user_secret,
                    account_id=account_id,
                    base=base_token,
                )
            except Exception as exc:
                return JSONResponse(
                    {"error": "crypto_search_failed", "detail": getattr(exc, "body", str(exc))},
                    status_code=502,
                )
            body = getattr(response, "body", [])
            if isinstance(body, dict):
                body = body.get("instruments") or body.get("results") or []
            if not isinstance(body, list):
                body = []
            rows = [flatten_crypto_instrument(item) for item in body if isinstance(item, dict)]
            return JSONResponse(rows)

        try:
            response = await asyncio.to_thread(
                client.reference_data.symbol_search_user_account,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
                substring=query,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "symbol_search_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", [])
        if not isinstance(body, list):
            body = []
        rows = [flatten_symbol(item) for item in body if isinstance(item, dict)]
        return JSONResponse(rows)

    @app.get("/snaptrade/trade/quote")
    async def trade_quote(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        account_id = (request.query_params.get("accountId") or "").strip()
        symbol = (request.query_params.get("symbol") or "").strip()
        asset_class = (request.query_params.get("assetClass") or "equity").strip().lower()
        if not account_id or not symbol:
            return JSONResponse({"error": "missing_param"}, status_code=400)

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        if asset_class == "crypto":
            try:
                response = await asyncio.to_thread(
                    client.trading.get_cryptocurrency_pair_quote,
                    user_id=user_id,
                    user_secret=user_secret,
                    account_id=account_id,
                    instrument_symbol=symbol,
                )
            except Exception as exc:
                return JSONResponse(
                    {"error": "quote_failed", "detail": getattr(exc, "body", str(exc))},
                    status_code=502,
                )
            body = getattr(response, "body", {})
            if not isinstance(body, dict):
                body = {}
            return JSONResponse(
                {
                    "symbol": symbol,
                    "bid_price": as_float(body.get("bid_price")),
                    "ask_price": as_float(body.get("ask_price")),
                    "last_trade_price": as_float(body.get("last_trade_price") or body.get("last_price")),
                }
            )

        try:
            response = await asyncio.to_thread(
                client.trading.get_user_account_quotes,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
                symbols=symbol.upper(),
                use_ticker=True,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "quote_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", [])
        if isinstance(body, dict):
            body = body.get("quotes") or []
        if isinstance(body, list) and body:
            return JSONResponse(flatten_account_quote(body[0]))
        return JSONResponse({})

    @app.post("/snaptrade/trade/impact")
    async def trade_impact(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        account_id = str(payload.get("accountId") or "").strip()
        action = str(payload.get("action") or "").strip().upper()
        order_type = str(payload.get("orderType") or "Market").strip()
        time_in_force = str(payload.get("timeInForce") or "Day").strip()
        universal_symbol_id = str(payload.get("universalSymbolId") or "").strip()
        units = payload.get("units")
        notional = payload.get("notionalValue")
        price = payload.get("price")
        stop = payload.get("stop")

        if not account_id or not universal_symbol_id or action not in {"BUY", "SELL"}:
            return JSONResponse({"error": "invalid_payload"}, status_code=400)

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        kwargs = {
            "user_id": user_id,
            "user_secret": user_secret,
            "account_id": account_id,
            "action": action,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "universal_symbol_id": universal_symbol_id,
        }
        units_f = as_float(units)
        notional_f = as_float(notional)
        price_f = as_float(price)
        stop_f = as_float(stop)
        if units_f is not None:
            kwargs["units"] = units_f
        if notional_f is not None:
            kwargs["notional_value"] = notional_f
        if price_f is not None:
            kwargs["price"] = price_f
        if stop_f is not None:
            kwargs["stop"] = stop_f

        try:
            response = await asyncio.to_thread(client.trading.get_order_impact, **kwargs)
        except Exception as exc:
            return JSONResponse(
                {"error": "impact_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", {})
        return JSONResponse(body if isinstance(body, (dict, list)) else {})

    @app.post("/snaptrade/trade/place")
    async def trade_place(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        trade_id = str(payload.get("tradeId") or "").strip()
        if not trade_id:
            return JSONResponse({"error": "missing_trade_id"}, status_code=400)
        wait_to_confirm = bool(payload.get("waitToConfirm", True))

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        try:
            response = await asyncio.to_thread(
                client.trading.place_order,
                trade_id=trade_id,
                user_id=user_id,
                user_secret=user_secret,
                wait_to_confirm=wait_to_confirm,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "place_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", {})
        return JSONResponse(body if isinstance(body, (dict, list)) else {})

    @app.post("/snaptrade/trade/crypto/preview")
    async def trade_crypto_preview(request: Request):
        return await _crypto_order(request, place=False)

    @app.post("/snaptrade/trade/crypto/place")
    async def trade_crypto_place(request: Request):
        return await _crypto_order(request, place=True)

    @app.post("/snaptrade/trade/force")
    async def trade_force(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        account_id = str(payload.get("accountId") or "").strip()
        action = str(payload.get("action") or "").strip().upper()
        order_type = str(payload.get("orderType") or "Market").strip()
        time_in_force = str(payload.get("timeInForce") or "Day").strip()
        trading_session = str(payload.get("tradingSession") or "REGULAR").strip().upper()
        universal_symbol_id = str(payload.get("universalSymbolId") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        units = payload.get("units")
        notional = payload.get("notionalValue")
        price = payload.get("price")
        stop = payload.get("stop")

        if not account_id or action not in {
            "BUY",
            "SELL",
            "BUY_TO_OPEN",
            "BUY_TO_CLOSE",
            "SELL_TO_OPEN",
            "SELL_TO_CLOSE",
        }:
            return JSONResponse({"error": "invalid_payload"}, status_code=400)
        if trading_session == "EXTENDED" and order_type.lower() != "limit":
            return JSONResponse(
                {"error": "invalid_payload", "detail": "Extended hours requires a Limit order."},
                status_code=400,
            )

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        kwargs = {
            "user_id": user_id,
            "user_secret": user_secret,
            "account_id": account_id,
            "action": action,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "trading_session": trading_session,
        }
        if universal_symbol_id:
            kwargs["universal_symbol_id"] = universal_symbol_id
        if symbol:
            kwargs["symbol"] = symbol
        units_f = as_float(units)
        notional_f = as_float(notional)
        price_f = as_float(price)
        stop_f = as_float(stop)
        if units_f is not None:
            kwargs["units"] = units_f
        if notional_f is not None:
            kwargs["notional_value"] = notional_f
        if price_f is not None:
            kwargs["price"] = price_f
        if stop_f is not None:
            kwargs["stop"] = stop_f

        try:
            response = await asyncio.to_thread(client.trading.place_force_order, **kwargs)
        except Exception as exc:
            return JSONResponse(
                {"error": "force_order_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", {})
        return JSONResponse(body if isinstance(body, (dict, list)) else {})

    @app.get("/snaptrade/trade/options/quote")
    async def trade_options_quote(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        account_id = (request.query_params.get("accountId") or "").strip()
        symbol = (request.query_params.get("symbol") or "").strip()
        if not account_id or not symbol:
            return JSONResponse({"error": "missing_param"}, status_code=400)

        mapping = await ensure_mapping(context)
        if not is_personal_client(context) and not mapping.snaptrade_user_secret:
            return missing_user_secret_response()
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        try:
            response = await asyncio.to_thread(
                client.trading.get_user_account_option_quotes,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
                symbol=symbol,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "option_quote_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )
        body = getattr(response, "body", {})
        if isinstance(body, list) and body:
            body = body[0]
        if not isinstance(body, dict):
            body = {}
        return JSONResponse(
            {
                "symbol": symbol,
                "bid_price": as_float(body.get("bid_price") or body.get("bid")),
                "ask_price": as_float(body.get("ask_price") or body.get("ask")),
                "last_trade_price": as_float(body.get("last_trade_price") or body.get("last")),
            }
        )

    @app.post("/snaptrade/trade/options/impact")
    async def trade_options_impact(request: Request):
        return await _options_order(request, place=False)

    @app.post("/snaptrade/trade/options/place")
    async def trade_options_place(request: Request):
        return await _options_order(request, place=True)
