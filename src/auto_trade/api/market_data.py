from datetime import datetime, timedelta

import pandas as pd


def get_futures_realtime_quote(api_client, symbol, sub_symbol):
    """純函數：取得期貨即時報價"""
    contract = api_client.Contracts.Futures[symbol][sub_symbol]
    snapshot = api_client.snapshots([contract])
    return format_quote_data(snapshot[0])


def get_futures_historical_kbars(api_client, symbol, sub_symbol, days=30):
    """純函數：取得期貨歷史K線資料"""
    contract = api_client.Contracts.Futures[symbol][sub_symbol]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    kbars = api_client.kbars(
        contract=contract,
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
    )

    return format_kbar_data(kbars)


def get_futures_historical_ticks(api_client, symbol, date):
    """純函數：取得期貨歷史tick資料"""
    contract = api_client.Contracts.Futures[symbol]
    ticks = api_client.ticks(contract=contract, date=date)
    return format_tick_data(ticks)


def format_quote_data(snapshot):
    """純函數：格式化即時報價資料"""
    return {
        "symbol": snapshot.code,
        "price": float(snapshot.close),
        "volume": snapshot.total_volume,
        "bid_price": float(snapshot.buy_price) if snapshot.buy_price else None,
        "ask_price": float(snapshot.sell_price) if snapshot.sell_price else None,
        "timestamp": datetime.fromtimestamp(snapshot.ts / 1000000000),
    }


def format_kbar_data(kbars):
    """純函數：格式化K線資料"""
    df = pd.DataFrame({**kbars})
    df["ts"] = pd.to_datetime(df["ts"])
    return df[["ts", "Open", "High", "Low", "Close", "Volume"]]


def format_tick_data(ticks):
    """純函數：格式化tick資料"""
    df = pd.DataFrame({**ticks})
    df["ts"] = pd.to_datetime(df["ts"])
    return df[["ts", "close", "volume", "bid_price", "ask_price"]]
