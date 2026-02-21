"""回測腳本 - 使用 BacktestEngine 測試策略組合.

用法：
    # 回測 YAML 中所有策略配置
    uv run backtest

    # 指定日期範圍
    uv run backtest --start 2025-01-01 --end 2025-06-30

    # 指定時間尺度
    uv run backtest --timeframe 1h

    # 只測試特定策略配置
    uv run backtest --strategies default,complex

    # 設定初始資金和滑價
    uv run backtest --capital 500000 --slippage 1
"""

import argparse
from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.engines.backtest_engine import BacktestEngine, BacktestEngineConfig
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManagerConfig
from auto_trade.strategies import create_strategy


def build_trading_units_from_config(
    indicator_service: IndicatorService,
    strategy_names: list[str] | None = None,
) -> list[TradingUnit]:
    """從 YAML 配置建立 TradingUnit 列表

    Args:
        indicator_service: 指標服務
        strategy_names: 要測試的策略名稱列表，None 表示全部

    Returns:
        list[TradingUnit]: 交易單元列表
    """
    from pathlib import Path

    import yaml

    # 讀取完整 YAML
    config_dir = Path(__file__).parent.parent.parent / "config"
    strategies_file = config_dir / "strategy.yaml"

    with open(strategies_file, encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    # 取得所有策略名稱
    all_strategies = [k for k in config_data if k not in ["active_strategy", "symbol"]]

    # 篩選要測試的策略
    if strategy_names:
        strategies_to_test = [s for s in strategy_names if s in all_strategies]
        missing = [s for s in strategy_names if s not in all_strategies]
        if missing:
            print(f"⚠️  找不到策略: {missing}，可用策略: {all_strategies}")
    else:
        strategies_to_test = all_strategies

    units = []
    for name in strategies_to_test:
        strategy_data = config_data[name]
        trading = strategy_data["trading"]
        position = strategy_data.get("position", {})

        # 根據 strategy_type 建立策略實例（預設為 macd_golden_cross）
        strategy_type = strategy_data.get("strategy_type", "macd_golden_cross")

        # 提取策略層級的參數（直接從 trading 區塊取，策略 __init__ 用 **kwargs 忽略不認識的）
        # 所有可能的策略參數 key（各策略各取所需）
        _STRATEGY_PARAM_KEYS = [
            # 共用參數
            "volume_percentile_threshold",
            "volume_percentile_lookback",
            "volume_lookback_period",
            # ORB 基本
            "or_bars",
            "or_start_time",
            "entry_end_time",
            "session_end_time",
            "tp_multiplier",
            "ts_start_multiplier",
            "ts_distance_ratio",
            # ORB: 強突破閾值
            "strong_rvol",
            "strong_candle",
            # ORB: 回踩確認
            "retest_tolerance_pct",
            "pullback_timeout_bars",
            "min_bounce_strength",
            # ORB: 可選過濾
            "long_only",
            "use_vwap_filter",
            "adx_threshold",
            "adx_period",
            # ORB: 前日 OHLC 過濾
            "use_prev_pressure_filter",
            "min_pressure_space_pct",
            "use_prev_direction_filter",
            # ORB: 階梯式壓力線移停
            "use_key_level_trailing",
            "key_level_buffer",
            "key_level_min_profit_pct",
            "key_level_min_distance_pct",
            # ORB: 壓力線停利
            "use_key_level_tp",
            "key_level_tp_min_pct",
            "use_key_level_tp_max",
            # ORB: 動能衰竭停利
            "use_momentum_exit",
            "momentum_min_profit_pct",
            "momentum_lookback",
            "momentum_weak_threshold",
            "momentum_min_weak_bars",
            # ORB: 固定停利 + 每日上限
            "fixed_tp_points",
            "max_entries_per_day",
            # ORB: EMA 方向過濾
            "use_ema_direction",
            "ema_direction_period",
            # ORB: RVOL
            "rvol_lookback",
            # Scalp 策略
            "session_start_time",
            "entry_mode",
            "breakout_lookback",
            "breakout_min_strength",
            "reversal_consecutive",
            "reversal_min_strength",
            "short_only",
            "cooldown_bars",
            # Bollinger 策略
            "bb_period",
            "bb_std",
            "tp_target",
            "tp_buffer",
            "hybrid_ts_trail_points",
            "sl_buffer",
            "trend_filter_bars",
        ]
        strategy_kwargs = {k: trading[k] for k in _STRATEGY_PARAM_KEYS if k in trading}

        strategy = create_strategy(strategy_type, indicator_service, **strategy_kwargs)

        # 用 from_dict() 一行建立 PositionManagerConfig —— 新增參數時不用改這裡
        pm_config = PositionManagerConfig.from_dict(trading, position)

        # 名稱中包含策略類型以便識別
        type_tags = {
            "macd_golden_cross": "做多",
            "macd_bidirectional": "雙向",
            "orb": "ORB",
            "scalp": "Scalp",
            "bollinger": "BB",
        }
        type_tag = type_tags.get(strategy_type, strategy_type)
        fs_tag = "" if pm_config.enable_macd_fast_stop else " noFS"
        ts_tag = " tightenTS" if pm_config.has_tightened_trailing_stop else ""

        # filter tags
        filter_tags = []
        if strategy_kwargs.get("long_only"):
            filter_tags.append("LongOnly")
        if strategy_kwargs.get("use_vwap_filter"):
            filter_tags.append("VWAP")
        if strategy_kwargs.get("adx_threshold") is not None:
            filter_tags.append(f"ADX>={strategy_kwargs['adx_threshold']}")
        # ORB entry mode tags
        strong_rvol = strategy_kwargs.get("strong_rvol")
        if strong_rvol is not None:
            if strong_rvol >= 50:
                filter_tags.append("retest-only")
            elif strong_rvol <= 1.0:
                filter_tags.append("momentum")
            else:
                filter_tags.append("dual-mode")
        # ORB prev OHLC filter tags
        if strategy_kwargs.get("use_prev_pressure_filter"):
            pct = strategy_kwargs.get("min_pressure_space_pct", 1.0)
            filter_tags.append(f"Pressure>={pct}x")
        if strategy_kwargs.get("use_prev_direction_filter"):
            filter_tags.append("DirBias")
        if strategy_kwargs.get("use_key_level_trailing"):
            kl_parts = [f"buf={strategy_kwargs.get('key_level_buffer', 10)}"]
            if strategy_kwargs.get("key_level_min_profit_pct", 0) > 0:
                kl_parts.append(
                    f"minP={strategy_kwargs['key_level_min_profit_pct']}x"
                )
            if strategy_kwargs.get("key_level_min_distance_pct", 0) > 0:
                kl_parts.append(
                    f"minD={strategy_kwargs['key_level_min_distance_pct']}x"
                )
            filter_tags.append(f"KeyLvlTS({','.join(kl_parts)})")
        if strategy_kwargs.get("use_key_level_tp"):
            min_pct = strategy_kwargs.get("key_level_tp_min_pct", 0.5)
            filter_tags.append(f"KeyLvlTP(min={min_pct}x)")
        if strategy_kwargs.get("use_key_level_tp_max"):
            filter_tags.append("KeyLvlTPMax")
        if strategy_kwargs.get("use_momentum_exit"):
            filter_tags.append("MomExit")
        if strategy_kwargs.get("use_ema_direction"):
            ema_p = strategy_kwargs.get("ema_direction_period", 200)
            filter_tags.append(f"EMA{ema_p}Dir")
        if strategy_kwargs.get("fixed_tp_points", 0) > 0:
            filter_tags.append(f"FixTP>={strategy_kwargs['fixed_tp_points']}")
        if strategy_kwargs.get("max_entries_per_day", 1) > 1:
            filter_tags.append(f"max{strategy_kwargs['max_entries_per_day']}x")
        # Scalp tags
        if strategy_type == "scalp":
            mode = strategy_kwargs.get("entry_mode", "both")
            filter_tags.append(f"mode={mode}")
            if strategy_kwargs.get("short_only"):
                filter_tags.append("ShortOnly")
        # Bollinger tags
        if strategy_type == "bollinger":
            bb_std = strategy_kwargs.get("bb_std", 3.0)
            tp_tgt = strategy_kwargs.get("tp_target", "middle")
            filter_tags.append(f"std={bb_std}")
            filter_tags.append(f"TP→{tp_tgt}")
        filter_str = " " + "+".join(filter_tags) if filter_tags else ""

        unit = TradingUnit(
            name=(
                f"{name} ({type_tag}{fs_tag}{ts_tag}"
                f"{filter_str}"
                f" qty={pm_config.total_quantity}"
                f" TP={pm_config.tp_leg_quantity}/TS={pm_config.ts_leg_quantity})"
            ),
            strategy=strategy,
            pm_config=pm_config,
        )
        units.append(unit)

    return units


def parse_args() -> argparse.Namespace:
    """解析命令列參數"""
    parser = argparse.ArgumentParser(
        description="回測交易策略",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  uv run backtest                                    # 測試所有策略，預設 30 天
  uv run backtest --start 2025-01-01 --end 2025-06-30
  uv run backtest --strategies default,complex
  uv run backtest --timeframe 1h --days 60
  uv run backtest --capital 500000 --slippage 1
        """,
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="回測開始日期 (YYYY-MM-DD)，預設為 --days 天前",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="回測結束日期 (YYYY-MM-DD)，預設為今天",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="回測天數（當 --start 未指定時使用），預設 30",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default=None,
        help="K 線時間尺度 (如 1m, 5m, 30m, 1h)，預設使用各策略自己的設定",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default=None,
        help="要測試的策略名稱，逗號分隔 (如 default,complex)，預設全部",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1_000_000,
        help="初始資金 (TWD)，預設 1,000,000",
    )
    parser.add_argument(
        "--slippage",
        type=int,
        default=0,
        help="滑價點數，預設 0",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="儲存回測報告到 data/backtest/",
    )
    parser.add_argument(
        "--chart",
        action="store_true",
        help="產生權益曲線比較圖（策略 vs Buy & Hold）",
    )

    return parser.parse_args()


def main():
    """回測主程式"""
    args = parse_args()

    print("=" * 60)
    print("🧪 回測系統啟動")
    print("=" * 60)

    # 載入配置
    config = Config()

    # 解析日期範圍
    if args.end:
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
    else:
        end_date = datetime.now()

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
    else:
        start_date = end_date - timedelta(days=args.days)

    # 決定 timeframe（命令列覆蓋 > 各策略自己的 timeframe）
    # 如果命令列未指定，BacktestEngine 會自動根據各 unit 的 timeframe 處理
    timeframe = args.timeframe or "30m"

    print(
        f"📅 期間: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}"
    )
    print(f"📈 商品: {config.symbol} ({config.sub_symbol})")
    print(f"⏱  時間尺度: {timeframe} (可被各策略覆蓋)")
    print(f"💰 初始資金: {args.capital:,.0f}")
    if args.slippage:
        print(f"📉 滑價: {args.slippage} 點")

    # 建立 API 客戶端（simulation mode 取歷史資料）
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=True,
    )

    # 建立服務
    market_service = MarketService(api_client)
    indicator_service = IndicatorService()

    # 解析要測試的策略
    strategy_names = None
    if args.strategies:
        strategy_names = [s.strip() for s in args.strategies.split(",")]

    # 從 YAML 建立 TradingUnit
    units = build_trading_units_from_config(indicator_service, strategy_names)

    if not units:
        print("❌ 沒有可測試的策略")
        return

    print(f"\n🎯 測試 {len(units)} 個策略配置:")
    for i, unit in enumerate(units, 1):
        print(f"   {i}. {unit.name}")

    # 建立回測引擎
    bt_config = BacktestEngineConfig(
        symbol=config.symbol,
        sub_symbol=config.sub_symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        initial_capital=args.capital,
        slippage_points=args.slippage,
    )

    engine = BacktestEngine(bt_config, market_service, indicator_service)

    # 執行回測
    results = engine.run(units)

    if not results:
        print("❌ 回測無結果")
        return

    # 產生報告
    report = engine.generate_report(results)
    print(f"\n{report}")

    # 儲存報告
    if args.save:
        engine.save_report(results)

    # 產生圖表
    if args.chart:
        engine.generate_chart(results)


if __name__ == "__main__":
    main()
