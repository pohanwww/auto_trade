"""Trading Engine - 實盤交易的薄協調者.

TradingEngine 不包含業務邏輯，只負責：
1. 協調 Strategy、PositionManager、Executor 之間的資料流
2. 管理主循環（獲取行情 → 策略評估 → PM 更新 → 執行下單）
3. I/O 相關操作（日誌、通知、持倉記錄）

所有交易邏輯分散在：
- Strategy → 信號產生
- PositionManager → 倉位決策
- Executor → 下單執行
"""

import json
import time as _time
from datetime import datetime

from auto_trade.executors.base_executor import BaseExecutor
from auto_trade.models.account import Action
from auto_trade.models.position import OrderAction
from auto_trade.models.position_record import ExitReason, PositionRecord
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.account_service import AccountService
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.line_bot_service import LineBotService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManager
from auto_trade.services.record_service import RecordService


class TradingEngine:
    """實盤交易引擎

    薄的協調層，將以下組件串聯：
    - TradingUnit (Strategy + PM Config)
    - MarketService (行情資料)
    - Executor (下單執行)
    - RecordService (持倉記錄)
    - LineBotService (通知，可選)
    """

    def __init__(
        self,
        trading_unit: TradingUnit,
        market_service: MarketService,
        executor: BaseExecutor,
        indicator_service: IndicatorService,
        account_service: AccountService | None = None,
        record_service: RecordService | None = None,
        line_bot_service: LineBotService | None = None,
    ):
        self.trading_unit = trading_unit
        self.market_service = market_service
        self.executor = executor
        self.indicator_service = indicator_service
        self.account_service = account_service
        self.record_service = record_service or RecordService()
        self.line_bot_service = line_bot_service

        # 建立 PositionManager
        self.position_manager = PositionManager(
            config=trading_unit.pm_config,
            indicator_service=indicator_service,
        )

        # 交易商品信息（由 configure 設定）
        self.symbol: str | None = None
        self.sub_symbol: str | None = None

        # 檢測頻率（進場／加碼等）；持倉出場僅在 tick 喚醒時檢查
        self.signal_check_interval: int = 5  # 分鐘

        # 加碼信號去重
        self._addon_checked_this_interval: bool = False

        self._INSTANT_SUPPRESS_SECONDS = 30
        # Persist tick-volume rolling window across instant-monitor rounds.
        self._instant_tick_volume_window = None
        self._instant_tick_volume_window_sec: float | None = None

        # 配置檔名（用於 dashboard 辨識程序）
        self.config_file: str | None = None

    def configure(
        self,
        symbol: str,
        sub_symbol: str,
        signal_check_interval: int = 5,
        config_file: str | None = None,
    ) -> None:
        """設定交易參數

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            signal_check_interval: 信號檢測間隔（分鐘）
            config_file: YAML 配置檔名（如 strategy_ma.yaml）
        """
        self.config_file = config_file
        self.symbol = symbol
        self.sub_symbol = sub_symbol
        self.signal_check_interval = signal_check_interval

        print("🔧 TradingEngine 配置:")
        print(f"  交易單元: {self.trading_unit.name}")
        print(f"  策略: {self.trading_unit.strategy.name}")
        print(f"  倉位配置: {self.trading_unit.pm_config}")
        print(f"  商品: {symbol} / {sub_symbol}")
        print("  持倉出場檢查: 每筆 tick")

    def run(self) -> None:
        """執行交易主循環"""
        if not self.symbol or not self.sub_symbol:
            print("❌ 請先呼叫 configure() 設定交易商品")
            return

        print(f"🚀 啟動 TradingEngine: {self.trading_unit.name}")

        if hasattr(self.trading_unit.strategy, "is_live"):
            self.trading_unit.strategy.is_live = True

        # 檢查是否有未平倉的持倉記錄 → 恢復到 PositionManager
        self._try_restore_position()

        # 訂閱商品
        self.market_service.subscribe_symbol(self.symbol, self.sub_symbol, init_days=14)

        # kbar 快取就緒後，用歷史 K 棒校正 highest/lowest price 及移停狀態
        self._reconcile_position_from_history()

        # kbar 資料就緒 → 立即計算 KL，確保重啟後狀態完整
        self._initialize_strategy_levels()

        # 發送啟動通知
        self._send_startup_notification()

        # 主循環
        print_flag = False
        _KBAR_REFRESH_INTERVAL = 3  # seconds – throttle K-bar resample
        _last_kbar_fetch: float = 0
        _cached_kbar_list = None
        while True:
            try:
                current_time = datetime.now()

                # 取得即時報價
                quote = self.market_service.get_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    raise Exception("無法取得即時報價")

                current_price = quote.price

                if self.position_manager.has_position:
                    # === 有倉位：tick-driven 高頻監控 ===

                    # 0. 時間強制平倉（日內策略用，如 ORB 13:30 收盤）
                    actions = self.position_manager.check_time_exit(
                        current_time, current_price
                    )

                    if not actions:
                        _now_mono = _time.monotonic()
                        if _cached_kbar_list is None or (_now_mono - _last_kbar_fetch) > _KBAR_REFRESH_INTERVAL:
                            _cached_kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                                self.symbol,
                                self.sub_symbol,
                                self.trading_unit.pm_config.timeframe,
                                days=5,
                            )
                            _last_kbar_fetch = _now_mono

                        # 讓 PM 處理價格更新
                        actions = self.position_manager.on_price_update(
                            current_price, _cached_kbar_list
                        )

                    # 執行 PM 產生的指令
                    for action in actions:
                        self._execute_action(action)

                    # 加碼信號檢測（每 signal_check_interval 分鐘）
                    if (
                        self.position_manager.config.enable_addon
                        and self.position_manager.has_position
                        and current_time.minute % self.signal_check_interval == 0
                        and not self._addon_checked_this_interval
                    ):
                        self._addon_checked_this_interval = True
                        kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                            self.symbol,
                            self.sub_symbol,
                            self.trading_unit.pm_config.timeframe,
                            days=5,
                        )
                        signal = self.trading_unit.strategy.evaluate(
                            kbar_list, current_price, self.sub_symbol
                        )
                        addon_actions = self.position_manager.on_signal(
                            signal, kbar_list, self.symbol, self.sub_symbol
                        )
                        for action in addon_actions:
                            self._execute_action(action)
                    elif current_time.minute % self.signal_check_interval != 0:
                        self._addon_checked_this_interval = False

                    # Sync intraday KL updates to PM position
                    self._try_sync_kl_to_position(current_price)

                    # 同步倉位狀態到 position.json
                    self._sync_position_record(current_price)

                    # 日誌（每 5 分鐘一次）
                    if current_time.minute % 5 == 0 and not print_flag:
                        print_flag = True
                        print(
                            f"[{current_time.strftime('%H:%M:%S')}] "
                            f"價格: {current_price:.1f}"
                        )
                    elif current_time.minute % 5 != 0:
                        print_flag = False

                    self.market_service.wait_for_tick(timeout=None)

                else:
                    # === 無倉位：低頻檢測信號 ===
                    _t0 = _time.monotonic()

                    print(
                        f"\n[{current_time.strftime('%H:%M:%S')}] "
                        f"價格: {current_price:.1f}"
                    )

                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=5,
                    )
                    _t1 = _time.monotonic()

                    # 策略評估
                    signal = self.trading_unit.strategy.evaluate(
                        kbar_list, current_price, self.sub_symbol
                    )
                    _t2 = _time.monotonic()

                    # PM 處理信號
                    actions = self.position_manager.on_signal(
                        signal, kbar_list, self.symbol, self.sub_symbol
                    )
                    _t3 = _time.monotonic()

                    if actions:
                        print(
                            f"⏱️ kbar={(_t1-_t0)*1000:.0f}ms "
                            f"eval={(_t2-_t1)*1000:.0f}ms "
                            f"signal={(_t3-_t2)*1000:.0f}ms "
                            f"total={(_t3-_t0)*1000:.0f}ms"
                        )

                    # 執行開倉指令
                    for action in actions:
                        fill_result = self._execute_action(action)
                        if fill_result and action.order_type == "Open" and self.position_manager.position:
                                pos = self.position_manager.position
                                signal_price = pos.entry_price
                                pos.entry_price = fill_result
                                pos.highest_price = fill_result
                                pos.lowest_price = fill_result
                                for leg in pos.legs:
                                    if leg.entry_price == signal_price:
                                        leg.entry_price = fill_result

                    if not actions:
                        print("無交易訊號")

                    self._sync_position_record(current_price)

                    if not self.position_manager.has_position:
                        self._wait_with_instant_check()

            except KeyboardInterrupt:
                print("\n程式被使用者中斷")
                break
            except Exception as e:
                print(f"執行錯誤: {str(e)}")
                print("結束程式")
                break

    # ── Instant trigger monitoring ─────────────────────────

    _INSTANT_PROXIMITY = 30   # points: switch to 1s polling
    _INSTANT_POLL_FAST = 1    # seconds
    _INSTANT_POLL_NORMAL = 3  # seconds

    def _wait_with_instant_check(self) -> None:
        """Wait for next bar while monitoring instant trigger prices.

        When a trigger fires, evaluate+execute inline. If rejected, suppress
        that trigger for 30s and keep monitoring. No round-trip to main loop.
        """
        from datetime import timedelta

        now = datetime.now()
        interval = self.signal_check_interval
        current_minute = now.minute
        next_min = ((current_minute // interval) + 1) * interval
        if next_min >= 60:
            next_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_time = now.replace(minute=next_min, second=0, microsecond=0)

        use_vol_confirm = bool(
            getattr(self.trading_unit.strategy, "instant_volume_confirm_1m", False)
        )
        if use_vol_confirm:
            print(f"下次執行時間: {next_time.strftime('%H:%M:%S')}")
            print(
                "⚡ Instant monitoring: tick rolling 帶量（實盤）；"
                "回測仍用 1m RVOL"
            )
            self._wait_instant_tick_volume_gated(next_time)
            return

        long_target, short_target = self.trading_unit.strategy.get_instant_targets()

        if long_target is None and short_target is None:
            print(f"下次執行時間: {next_time.strftime('%H:%M:%S')}")
            while datetime.now() < next_time:
                remaining = (next_time - datetime.now()).total_seconds()
                if remaining <= 0:
                    break
                self.market_service.wait_for_tick(timeout=min(remaining, 5.0))
            return

        print(f"下次執行時間: {next_time.strftime('%H:%M:%S')}")
        print(
            f"⚡ Instant monitoring: "
            f"long={f'{long_target:.0f}' if long_target else '-'}, "
            f"short={f'{short_target:.0f}' if short_target else '-'}"
        )

        suppress_until = 0.0
        last_refresh_minute = -1

        while datetime.now() < next_time:
            try:
                # Refresh targets at each minute boundary
                cur_min = datetime.now().minute
                if cur_min != last_refresh_minute:
                    last_refresh_minute = cur_min
                    long_target, short_target = self.trading_unit.strategy.get_instant_targets()

                quote = self.market_service.get_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                price = quote.price
                now_ts = _time.monotonic()

                # Check suppress
                if now_ts < suppress_until:
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                triggered = (
                    (long_target is not None and price >= long_target)
                    or (short_target is not None and price <= short_target)
                )

                if triggered:
                    print(f"⚡ Instant trigger hit! price={price:.0f}")

                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=5,
                    )
                    signal = self.trading_unit.strategy.evaluate(
                        kbar_list, price, self.sub_symbol, bar_close=False,
                    )
                    actions = self.position_manager.on_signal(
                        signal, kbar_list, self.symbol, self.sub_symbol,
                    )

                    if actions:
                        for action in actions:
                            fill_result = self._execute_action(action)
                            if fill_result and action.order_type == "Open" and self.position_manager.position:
                                pos = self.position_manager.position
                                signal_price = pos.entry_price
                                pos.entry_price = fill_result
                                pos.highest_price = fill_result
                                pos.lowest_price = fill_result
                                for leg in pos.legs:
                                    if leg.entry_price == signal_price:
                                        leg.entry_price = fill_result
                        self._sync_position_record(price)
                        return  # position opened, back to main loop

                    # Rejected — suppress for 30s, keep monitoring
                    suppress_until = now_ts + self._INSTANT_SUPPRESS_SECONDS
                    print(f"⚡ Trigger rejected, suppress {self._INSTANT_SUPPRESS_SECONDS}s")
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                # Wait for next tick (fallback timeout keeps adaptive ceiling)
                distances = []
                if long_target is not None:
                    distances.append(abs(price - long_target))
                if short_target is not None:
                    distances.append(abs(price - short_target))
                min_dist = min(distances) if distances else 9999
                poll = (
                    self._INSTANT_POLL_FAST
                    if min_dist <= self._INSTANT_PROXIMITY
                    else self._INSTANT_POLL_NORMAL
                )
                self.market_service.wait_for_tick(timeout=poll)

            except Exception:
                self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)

    def _wait_instant_tick_volume_gated(self, next_time: datetime) -> None:
        """實盤：即時價觸發價 AND 近窗 tick 累積量達標（對照前三根已收 5m 均量/窗寬）。

        回測仍由 BacktestEngine + evaluate_instant_volume_breakout_1m（1m RVOL）處理。
        """
        from auto_trade.services.tick_volume_monitor import (
            RollingTickVolumeWindow,
            avg_volume_per_seconds_from_last_n_closed_5m,
            is_high_volume_vs_baseline,
        )

        s = self.trading_unit.strategy
        window_sec = float(getattr(s, "instant_volume_tick_window_sec", 10.0))
        n_closed = int(getattr(s, "instant_volume_baseline_closed_5m_bars", 3))
        mult = float(getattr(s, "instant_volume_rvol_min", 1.3))
        min_roll = int(getattr(s, "instant_volume_min_rolling", 10))
        if (
            self._instant_tick_volume_window is None
            or self._instant_tick_volume_window_sec != window_sec
        ):
            self._instant_tick_volume_window = RollingTickVolumeWindow(
                window_sec=window_sec
            )
            self._instant_tick_volume_window_sec = window_sec
        tw = self._instant_tick_volume_window

        long_target: float | None = None
        short_target: float | None = None
        kbar_list = None
        baseline: float | None = None
        last_kbar_mono: float = 0.0
        _KBAR_REFRESH_INTERVAL = 5.0
        _VOL_LOG_INTERVAL = 5.0
        _TRIGGER_REJECT_LOG_INTERVAL = 2.0

        suppress_until = 0.0
        last_vol_log_mono = 0.0
        last_reject_log_mono = 0.0

        while datetime.now() < next_time:
            try:
                quote = self.market_service.get_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                ts = quote.timestamp if quote.timestamp else datetime.now()
                tw.on_tick(ts, int(quote.volume))

                now_mono = _time.monotonic()
                if kbar_list is None or (
                    now_mono - last_kbar_mono > _KBAR_REFRESH_INTERVAL
                ):
                    last_kbar_mono = now_mono
                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=5,
                    )
                    if hasattr(s, "_compute_active_targets"):
                        # Instant rolling loop refreshes frequently; skip target spam.
                        s._compute_active_targets(kbar_list, log_targets=False)
                    long_target, short_target = s.get_instant_targets()
                    baseline = avg_volume_per_seconds_from_last_n_closed_5m(
                        kbar_list,
                        n_closed_bars=n_closed,
                        baseline_window_sec=max(1, int(round(window_sec))),
                        exclude_forming=True,
                    )
                    closed_bars = kbar_list.kbars[:-1]
                    baseline_bars = closed_bars[-n_closed:] if len(closed_bars) >= n_closed else []
                    baseline_snapshot = ", ".join(
                        f"{b.time.strftime('%H:%M')}:{int(b.volume)}"
                        for b in baseline_bars
                    )
                    print(
                        "📦 Instant baseline source: "
                        f"{baseline_snapshot or 'insufficient closed 5m bars'} "
                        f"-> per_{int(round(window_sec))}s≈"
                        f"{(baseline if baseline is not None else 0.0):.1f}"
                    )

                now_ts = _time.monotonic()
                if now_ts < suppress_until:
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                price = quote.price
                triggered = (
                    (long_target is not None and price >= long_target)
                    or (short_target is not None and price <= short_target)
                )
                rolling = tw.rolling_sum(ts)
                vol_ok = (
                    baseline is not None
                    and baseline > 0
                    and is_high_volume_vs_baseline(
                        float(rolling), baseline, multiplier=mult
                    )
                    and rolling > min_roll
                )
                vol_ratio = (
                    (float(rolling) / float(baseline))
                    if baseline is not None and baseline > 0
                    else 0.0
                )

                if now_mono - last_vol_log_mono >= _VOL_LOG_INTERVAL:
                    last_vol_log_mono = now_mono
                    print(
                        "📊 Instant tick vol: "
                        f"roll={rolling} / baseline≈{(baseline or 0):.1f} "
                        f"(ratio={vol_ratio:.2f}x, need>={mult:.2f}x, roll>{min_roll}) "
                        f"price={price:.0f} "
                        f"targets(L/S)="
                        f"{(f'{long_target:.0f}' if long_target is not None else '-')}/"
                        f"{(f'{short_target:.0f}' if short_target is not None else '-')}"
                    )

                if triggered and vol_ok:
                    print(
                        f"⚡ Instant trigger (tick vol): price={price:.0f} "
                        f"roll={rolling} baseline_per_{int(round(window_sec))}s≈{baseline:.1f} "
                        f"(>{mult:.2f}× and >{min_roll})"
                    )

                    if kbar_list is None or len(kbar_list.kbars) < 2:
                        kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                            self.symbol,
                            self.sub_symbol,
                            self.trading_unit.pm_config.timeframe,
                            days=5,
                        )

                    signal = s.evaluate(
                        kbar_list,
                        price,
                        self.sub_symbol,
                        bar_close=False,
                    )
                    actions = self.position_manager.on_signal(
                        signal,
                        kbar_list,
                        self.symbol,
                        self.sub_symbol,
                    )

                    if actions:
                        for action in actions:
                            fill_result = self._execute_action(action)
                            if (
                                fill_result
                                and action.order_type == "Open"
                                and self.position_manager.position
                            ):
                                pos = self.position_manager.position
                                signal_price = pos.entry_price
                                pos.entry_price = fill_result
                                pos.highest_price = fill_result
                                pos.lowest_price = fill_result
                                for leg in pos.legs:
                                    if leg.entry_price == signal_price:
                                        leg.entry_price = fill_result
                        self._sync_position_record(int(price))
                        return

                    suppress_until = now_ts + self._INSTANT_SUPPRESS_SECONDS
                    sig_type = getattr(signal, "signal_type", None)
                    sig_type_str = getattr(sig_type, "value", str(sig_type))
                    sig_reason = getattr(signal, "reason", "") or "no reason"
                    print(
                        f"⚡ Trigger rejected (tick vol ok), suppress "
                        f"{self._INSTANT_SUPPRESS_SECONDS}s "
                        f"| signal={sig_type_str} | reason={sig_reason}"
                    )
                    self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)
                    continue

                if triggered and not vol_ok and (
                    now_mono - last_reject_log_mono >= _TRIGGER_REJECT_LOG_INTERVAL
                ):
                    last_reject_log_mono = now_mono
                    print(
                        "⚠️ Instant price hit but volume not enough: "
                        f"price={price:.0f} roll={rolling} "
                        f"baseline≈{(baseline or 0):.1f} "
                        f"ratio={vol_ratio:.2f}x (<{mult:.2f}x or roll<={min_roll})"
                    )

                distances = []
                if long_target is not None:
                    distances.append(abs(price - long_target))
                if short_target is not None:
                    distances.append(abs(price - short_target))
                min_dist = min(distances) if distances else 9999
                poll = (
                    self._INSTANT_POLL_FAST
                    if min_dist <= self._INSTANT_PROXIMITY
                    else self._INSTANT_POLL_NORMAL
                )
                self.market_service.wait_for_tick(timeout=poll)

            except Exception:
                self.market_service.wait_for_tick(timeout=self._INSTANT_POLL_NORMAL)

    def _execute_action(self, action: OrderAction) -> int | None:
        """執行下單指令並處理成交

        Returns:
            成交價格，失敗則返回 None
        """
        fill_result = self.executor.execute(action)

        if fill_result.success and fill_result.fill_price is not None:
            print(
                f"{'📈' if action.order_type == 'Open' else '📉'} "
                f"{action.action.value} x{action.quantity} @ {fill_result.fill_price} "
                f"({action.reason})"
            )

            # 開倉 → 保存持倉記錄
            if action.order_type == "Open":
                self._record_open(action, fill_result.fill_price)

            # 平倉 → 先取 entry_price / direction（on_fill 後 position 可能變 None）
            close_entry_price = 0
            close_direction = None
            close_remaining_qty = 0
            if action.order_type == "Close" and self.position_manager.position:
                pos = self.position_manager.position
                close_direction = pos.direction
                close_leg_ids = []
                if action.leg_id:
                    close_leg_ids = [action.leg_id]
                elif "leg_ids" in action.metadata:
                    close_leg_ids = action.metadata["leg_ids"]
                if close_leg_ids:
                    leg_eps = [
                        leg.entry_price for leg in pos.legs
                        if leg.leg_id in close_leg_ids and leg.entry_price
                    ]
                    if leg_eps:
                        close_entry_price = sum(leg_eps) // len(leg_eps)
                if not close_entry_price:
                    close_entry_price = pos.entry_price
                close_remaining_qty = pos.open_quantity - action.quantity

            # 平倉 → 通知 PM 並更新 Google Sheets per-leg
            if action.order_type == "Close" and action.leg_id:
                exit_reason_str = action.metadata.get("exit_reason", "SL")
                exit_reason = ExitReason(exit_reason_str)
                self.position_manager.on_fill(
                    leg_id=action.leg_id,
                    fill_price=fill_result.fill_price,
                    fill_time=fill_result.fill_time or datetime.now(),
                    exit_reason=exit_reason,
                )
                self._record_leg_close(
                    action, fill_result.fill_price, exit_reason,
                    leg_ids=[action.leg_id],
                )
            elif action.order_type == "Close" and "leg_ids" in action.metadata:
                exit_reason_str = action.metadata.get("exit_reason", "FS")
                exit_reason = ExitReason(exit_reason_str)
                for leg_id in action.metadata["leg_ids"]:
                    self.position_manager.on_fill(
                        leg_id=leg_id,
                        fill_price=fill_result.fill_price,
                        fill_time=fill_result.fill_time or datetime.now(),
                        exit_reason=exit_reason,
                    )
                self._record_leg_close(
                    action, fill_result.fill_price, exit_reason,
                    leg_ids=action.metadata["leg_ids"],
                )

            # 發送通知
            if self.line_bot_service:
                try:
                    strategy = self.record_service.strategy_name if self.record_service else ""
                    pm = self.position_manager
                    if action.order_type == "Open":
                        sl_price = 0
                        total_qty = 0
                        if pm.position and pm.position.open_legs:
                            sl_price = pm.position.open_legs[0].exit_rule.stop_loss_price or 0
                            total_qty = pm.position.open_quantity
                        self.line_bot_service.send_open_position_message(
                            symbol=action.symbol,
                            sub_symbol=action.sub_symbol,
                            price=fill_result.fill_price,
                            quantity=action.quantity,
                            action=action.action.value,
                            stop_loss_price=sl_price,
                            strategy_name=strategy,
                            reason=action.reason,
                            total_quantity=total_qty,
                        )
                    elif action.order_type == "Close":
                        equity = None
                        if self.account_service:
                            try:
                                margin = self.account_service.get_margin()
                                equity = margin.equity
                            except Exception:
                                pass
                        self.line_bot_service.send_close_position_message(
                            symbol=action.symbol,
                            sub_symbol=action.sub_symbol,
                            price=fill_result.fill_price,
                            quantity=action.quantity,
                            exit_reason=action.reason,
                            entry_price=close_entry_price,
                            direction=close_direction,
                            strategy_name=strategy,
                            remaining_quantity=max(close_remaining_qty, 0),
                            equity=equity,
                        )
                except Exception as e:
                    print(f"發送通知失敗: {e}")

            return fill_result.fill_price
        else:
            print(f"❌ 下單失敗: {fill_result.message}")
            return None

    def _try_sync_kl_to_position(self, current_price: float) -> None:
        """Sync intraday KL updates from strategy to PM position metadata.

        Adjusts next_key_level_idx so it still points to the same KL
        as before the list was updated (new intermediate levels are
        inserted before/after but the pointer stays on the same target).
        """
        strategy = self.trading_unit.strategy
        if not getattr(strategy, "_kl_updated", False):
            return
        strategy._kl_updated = False

        pm = self.position_manager
        if not pm.has_position or not pm.position:
            return

        pos = pm.position
        old_levels = pos.metadata.get("key_levels")
        if old_levels is None:
            return

        is_long = pos.direction.value == "Buy"
        entry_price = pos.entry_price
        all_kl_prices = sorted(set(kl.price for kl in strategy._key_levels))

        if is_long:
            new_levels = [p for p in all_kl_prices if p > entry_price]
        else:
            new_levels = sorted(
                [p for p in all_kl_prices if p < entry_price],
                reverse=True,
            )

        if new_levels == old_levels:
            return

        old_idx = pos.metadata.get("next_key_level_idx", 0)

        # Remap idx so it still points to the same KL
        if old_idx < len(old_levels):
            target_level = old_levels[old_idx]
            try:
                new_idx = new_levels.index(target_level)
            except ValueError:
                new_idx = min(old_idx, len(new_levels))
        else:
            new_idx = len(new_levels)

        pos.metadata["key_levels"] = new_levels
        pos.metadata["next_key_level_idx"] = new_idx

        print(
            f"[KL] Synced key_levels to position: "
            f"{len(old_levels)} → {len(new_levels)} levels, "
            f"idx {old_idx} → {new_idx} "
            f"(target={'done' if new_idx >= len(new_levels) else new_levels[new_idx]}, "
            f"price={current_price:.0f})"
        )

    def _sync_position_record(self, current_price: float) -> None:
        """同步倉位狀態 + 即時價格到 position.json（供 dashboard 讀取）"""
        if not self.record_service or not self.sub_symbol:
            return

        pm = self.position_manager
        pos = pm.position

        try:
            path = self.record_service.record_file
            raw = path.read_text(encoding="utf-8")
            try:
                records = json.loads(raw)
            except json.JSONDecodeError as e:
                # 避免用「只有 _live」覆寫掉一份其實有內容但 JSON 壞掉的檔案
                print(
                    f"⚠️ position.json 解析失敗，略過寫入以免清空持倉: "
                    f"{path.resolve()} err={e}"
                )
                return
            if not isinstance(records, dict):
                print(
                    f"⚠️ position.json 根節點必須是 JSON 物件，略過寫入: {path.resolve()}"
                )
                return

            # Always write live metadata
            records["_live"] = {
                "current_price": int(current_price),
                "timestamp": datetime.now().isoformat(),
                "strategy": self.record_service.strategy_name,
                "symbol": self.symbol,
                "sub_symbol": self.sub_symbol,
                "config_file": self.config_file,
            }

            pending = self.trading_unit.strategy.get_pending_state()
            if pending:
                records["_live"]["strategy_state"] = pending

            if pos and self.sub_symbol in records:
                is_long = pos.direction.value == "Buy"
                ts_prices = [
                    leg.exit_rule.trailing_stop_price
                    for leg in pos.open_legs
                    if leg.exit_rule.trailing_stop_active
                    and leg.exit_rule.trailing_stop_price
                ]
                rec = records[self.sub_symbol]
                rec["highest_price"] = pos.highest_price
                rec["trailing_stop_active"] = any(
                    leg.exit_rule.trailing_stop_active for leg in pos.open_legs
                )
                rec["stop_loss_price"] = (
                    pos.open_legs[0].exit_rule.stop_loss_price or 0
                    if pos.open_legs
                    else 0
                )
                rec["start_trailing_stop_price"] = (
                    pos.open_legs[0].exit_rule.start_trailing_stop_price
                    if pos.open_legs
                    else None
                )
                rec["trailing_stop_price"] = (
                    (max(ts_prices) if is_long else min(ts_prices))
                    if ts_prices
                    else None
                )
                rec["quantity"] = sum(leg.quantity for leg in pos.open_legs)

                # ORB metadata: key_levels, TP, etc.
                if pos.metadata:
                    meta_keys = [
                        "key_levels", "next_key_level_idx", "key_level_buffer",
                        "or_high", "or_low", "or_mid", "or_range",
                        "override_take_profit_points",
                        "override_start_trailing_stop_points",
                        "override_trailing_stop_points",
                    ]
                    pos_meta = {}
                    for k in meta_keys:
                        if k in pos.metadata:
                            pos_meta[k] = pos.metadata[k]
                    if pos_meta:
                        rec["position_metadata"] = pos_meta

            self.record_service.record_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False)
            )
        except Exception:
            pass

    def _try_restore_position(self) -> None:
        """啟動時檢查 position.json，如有未平倉記錄則恢復到 PositionManager"""
        if not self.sub_symbol:
            return

        record = self.record_service.get_position(self.sub_symbol)

        # Always try to restore strategy state (trades_today, cooldown)
        # even if there's no open position
        self._try_restore_strategy_state()

        if record is None:
            print("📋 無未平倉記錄，正常啟動")
            return

        print(f"📋 發現未平倉記錄: {record.sub_symbol} @ {record.entry_price}")
        self.position_manager.restore_position(record)

    def _try_restore_strategy_state(self) -> None:
        """從 position.json 的 _live.strategy_state 恢復策略運行狀態"""
        try:
            records = self.record_service._load_records(self.record_service.record_file)
            live_data = records.get("_live", {})
            strategy_state = live_data.get("strategy_state")
            if strategy_state and hasattr(self.trading_unit.strategy, "restore_state"):
                self.trading_unit.strategy.restore_state(strategy_state)
                print(f"📋 策略狀態已恢復: trades_today={strategy_state.get('trades_today', 0)}")
        except Exception as e:
            print(f"⚠️ 恢復策略狀態失敗: {e}")

    def _reconcile_position_from_history(self) -> None:
        """用歷史 K 棒校正 highest/lowest price，補回停機期間的極值。

        在 subscribe_symbol 之後呼叫（kbar 快取已初始化）。
        掃描入場時間之後的所有 K 棒，找出真正的最高/最低價，
        並重新觸發移停啟動邏輯，確保重啟後移停狀態正確。
        """
        pos = self.position_manager.position
        if pos is None:
            return

        kbar_list = self.market_service.get_futures_kbars_with_timeframe(
            self.symbol,
            self.sub_symbol,
            self.trading_unit.pm_config.timeframe,
            days=5,
        )
        if not kbar_list or len(kbar_list) == 0:
            return

        entry_time = pos.entry_time
        is_long = pos.direction == Action.Buy
        old_highest = pos.highest_price
        old_lowest = pos.lowest_price

        hist_high = old_highest
        hist_low = old_lowest

        for kb in kbar_list.kbars:
            if kb.time < entry_time:
                continue
            if kb.high > hist_high:
                hist_high = int(kb.high)
            if kb.low < hist_low:
                hist_low = int(kb.low)

        if hist_high == old_highest and hist_low == old_lowest:
            print("📋 歷史校正: highest/lowest 與記錄一致，無需更新")
            return

        pos.highest_price = hist_high
        pos.lowest_price = hist_low

        extreme = hist_high if is_long else hist_low
        old_extreme = old_highest if is_long else old_lowest
        print(
            f"📋 歷史校正: {'最高' if is_long else '最低'}價 "
            f"{old_extreme} → {extreme}"
        )

        # 用校正後的極值重新觸發移停邏輯
        self.position_manager._update_trailing_stops(extreme)

    def _initialize_strategy_levels(self) -> None:
        """Kbar 就緒後立即跑一次策略 evaluate，確保 KL 狀態就位。

        如果 restore_state 已恢復 key_levels → evaluate 跳過重算，
        只初始化 ATR 等 runtime state。
        如果尚未計算（首次啟動）→ evaluate 會觸發 KL 計算。
        丟棄信號結果。若有持倉且產生新 KL，同步到 PM。
        """
        try:
            strategy = self.trading_unit.strategy
            already_restored = getattr(strategy, "_levels_calculated", False)

            kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                self.symbol,
                self.sub_symbol,
                self.trading_unit.pm_config.timeframe,
                days=5,
            )
            if not kbar_list or len(kbar_list) == 0:
                print("⚠️ 啟動時無 kbar 資料，跳過 KL 初始化")
                return

            quote = self.market_service.get_realtime_quote(
                self.symbol, self.sub_symbol,
            )
            current_price = quote.price if quote else 0

            # evaluate 內部：
            #   - _levels_calculated=True (已恢復) → 跳過 _calculate_key_levels
            #   - ATR 等 runtime state 正常初始化
            self.trading_unit.strategy.evaluate(
                kbar_list, current_price, self.sub_symbol,
            )

            # 首次啟動且產出新 KL → 同步到 PM
            if not already_restored and current_price > 0:
                self._try_sync_kl_to_position(current_price)

            n_levels = len(getattr(strategy, "_key_levels", []))
            src = "restored" if already_restored else "calculated"
            print(f"📋 KL 初始化完成 ({src}): {n_levels} levels")
        except Exception as e:
            print(f"⚠️ KL 初始化失敗（不影響主循環）: {e}")

    def _record_open(self, action: OrderAction, fill_price: int) -> None:
        """開倉時保存持倉記錄到 position.json + Google Sheets（每個 leg 一行）"""
        try:
            pm = self.position_manager
            position = pm.position
            if not position:
                return

            sl_price = None
            if position.open_legs:
                sl_price = position.open_legs[0].exit_rule.stop_loss_price

            # 讀取既有的 position record（加碼時保留原始資料）
            existing_record = self.record_service.get_position(action.sub_symbol)
            existing_row_map = {}
            existing_legs_info = {}
            if existing_record:
                if existing_record.sheets_row_map:
                    existing_row_map = dict(existing_record.sheets_row_map)
                if existing_record.legs_info:
                    existing_legs_info = dict(existing_record.legs_info)

            # 新倉位：用實際成交價更新 entry_price 及所有出場參數
            if not existing_record:
                pm.update_entry_on_fill(fill_price)

            record = PositionRecord(
                symbol=action.symbol,
                sub_symbol=action.sub_symbol,
                direction=action.action,
                entry_time=existing_record.entry_time if existing_record else datetime.now(),
                timeframe=pm.config.timeframe,
                quantity=position.open_quantity,
                entry_price=position.entry_price,
                stop_loss_price=sl_price,
                start_trailing_stop_price=(
                    position.open_legs[0].exit_rule.start_trailing_stop_price
                    if position.open_legs
                    else None
                ),
                take_profit_price=(
                    position.open_legs[0].exit_rule.take_profit_price
                    if position.open_legs
                    else None
                ),
                trailing_stop_active=(
                    position.open_legs[0].exit_rule.trailing_stop_active
                    if position.open_legs
                    else False
                ),
                highest_price=position.highest_price or fill_price,
                lowest_price=position.lowest_price or fill_price,
                sheets_row_map=existing_row_map,
                legs_info=existing_legs_info,
            )

            # 找出尚未記錄的新 legs
            new_legs = []
            for leg in position.open_legs:
                is_new = leg.leg_id not in existing_row_map
                leg_ep = leg.entry_price or fill_price
                if is_new:
                    new_legs.append({
                        "leg_id": leg.leg_id,
                        "quantity": leg.quantity,
                        "entry_price": leg_ep,
                    })
                if leg.leg_id not in existing_legs_info:
                    if record.legs_info is None:
                        record.legs_info = {}
                    record.legs_info[leg.leg_id] = {
                        "entry_price": leg_ep,
                        "quantity": leg.quantity,
                        "leg_type": leg.leg_type.value,
                    }

            # 為新 legs 建立 Google Sheets 記錄
            if new_legs:
                new_row_map = self.record_service.log_legs_open(record, new_legs)
                if record.sheets_row_map is None:
                    record.sheets_row_map = {}
                record.sheets_row_map.update(new_row_map)

            self.record_service.save_position(record)
        except Exception as e:
            print(f"⚠️ 保存開倉記錄失敗: {e}")

    def _record_leg_close(
        self,
        action: OrderAction,
        fill_price: int,
        exit_reason: ExitReason,
        leg_ids: list[str],
    ) -> None:
        """平倉時更新 Google Sheets 對應 leg 的記錄，並在全部平倉後清除 position.json"""
        try:
            pm = self.position_manager
            strategy_params = {
                "stop_loss_points": pm.config.stop_loss_points,
                "start_trailing_stop_points": pm.config.start_trailing_stop_points,
                "trailing_stop_points": pm.config.trailing_stop_points,
                "take_profit_points": pm.config.take_profit_points,
            }

            # 取得 sheets_row_map
            existing_record = self.record_service.get_position(action.sub_symbol)
            row_map = {}
            if existing_record and existing_record.sheets_row_map:
                row_map = existing_record.sheets_row_map

            # 更新每個被平倉 leg 的 Google Sheets 記錄
            for leg_id in leg_ids:
                row_number = row_map.get(leg_id)
                if row_number:
                    self.record_service.log_leg_close(
                        leg_id=leg_id,
                        row_number=row_number,
                        exit_price=float(fill_price),
                        exit_reason=exit_reason,
                        strategy_params=strategy_params,
                    )

            # 全部平倉 → 清除 position.json + 通知策略
            if pm.position is None:
                self.record_service.remove_position(sub_symbol=action.sub_symbol)
                self.trading_unit.strategy.on_position_closed(exit_price=fill_price)
            else:
                # 部分平倉 → 更新 position.json（移除已平倉 leg 的條目）
                legs_info = existing_record.legs_info or {}
                for leg_id in leg_ids:
                    row_map.pop(leg_id, None)
                    legs_info.pop(leg_id, None)
                existing_record.sheets_row_map = row_map
                existing_record.legs_info = legs_info
                existing_record.quantity = pm.position.open_quantity
                self.record_service.save_position(existing_record)

        except Exception as e:
            print(f"⚠️ 記錄平倉失敗: {e}")

    def _send_startup_notification(self) -> None:
        """發送系統啟動通知"""
        if not self.line_bot_service or not self.account_service:
            return

        try:
            quote = self.market_service.get_realtime_quote(self.symbol, self.sub_symbol)
            current_price = quote.price if quote else "N/A"
            margin = self.account_service.get_margin()

            strategy = self.record_service.strategy_name if self.record_service else "unknown"
            pos_qty = (
                self.position_manager.position.open_quantity
                if self.position_manager.has_position
                else 0
            )
            self.line_bot_service.send_status_message(
                total_equity=margin.equity_amount,
                contract=self.sub_symbol,
                price=current_price,
                position=pos_qty,
                status=f"策略 [{strategy}] 已啟動",
            )
        except Exception as e:
            print(f"發送啟動通知失敗: {e}")
