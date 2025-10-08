def get_account_balance(api_client, account_type="stock"):
    """純函數：取得帳戶餘額"""
    if account_type == "stock":
        settlement = api_client.list_settlements(api_client.stock_account)
    else:
        settlement = api_client.list_settlements(api_client.futopt_account)

    return format_balance_data(settlement)


def get_positions(api_client, account_type="stock"):
    """純函數：取得持倉資料"""
    if account_type == "stock":
        positions = api_client.list_positions(api_client.stock_account)
    else:
        positions = api_client.list_positions(api_client.futopt_account)

    return format_positions_data(positions)


def get_order_history(api_client):
    """純函數：取得委託紀錄"""
    orders = api_client.list_orders()
    return format_orders_data(orders)


def format_balance_data(settlement):
    """純函數：格式化餘額資料"""
    return {
        "available_balance": float(settlement.available_balance),
        "buying_power": float(settlement.buying_power),
        "account_value": float(settlement.account_value),
    }


def format_positions_data(positions):
    """純函數：格式化持倉資料"""
    return [
        {
            "symbol": pos.code,
            "quantity": pos.quantity,
            "avg_price": float(pos.price),
            "market_value": float(pos.pnl),
            "unrealized_pnl": float(pos.pnl),
        }
        for pos in positions
    ]


def format_orders_data(orders):
    """純函數：格式化委託資料"""
    return [
        {
            "order_id": order.id,
            "symbol": order.contract.code,
            "action": order.action.value,
            "quantity": order.quantity,
            "price": float(order.price),
            "status": order.status.value,
        }
        for order in orders
    ]
