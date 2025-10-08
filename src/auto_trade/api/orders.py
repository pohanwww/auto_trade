import shioaji as sj


def create_market_order(symbol, action, quantity):
    """純函數：建立市價單"""
    return {
        "symbol": symbol,
        "action": action,  # 'Buy' or 'Sell'
        "quantity": quantity,
        "price_type": "Market",
        "order_type": "ROD",
    }


def create_limit_order(symbol, action, quantity, price):
    """純函數：建立限價單"""
    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "price": price,
        "price_type": "Limit",
        "order_type": "ROD",
    }


def place_order(api_client, order_data):
    """執行下單（副作用函數）"""
    contract = api_client.Contracts.Stocks[order_data["symbol"]]

    order = api_client.Order(
        price=order_data.get("price", 0),
        quantity=order_data["quantity"],
        action=getattr(sj.constant.Action, order_data["action"]),
        price_type=getattr(
            sj.constant.StockPriceType,
            "MKT" if order_data["price_type"] == "Market" else "LMT",
        ),
        order_type=sj.constant.OrderType.ROD,
        account=api_client.stock_account,
    )

    result = api_client.place_order(contract, order)
    return format_order_result(result)


def format_order_result(result):
    """純函數：格式化下單結果"""
    return {
        "order_id": result.order.id,
        "status": result.status.status,
        "msg": result.status.msg,
    }
