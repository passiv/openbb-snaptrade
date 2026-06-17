import re

from .config import CRYPTO_BROKERAGE_SLUGS, OPTIONS_BROKERAGE_SLUGS


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def response_body(payload):
    body = getattr(payload, "body", payload)
    if isinstance(body, (dict, list, str, int, float, bool)) or body is None:
        return body
    if isinstance(body, bytes):
        try:
            return body.decode("utf-8")
        except Exception:
            return str(body)
    return str(body)


def derive_asset_classes(slug: str) -> list[str]:
    if not slug:
        return ["equity"]
    upper = slug.upper()
    if upper in CRYPTO_BROKERAGE_SLUGS:
        return ["crypto"]
    classes = ["equity"]
    if upper in OPTIONS_BROKERAGE_SLUGS:
        classes.append("option")
    return classes


def account_label(account: dict) -> str:
    if not isinstance(account, dict):
        return ""
    name = account.get("name") or account.get("institution_name") or "Account"
    institution = account.get("institution_name") or ""
    number = account.get("number") or ""
    suffix_bits = [bit for bit in (institution, f"…{str(number)[-4:]}" if number else "") if bit]
    suffix = " · ".join(suffix_bits)
    return f"{name} ({suffix})" if suffix else str(name)


def sum_metric(rows: list[dict], key: str) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        number = as_float(row.get(key))
        if number is None:
            continue
        total += number
        found = True
    return total if found else None


def sum_position_value(positions: list[dict], key_a: str, key_b: str) -> float | None:
    total = 0.0
    found = False
    for position in positions:
        if not isinstance(position, dict):
            continue
        a = as_float(position.get(key_a))
        b = as_float(position.get(key_b))
        if a is None or b is None:
            continue
        total += a * b
        found = True
    return total if found else None


def extract_currency(account: dict, balances: list[dict], positions: list[dict]) -> str:
    account_currency = (
        (((account.get("balance") or {}).get("total") or {}).get("currency")) if isinstance(account, dict) else None
    )
    if isinstance(account_currency, str) and account_currency:
        return account_currency

    for row in balances:
        if not isinstance(row, dict):
            continue
        candidate = row.get("currency")
        if isinstance(candidate, dict):
            candidate = candidate.get("code") or candidate.get("id")
        if isinstance(candidate, str) and candidate:
            return candidate

    for position in positions:
        if not isinstance(position, dict):
            continue
        candidate = position.get("currency")
        if isinstance(candidate, str) and candidate:
            return candidate

    return "USD"


def position_to_row(account: dict, position: dict) -> dict:
    instrument = position.get("instrument") if isinstance(position, dict) else {}
    instrument = instrument if isinstance(instrument, dict) else {}

    units = as_float(position.get("units"))
    price = as_float(position.get("price"))
    cost_basis = as_float(position.get("cost_basis"))

    market_value = None
    if units is not None and price is not None:
        market_value = units * price

    cost_basis_value = None
    if units is not None and cost_basis is not None:
        cost_basis_value = units * cost_basis

    open_pnl = None
    if market_value is not None and cost_basis_value is not None:
        open_pnl = market_value - cost_basis_value

    return {
        "account_id": str(account.get("id", "")),
        "account_name": account.get("name") or account.get("display_name") or "Unknown Account",
        "symbol": instrument.get("symbol") or instrument.get("raw_symbol") or "Unknown",
        "description": instrument.get("description") or "",
        "asset_kind": instrument.get("kind") or "unknown",
        "currency": position.get("currency") or "USD",
        "units": units,
        "price": price,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "cost_basis_value": cost_basis_value,
        "open_pnl": open_pnl,
    }


def aggregate_exposure(positions: list[dict]) -> dict:
    by_kind: dict[str, float] = {}
    total_market_value = 0.0
    gross_exposure = 0.0
    for row in positions:
        value = as_float(row.get("market_value"))
        if value is None:
            continue
        kind = str(row.get("asset_kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0.0) + value
        total_market_value += value
        gross_exposure += abs(value)

    exposures = []
    for kind, value in sorted(by_kind.items(), key=lambda item: abs(item[1]), reverse=True):
        weight = (value / gross_exposure) if gross_exposure else 0.0
        exposures.append(
            {
                "asset_kind": kind,
                "market_value": value,
                "weight": weight,
            }
        )

    top_positions = sorted(
        [row for row in positions if as_float(row.get("market_value")) is not None],
        key=lambda row: abs(as_float(row.get("market_value")) or 0.0),
        reverse=True,
    )[:25]

    return {
        "totals": {
            "positions": len(positions),
            "net_market_value": total_market_value,
            "gross_exposure": gross_exposure,
        },
        "exposures_by_kind": exposures,
        "top_positions": top_positions,
    }


def flatten_order(account: dict, order: dict) -> dict:
    universal = order.get("universal_symbol") if isinstance(order, dict) else None
    option = order.get("option_symbol") if isinstance(order, dict) else None
    symbol_text = ""
    description = ""
    if isinstance(universal, dict):
        symbol_text = universal.get("symbol") or universal.get("raw_symbol") or ""
        description = universal.get("description") or ""
    if not symbol_text and isinstance(option, dict):
        symbol_text = option.get("ticker") or ""
        description = description or option.get("option_type") or ""
    if not symbol_text:
        raw_symbol = order.get("symbol") if isinstance(order, dict) else None
        if isinstance(raw_symbol, dict):
            symbol_text = raw_symbol.get("symbol") or raw_symbol.get("raw_symbol") or ""
            description = description or raw_symbol.get("description") or ""

    return {
        "account_id": str(account.get("id", "")) if isinstance(account, dict) else "",
        "account_name": account.get("name") if isinstance(account, dict) else "",
        "brokerage_order_id": order.get("brokerage_order_id") or order.get("id") or "",
        "status": order.get("status") or "",
        "symbol": symbol_text,
        "description": description,
        "action": order.get("action") or "",
        "order_type": order.get("order_type") or "",
        "time_in_force": order.get("time_in_force") or "",
        "total_quantity": as_float(order.get("total_quantity")),
        "filled_quantity": as_float(order.get("filled_quantity")),
        "open_quantity": as_float(order.get("open_quantity")),
        "canceled_quantity": as_float(order.get("canceled_quantity")),
        "execution_price": as_float(order.get("execution_price")),
        "limit_price": as_float(order.get("limit_price")),
        "stop_price": as_float(order.get("stop_price")),
        "time_placed": order.get("time_placed") or "",
        "time_updated": order.get("time_updated") or "",
        "time_executed": order.get("time_executed") or "",
        "expiry_date": order.get("expiry_date") or "",
    }


def flatten_activity(activity: dict) -> dict:
    if not isinstance(activity, dict):
        return {}
    account = activity.get("account") if isinstance(activity.get("account"), dict) else {}
    symbol_obj = activity.get("symbol") if isinstance(activity.get("symbol"), dict) else {}
    option_obj = activity.get("option_symbol") if isinstance(activity.get("option_symbol"), dict) else {}
    currency_obj = activity.get("currency") if isinstance(activity.get("currency"), dict) else {}
    currency_code = currency_obj.get("code") if isinstance(currency_obj, dict) else activity.get("currency")
    symbol_text = (
        symbol_obj.get("symbol")
        or symbol_obj.get("raw_symbol")
        or option_obj.get("ticker")
        or activity.get("symbol_description")
        or ""
    )
    return {
        "id": activity.get("id") or "",
        "trade_date": activity.get("trade_date") or "",
        "settlement_date": activity.get("settlement_date") or "",
        "account_id": str(account.get("id", "")) if isinstance(account, dict) else "",
        "account_name": account.get("name") if isinstance(account, dict) else "",
        "type": activity.get("type") or "",
        "symbol": symbol_text,
        "description": activity.get("description") or "",
        "units": as_float(activity.get("units")),
        "price": as_float(activity.get("price")),
        "amount": as_float(activity.get("amount")),
        "fee": as_float(activity.get("fee")),
        "fx_rate": as_float(activity.get("fx_rate")),
        "currency": currency_code or "",
        "institution": activity.get("institution") or "",
        "external_reference_id": activity.get("external_reference_id") or "",
    }


def flatten_balance_point(account: dict, point: dict, currency_code: str = "") -> dict:
    if not isinstance(point, dict):
        return {}
    point_currency = point.get("currency")
    if isinstance(point_currency, dict):
        resolved_currency = point_currency.get("code") or currency_code
    else:
        resolved_currency = str(point_currency or "") or currency_code
    return {
        "account_id": str(account.get("id", "")) if isinstance(account, dict) else "",
        "account_name": account.get("name") if isinstance(account, dict) else "",
        "date": point.get("date") or "",
        "amount": as_float(point.get("total_value") if point.get("total_value") is not None else point.get("amount")),
        "currency": resolved_currency,
    }


def flatten_brokerage(brokerage: dict) -> dict:
    if not isinstance(brokerage, dict):
        return {}
    return {
        "id": brokerage.get("id") or "",
        "slug": brokerage.get("slug") or "",
        "name": brokerage.get("name") or brokerage.get("display_name") or "",
        "display_name": brokerage.get("display_name") or brokerage.get("name") or "",
        "description": brokerage.get("description") or "",
        "url": brokerage.get("url") or "",
        "open_url": brokerage.get("open_url") or "",
        "logo_url": brokerage.get("aws_s3_logo_url") or brokerage.get("aws_s3_square_logo_url") or "",
        "enabled": bool(brokerage.get("enabled")),
        "maintenance_mode": bool(brokerage.get("maintenance_mode")),
        "allows_trading": bool(brokerage.get("allows_trading")),
        "allows_fractional_units": bool(brokerage.get("allows_fractional_units")),
    }


def flatten_symbol(symbol: dict) -> dict:
    if not isinstance(symbol, dict):
        return {}
    currency = symbol.get("currency")
    currency_code = currency.get("code") if isinstance(currency, dict) else str(currency or "")
    exchange = symbol.get("exchange")
    exchange_code = exchange.get("code") if isinstance(exchange, dict) else str(exchange or "")
    sec_type = symbol.get("type")
    type_code = sec_type.get("code") if isinstance(sec_type, dict) else str(sec_type or "")
    return {
        "id": symbol.get("id") or "",
        "symbol": symbol.get("symbol") or symbol.get("raw_symbol") or "",
        "raw_symbol": symbol.get("raw_symbol") or "",
        "description": symbol.get("description") or "",
        "currency": currency_code,
        "exchange": exchange_code,
        "type": type_code,
        "figi_code": symbol.get("figi_code") or "",
    }


def flatten_exchange(exchange: dict) -> dict:
    if not isinstance(exchange, dict):
        return {}
    return {
        "code": exchange.get("code") or "",
        "mic_code": exchange.get("mic_code") or "",
        "name": exchange.get("name") or "",
        "suffix": exchange.get("suffix") or "",
        "timezone": exchange.get("timezone") or "",
        "start_time": exchange.get("start_time") or "",
        "close_time": exchange.get("close_time") or "",
        "id": exchange.get("id") or "",
    }


def flatten_security_type(sec: dict) -> dict:
    if not isinstance(sec, dict):
        return {}
    return {
        "code": sec.get("code") or "",
        "description": sec.get("description") or "",
        "is_supported": bool(sec.get("is_supported")),
        "id": sec.get("id") or "",
    }


def flatten_currency(currency: dict) -> dict:
    if not isinstance(currency, dict):
        return {}
    return {
        "code": currency.get("code") or "",
        "name": currency.get("name") or "",
        "id": currency.get("id") or "",
    }


def flatten_fx_rate(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    src = row.get("src") if isinstance(row.get("src"), dict) else {}
    dst = row.get("dst") if isinstance(row.get("dst"), dict) else {}
    src_code = src.get("code") or ""
    dst_code = dst.get("code") or ""
    return {
        "pair": f"{src_code}-{dst_code}" if src_code and dst_code else "",
        "src_code": src_code,
        "src_name": src.get("name") or "",
        "dst_code": dst_code,
        "dst_name": dst.get("name") or "",
        "exchange_rate": as_float(row.get("exchange_rate")),
    }


def flatten_broker_instrument(instrument: dict) -> dict:
    if not isinstance(instrument, dict):
        return {}
    return {
        "symbol": instrument.get("symbol") or "",
        "exchange_mic": instrument.get("exchange_mic") or "",
        "tradeable": bool(instrument.get("tradeable")),
        "fractionable": (
            True
            if instrument.get("fractionable") is True
            else (False if instrument.get("fractionable") is False else None)
        ),
        "universal_symbol_id": instrument.get("universal_symbol_id") or "",
    }


def flatten_account_quote(quote: dict) -> dict:
    if not isinstance(quote, dict):
        return {}
    symbol_obj = quote.get("symbol") if isinstance(quote.get("symbol"), dict) else {}
    currency = symbol_obj.get("currency") if isinstance(symbol_obj.get("currency"), dict) else {}
    exchange = symbol_obj.get("exchange") if isinstance(symbol_obj.get("exchange"), dict) else {}
    bid = as_float(quote.get("bid_price"))
    ask = as_float(quote.get("ask_price"))
    last = as_float(quote.get("last_trade_price"))
    spread = None
    if bid is not None and ask is not None:
        spread = ask - bid
    mid = None
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    return {
        "symbol": symbol_obj.get("symbol") or symbol_obj.get("raw_symbol") or "",
        "description": symbol_obj.get("description") or "",
        "exchange": exchange.get("code") or "",
        "currency": currency.get("code") or "",
        "bid_price": bid,
        "ask_price": ask,
        "mid_price": mid,
        "spread": spread,
        "last_trade_price": last,
        "bid_size": as_float(quote.get("bid_size")),
        "ask_size": as_float(quote.get("ask_size")),
        "universal_symbol_id": symbol_obj.get("id") or "",
    }


def flatten_crypto_instrument(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    base_symbol = base.get("symbol") or item.get("base_currency") or ""
    quote_symbol = quote.get("symbol") or item.get("quote_currency") or ""
    pair_symbol = item.get("symbol") or (f"{base_symbol}-{quote_symbol}" if base_symbol and quote_symbol else "")
    return {
        "id": item.get("id") or pair_symbol,
        "symbol": pair_symbol,
        "raw_symbol": item.get("raw_symbol") or pair_symbol,
        "description": item.get("description") or f"{base_symbol}/{quote_symbol}".strip("/"),
        "base": base_symbol,
        "quote": quote_symbol,
        "type": item.get("type") or "CRYPTOCURRENCY_PAIR",
    }
