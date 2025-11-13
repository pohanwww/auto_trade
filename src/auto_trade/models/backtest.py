"""回測相關的數據模型"""

from dataclasses import dataclass, field
from datetime import datetime

from auto_trade.models.account import Action
from auto_trade.models.position_record import ExitReason


def get_point_value(symbol: str) -> int:
    """根據商品代碼獲取每點價值"""
    point_values = {
        "TXF": 200,  # 大台指每點200元
        "MXF": 50,  # 小台指每點50元
        "EXF": 200,  # 電子期貨每點200元
        "FXF": 200,  # 金融期貨每點200元
        "NXF": 200,  # 非金電期貨每點200元
    }
    return point_values.get(symbol, 50)  # 預設為50元


@dataclass
class BacktestTrade:
    """回測交易記錄"""

    trade_id: str
    symbol: str
    action: Action
    entry_time: datetime
    entry_price: int
    exit_time: datetime | None = None
    exit_price: int | None = None
    quantity: int = 1
    exit_reason: ExitReason | None = None
    pnl_points: float | None = None
    pnl_twd: float | None = None
    commission: float = 0.0  # 手續費
    slippage: float = 0.0  # 滑價

    def calculate_pnl(self) -> tuple[float, float]:
        """計算盈虧 (點數, 新台幣)"""
        if self.exit_price is None:
            return 0.0, 0.0

        if self.action == Action.Buy:
            pnl_points = float(self.exit_price - self.entry_price)
        else:  # Sell
            pnl_points = float(self.entry_price - self.exit_price)

        # 根據商品代碼獲取每點價值
        point_value = get_point_value(self.symbol)
        pnl_twd = pnl_points * self.quantity * point_value

        self.pnl_points = pnl_points
        self.pnl_twd = pnl_twd

        return pnl_points, pnl_twd


@dataclass
class BacktestPosition:
    """回測持倉狀態"""

    symbol: str
    action: Action
    entry_time: datetime
    entry_price: int
    quantity: int
    stop_loss_price: int
    take_profit_price: int
    trailing_stop_price: int | None = None
    trailing_stop_active: bool = False
    max_profit_points: float = 0.0
    max_loss_points: float = 0.0

    def update_trailing_stop(self, current_price: int, trailing_stop_points: int):
        """更新移動停損"""
        if self.action == Action.Buy:
            new_trailing_stop = current_price - trailing_stop_points
            if (
                self.trailing_stop_price is None
                or new_trailing_stop > self.trailing_stop_price
            ):
                self.trailing_stop_price = new_trailing_stop
        else:  # Sell
            new_trailing_stop = current_price + trailing_stop_points
            if (
                self.trailing_stop_price is None
                or new_trailing_stop < self.trailing_stop_price
            ):
                self.trailing_stop_price = new_trailing_stop


@dataclass
class BacktestConfig:
    """回測配置"""

    # 基本設定
    symbol: str
    sub_symbol: str
    start_date: datetime
    end_date: datetime
    initial_capital: float = 1000000.0  # 初始資金

    # 交易參數
    order_quantity: int = 1
    stop_loss_points: int = 50
    start_trailing_stop_points: int = 200
    trailing_stop_points: int = 200
    take_profit_points: int = 500
    # 百分比參數（可選，如果設置則會覆蓋固定點數）
    trailing_stop_points_rate: float | None = None
    take_profit_points_rate: float | None = None

    # 策略參數
    timeframe: str = "30m"
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9

    # 回測設定
    max_positions: int = 1  # 最大同時持倉數
    enable_trailing_stop: bool = True
    enable_take_profit: bool = True

    # MACD 快速停損設定
    enable_macd_fast_stop: bool = False  # 是否啟用 MACD 快速停損
    macd_fast_stop_min_loss: int = 30  # MACD 快速停損最小虧損點數

    def calculate_trailing_stop_points(self, entry_price: int) -> int:
        """根據進入價格計算移動停損點數"""
        if self.trailing_stop_points_rate is not None:
            return int(entry_price * self.trailing_stop_points_rate)
        return int(self.trailing_stop_points)

    def calculate_take_profit_points(self, entry_price: int) -> int:
        """根據進入價格計算獲利了結點數"""
        if self.take_profit_points_rate is not None:
            return int(entry_price * self.take_profit_points_rate)
        return int(self.take_profit_points)


@dataclass
class BacktestResult:
    """回測結果"""

    config: BacktestConfig
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)

    # 基本統計
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # 盈虧統計
    total_pnl_twd: float = 0.0
    total_pnl_points: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0

    # 風險指標
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # 時間統計
    backtest_duration_days: int = 0
    avg_trade_duration_hours: float = 0.0

    def calculate_statistics(self):
        """計算統計指標"""
        if not self.trades:
            return

        # 重置計數器（避免重複調用時累加）
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl_twd = 0.0
        self.total_pnl_points = 0.0

        self.total_trades = len(self.trades)

        # 計算盈虧
        profits = []
        losses = []

        for trade in self.trades:
            if trade.exit_price is not None:
                trade.calculate_pnl()
                if trade.pnl_twd > 0:
                    profits.append(trade.pnl_twd)
                    self.winning_trades += 1
                else:
                    losses.append(trade.pnl_twd)
                    self.losing_trades += 1

                self.total_pnl_twd += trade.pnl_twd
                self.total_pnl_points += trade.pnl_points

        self.win_rate = (
            self.winning_trades / self.total_trades if self.total_trades > 0 else 0.0
        )
        self.gross_profit = sum(profits) if profits else 0.0
        self.gross_loss = abs(sum(losses)) if losses else 0.0

        # 計算盈虧比
        self.profit_factor = (
            self.gross_profit / self.gross_loss if self.gross_loss > 0 else float("inf")
        )

        # 計算最大回撤
        self._calculate_max_drawdown()

        # 計算夏普比率
        self._calculate_sharpe_ratio()

        # 計算卡爾瑪比率
        self._calculate_calmar_ratio()

        # 計算平均持倉時間
        self._calculate_avg_trade_duration()

    def _calculate_max_drawdown(self):
        """計算最大回撤"""
        if not self.equity_curve:
            return

        peak = self.equity_curve[0][1]
        max_dd = 0.0
        dd_duration = 0
        current_dd_duration = 0

        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
                current_dd_duration = 0
            else:
                drawdown = (peak - equity) / peak
                if drawdown > max_dd:
                    max_dd = drawdown
                current_dd_duration += 1
                if current_dd_duration > dd_duration:
                    dd_duration = current_dd_duration

        self.max_drawdown = max_dd
        self.max_drawdown_duration = dd_duration

    def _calculate_sharpe_ratio(self):
        """計算夏普比率"""
        if len(self.equity_curve) < 2:
            return

        returns = []
        for i in range(1, len(self.equity_curve)):
            prev_equity = self.equity_curve[i - 1][1]
            curr_equity = self.equity_curve[i][1]
            if prev_equity > 0:
                returns.append((curr_equity - prev_equity) / prev_equity)

        if not returns:
            return

        import statistics

        mean_return = statistics.mean(returns)
        std_return = statistics.stdev(returns)

        if std_return > 0:
            self.sharpe_ratio = mean_return / std_return

    def _calculate_calmar_ratio(self):
        """計算卡爾瑪比率"""
        if self.max_drawdown > 0:
            annual_return = self.total_pnl_twd / self.config.initial_capital
            self.calmar_ratio = annual_return / self.max_drawdown

    def _calculate_avg_trade_duration(self):
        """計算平均持倉時間"""
        durations = []
        for trade in self.trades:
            if trade.exit_time:
                duration = (
                    trade.exit_time - trade.entry_time
                ).total_seconds() / 3600  # 小時
                durations.append(duration)

        if durations:
            import statistics

            self.avg_trade_duration_hours = statistics.mean(durations)


@dataclass
class PerformanceMetrics:
    """績效指標"""

    # 基本指標
    total_return: float
    annual_return: float
    win_rate: float
    profit_factor: float

    # 風險指標
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    sortino_ratio: float

    # 交易指標
    total_trades: int
    avg_trade_return: float
    avg_winning_trade: float
    avg_losing_trade: float
    largest_win: float
    largest_loss: float

    # 時間指標
    avg_trade_duration: float
    max_trade_duration: float
    min_trade_duration: float
