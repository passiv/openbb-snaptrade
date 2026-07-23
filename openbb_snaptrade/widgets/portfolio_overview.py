import asyncio
from datetime import date as _date

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
    snaptrade_client,
    snaptrade_credentials,
)
from ..transforms import (
    account_label,
    aggregate_exposure,
    as_float,
    extract_currency,
    flatten_account_quote,
    flatten_activity,
    flatten_balance_point,
    flatten_order,
    position_to_row,
    sum_metric,
    sum_position_value,
)


_TEMPLATE = STATIC_DIR / "portfolio_overview.html"


def build_portfolio_overview_content() -> HtmlContent:
    return HtmlContent(
        html=_TEMPLATE.read_text(encoding="utf-8"),
        css_files=[STATIC_DIR / "portfolio_overview.css"],
        script_files=[
            STATIC_DIR / "openbb_iframe_bridge.js",
            STATIC_DIR / "portfolio_overview.js",
        ],
    )


PORTFOLIO_OVERVIEW_CONTENT = build_portfolio_overview_content()


def _trim_account(account: dict) -> dict:
    """Extract minimal account data for wire transmission."""
    if not isinstance(account, dict):
        return {}
    balance = account.get("balance", {})
    total = balance.get("total", {}) if isinstance(balance, dict) else {}
    meta = account.get("meta", {})
    return {
        "id": account.get("id"),
        "name": account.get("name"),
        "institution_name": account.get("institution_name"),
        "account_type": meta.get("type") or account.get("account_category"),
        "is_paper": account.get("is_paper"),
        "status": account.get("status"),
        "total_value": total.get("amount"),
        "currency": total.get("currency", "USD"),
    }


async def _fetch_account_summary(context, mapping, account):
    account_id = str(account.get("id", "")) if isinstance(account, dict) else ""
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)

    balances = []
    positions_response = {}

    if account_id:
        try:
            balance_response = await asyncio.to_thread(
                client.account_information.get_user_account_balance,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )
            body = getattr(balance_response, "body", [])
            if isinstance(body, list):
                balances = body
        except Exception:
            balances = []

        try:
            positions_resp = await asyncio.to_thread(
                client.account_information.get_all_account_positions,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )
            body = getattr(positions_resp, "body", {})
            if isinstance(body, dict):
                positions_response = body
        except Exception:
            positions_response = {}

    positions = positions_response.get("results") if isinstance(positions_response, dict) else []
    if not isinstance(positions, list):
        positions = []

    account_total = (
        (((account.get("balance") or {}).get("total") or {}).get("amount")) if isinstance(account, dict) else None
    )
    total_value = as_float(account_total)

    cash = sum_metric(balances, "cash")
    buying_power = sum_metric(balances, "buying_power")
    market_value = sum_position_value(positions, "units", "price")
    cost_basis = sum_position_value(positions, "units", "cost_basis")
    open_pnl = None
    if market_value is not None and cost_basis is not None:
        open_pnl = market_value - cost_basis

    currency = extract_currency(account if isinstance(account, dict) else {}, balances, positions)

    return {
        "account_id": account_id,
        "connection_id": str(
            (account.get("brokerage_authorization") or account.get("connection_id") or account.get("connectionId") or "")
        )
        if isinstance(account, dict)
        else "",
        "currency": currency,
        "total_value": total_value,
        "cash": cash,
        "buying_power": buying_power,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "open_pnl": open_pnl,
        "positions_count": len(positions),
    }


async def _fetch_account_summaries(context, mapping, accounts):
    tasks = [_fetch_account_summary(context, mapping, account) for account in accounts if isinstance(account, dict)]
    if not tasks:
        return []
    return await asyncio.gather(*tasks)


async def _fetch_portfolio_exposure(context, mapping, accounts):
    user_id, user_secret = snaptrade_credentials(context, mapping)
    client = snaptrade_client(context)

    rows: list[dict] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        account_id = str(account.get("id", ""))
        if not account_id:
            continue
        try:
            response = await asyncio.to_thread(
                client.account_information.get_all_account_positions,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
            )
            body = getattr(response, "body", {})
            positions = body.get("results") if isinstance(body, dict) else []
            if not isinstance(positions, list):
                positions = []
            for position in positions:
                if isinstance(position, dict):
                    rows.append(position_to_row(account, position))
        except Exception:
            continue

    aggregate = aggregate_exposure(rows)
    aggregate["positions"] = rows
    return aggregate


def register(app: FastAPI) -> None:
    @app.get("/portfolio-overview", response_class=HTMLResponse)
    async def portfolio_overview_route(request: Request):
        return HTMLResponse(
            render_html_content(PORTFOLIO_OVERVIEW_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/portfolio-overview/s/{token}", response_class=HTMLResponse)
    async def portfolio_overview_session_route(token: str):
        ctx = await SESSION_MANAGER.resolve(token)
        if not ctx:
            return not_found_response()
        return HTMLResponse(
            render_html_content(PORTFOLIO_OVERVIEW_CONTENT),
            headers=IFRAME_HEADERS,
        )

    @app.get("/snaptrade/accounts")
    async def list_accounts(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response
        return JSONResponse([_trim_account(acc) for acc in (accounts or [])])

    @app.get("/snaptrade/account-summaries")
    async def list_account_summaries(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        summaries = await _fetch_account_summaries(context, mapping, accounts)
        return JSONResponse(summaries)

    @app.get("/snaptrade/portfolio-exposure")
    async def portfolio_exposure(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        data = await _fetch_portfolio_exposure(context, mapping, accounts)
        return JSONResponse(data)

    @app.get("/snaptrade/account-options")
    async def account_options(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        options = [{"label": "All accounts", "value": "__all__"}]
        for account in accounts or []:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id", ""))
            if not account_id:
                continue
            options.append({"label": account_label(account), "value": account_id})
        return JSONResponse(options)

    @app.get("/snaptrade/account-options-required")
    async def account_options_required(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        options: list[dict] = []
        for account in accounts or []:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("id", ""))
            if not account_id:
                continue
            options.append({"label": account_label(account), "value": account_id})
        return JSONResponse(options)

    @app.get("/snaptrade/orders")
    async def list_orders(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        account_filter = (request.query_params.get("accountId") or "").strip()
        if account_filter == "__all__":
            account_filter = ""
        state = (request.query_params.get("state") or "all").strip().lower()
        days_param = request.query_params.get("days")
        try:
            days = int(days_param) if days_param else 30
        except (TypeError, ValueError):
            days = 30

        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        target_accounts = [a for a in (accounts or []) if isinstance(a, dict)]
        if account_filter:
            target_accounts = [a for a in target_accounts if str(a.get("id", "")) == account_filter]

        async def _fetch_one(account: dict) -> list[dict]:
            account_id = str(account.get("id", ""))
            if not account_id:
                return []
            try:
                if state == "recent":
                    response = await asyncio.to_thread(
                        client.account_information.get_user_account_recent_orders,
                        user_id=user_id,
                        user_secret=user_secret,
                        account_id=account_id,
                    )
                else:
                    sdk_state = None if state in ("all", "") else state
                    response = await asyncio.to_thread(
                        client.account_information.get_user_account_orders,
                        user_id=user_id,
                        user_secret=user_secret,
                        account_id=account_id,
                        state=sdk_state,
                        days=days,
                    )
            except Exception:
                return []
            body = getattr(response, "body", [])
            if isinstance(body, dict):
                body = body.get("orders") or body.get("results") or []
            if not isinstance(body, list):
                return []
            return [flatten_order(account, order) for order in body if isinstance(order, dict)]

        results = await asyncio.gather(*[_fetch_one(a) for a in target_accounts])
        rows: list[dict] = []
        for batch in results:
            rows.extend(batch)
        rows.sort(key=lambda r: str(r.get("time_placed") or ""), reverse=True)
        return JSONResponse(rows)

    @app.get("/snaptrade/activities")
    async def list_activities(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        user_id, user_secret = snaptrade_credentials(context, mapping)

        start_date = (request.query_params.get("startDate") or "").strip()
        end_date = (request.query_params.get("endDate") or "").strip()
        account_filter = (request.query_params.get("accounts") or "").strip()
        if account_filter == "__all__":
            account_filter = ""
        activity_type = (request.query_params.get("type") or "").strip()
        if activity_type == "__all__":
            activity_type = ""

        def _parse(value: str):
            if not value:
                return None
            try:
                return _date.fromisoformat(value)
            except ValueError:
                return None

        start_d = _parse(start_date)
        end_d = _parse(end_date)

        target_accounts = [a for a in (accounts or []) if isinstance(a, dict)]
        if account_filter:
            target_accounts = [a for a in target_accounts if str(a.get("id", "")) == account_filter]

        client = snaptrade_client(context)

        async def _fetch_one(account: dict) -> list[dict]:
            account_id = str(account.get("id", ""))
            if not account_id:
                return []
            kwargs = {
                "account_id": account_id,
                "user_id": user_id,
                "user_secret": user_secret,
                "start_date": start_d,
                "end_date": end_d,
                "type": activity_type or None,
            }
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            try:
                response = await asyncio.to_thread(
                    client.account_information.get_account_activities,
                    **kwargs,
                )
            except Exception:
                return []
            body = getattr(response, "body", [])
            if isinstance(body, dict):
                body = body.get("data") or body.get("results") or []
            if not isinstance(body, list):
                return []
            rows = []
            for item in body:
                if not isinstance(item, dict):
                    continue
                if not isinstance(item.get("account"), dict):
                    item = dict(item)
                    item["account"] = {"id": account_id, "name": account.get("name")}
                rows.append(flatten_activity(item))
            return rows

        results = await asyncio.gather(*[_fetch_one(a) for a in target_accounts])
        rows: list[dict] = []
        for batch in results:
            rows.extend(batch)
        rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
        return JSONResponse(rows)

    @app.get("/snaptrade/balance-history")
    async def balance_history(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        mapping = await ensure_mapping(context)
        accounts, error_response = await fetch_accounts(context, mapping)
        if error_response:
            return error_response

        account_filter = (request.query_params.get("accountId") or "").strip()
        if account_filter == "__all__":
            account_filter = ""
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)

        target_accounts = [a for a in (accounts or []) if isinstance(a, dict)]
        if account_filter:
            target_accounts = [a for a in target_accounts if str(a.get("id", "")) == account_filter]

        async def _fetch_one(account: dict) -> list[dict]:
            account_id = str(account.get("id", ""))
            if not account_id:
                return []
            try:
                response = await asyncio.to_thread(
                    client.account_information.get_account_balance_history,
                    user_id=user_id,
                    user_secret=user_secret,
                    account_id=account_id,
                )
            except Exception:
                return []
            body = getattr(response, "body", [])
            currency_code = ""
            if isinstance(body, dict):
                response_currency = body.get("currency")
                if isinstance(response_currency, dict):
                    currency_code = response_currency.get("code") or ""
                elif isinstance(response_currency, str):
                    currency_code = response_currency
                body = body.get("history") or body.get("results") or []
            if not isinstance(body, list):
                return []
            return [flatten_balance_point(account, point, currency_code) for point in body if isinstance(point, dict)]

        results = await asyncio.gather(*[_fetch_one(a) for a in target_accounts])
        rows: list[dict] = []
        for batch in results:
            rows.extend(batch)
        rows.sort(key=lambda r: str(r.get("date") or ""))
        return JSONResponse(rows)

    @app.get("/snaptrade/quotes")
    async def account_quotes(request: Request):
        context = await resolve_workspace_context(request)
        if not context:
            return not_found_response()

        account_id = (request.query_params.get("accountId") or "").strip()
        if not account_id or account_id == "__all__":
            return JSONResponse(
                {"error": "account_required", "detail": "Select a specific account."},
                status_code=400,
            )

        symbols_raw = (request.query_params.get("symbols") or "").strip()
        if not symbols_raw:
            return JSONResponse([])

        symbols_list = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
        if not symbols_list:
            return JSONResponse([])
        if len(symbols_list) > 10:
            return JSONResponse(
                {
                    "error": "too_many_symbols",
                    "detail": "SnapTrade allows a maximum of 10 symbols per quote request.",
                },
                status_code=400,
            )
        symbols_param = ",".join(symbols_list)

        mapping = await ensure_mapping(context)
        user_id, user_secret = snaptrade_credentials(context, mapping)
        client = snaptrade_client(context)
        try:
            response = await asyncio.to_thread(
                client.trading.get_user_account_quotes,
                user_id=user_id,
                user_secret=user_secret,
                account_id=account_id,
                symbols=symbols_param,
                use_ticker=True,
            )
        except Exception as exc:
            return JSONResponse(
                {"error": "quotes_fetch_failed", "detail": getattr(exc, "body", str(exc))},
                status_code=502,
            )

        body = getattr(response, "body", [])
        if isinstance(body, dict):
            body = body.get("quotes") or body.get("results") or []
        if not isinstance(body, list):
            return JSONResponse([])
        rows = [flatten_account_quote(item) for item in body if isinstance(item, dict)]
        rows.sort(key=lambda r: str(r.get("symbol") or "").upper())
        return JSONResponse(rows)
