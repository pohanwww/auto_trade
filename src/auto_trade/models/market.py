"""Market-related data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class KBar:
    """K線資料模型"""

    time: datetime
    open: float
    high: float
    low: float
    close: float

    def to_dict(self) -> dict[str, Any]:
        """轉換為字典格式"""
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KBar":
        """從字典創建KBar"""
        return cls(
            time=data["time"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
        )


@dataclass
class KBarList:
    """K線資料列表模型"""

    kbars: list[KBar] = field(default_factory=list)
    symbol: str = ""
    timeframe: str = "1m"

    def __len__(self) -> int:
        """返回K線數量"""
        return len(self.kbars)

    def __getitem__(self, index: int) -> KBar:
        """支持索引訪問"""
        return self.kbars[index]

    def __iter__(self):
        """支持迭代"""
        return iter(self.kbars)

    def append(self, kbar: KBar) -> None:
        """添加K線"""
        self.kbars.append(kbar)

    def extend(self, kbars: list[KBar]) -> None:
        """擴展K線列表"""
        self.kbars.extend(kbars)

    def get_latest(self, count: int = 1) -> list[KBar]:
        """取得最新的K線"""
        return self.kbars[-count:] if count > 0 else []

    def get_oldest(self, count: int = 1) -> list[KBar]:
        """取得最舊的K線"""
        return self.kbars[:count] if count > 0 else []

    def get_price_range(self) -> tuple[float, float]:
        """取得價格範圍 (最低價, 最高價)"""
        if not self.kbars:
            return 0.0, 0.0

        min_price = min(kbar.low for kbar in self.kbars)
        max_price = max(kbar.high for kbar in self.kbars)
        return min_price, max_price

    def get_time_range(self) -> tuple[datetime, datetime]:
        """取得時間範圍 (最早時間, 最晚時間)"""
        if not self.kbars:
            return datetime.now(), datetime.now()

        min_time = min(kbar.time for kbar in self.kbars)
        max_time = max(kbar.time for kbar in self.kbars)
        return min_time, max_time

    def to_dataframe(self) -> Any:
        """轉換為pandas DataFrame"""
        import pandas as pd

        data = []
        for kbar in self.kbars:
            data.append(
                {
                    "time": kbar.time,
                    "open": kbar.open,
                    "high": kbar.high,
                    "low": kbar.low,
                    "close": kbar.close,
                }
            )

        df = pd.DataFrame(data)
        df.set_index("time", inplace=True)
        return df

    @classmethod
    def from_dataframe(
        cls, df: Any, symbol: str = "", timeframe: str = "1m"
    ) -> "KBarList":
        """從pandas DataFrame創建KBarList"""
        kbars = []
        for _, row in df.iterrows():
            # 優先使用 'time' 欄位，如果沒有則使用 'index' 欄位
            if "time" in row:
                time_value = row["time"]
            elif "index" in row:
                time_value = row["index"]
            else:
                # 如果都沒有，使用 row.name (但這通常是數字索引)
                time_value = row.name

            kbars.append(
                KBar(
                    time=time_value,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
            )

        return cls(kbars=kbars, symbol=symbol, timeframe=timeframe)


@dataclass
class EMAData:
    """EMA指標資料模型"""

    time: datetime
    ema_value: float  # EMA值

    def to_dict(self) -> dict[str, Any]:
        """轉換為字典格式"""
        return {
            "time": self.time,
            "ema_value": self.ema_value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EMAData":
        """從字典創建EMAData"""
        return cls(
            time=data["time"],
            ema_value=data["ema_value"],
        )


@dataclass
class EMAList:
    """EMA指標列表模型"""

    ema_data: list[EMAData] = field(default_factory=list)
    symbol: str = ""
    timeframe: str = "1m"
    period: int = 12  # EMA週期

    def __len__(self) -> int:
        """返回EMA資料數量"""
        return len(self.ema_data)

    def __getitem__(self, index: int) -> EMAData:
        """支持索引訪問"""
        return self.ema_data[index]

    def __iter__(self):
        """支持迭代"""
        return iter(self.ema_data)

    def append(self, ema_data: EMAData) -> None:
        """添加EMA資料"""
        self.ema_data.append(ema_data)

    def extend(self, ema_data_list: list[EMAData]) -> None:
        """擴展EMA資料列表"""
        self.ema_data.extend(ema_data_list)

    def get_latest(self, count: int = 1) -> list[EMAData]:
        """取得最新的EMA資料"""
        return self.ema_data[-count:] if count > 0 else []

    def get_oldest(self, count: int = 1) -> list[EMAData]:
        """取得最舊的EMA資料"""
        return self.ema_data[:count] if count > 0 else []

    def to_dataframe(self) -> Any:
        """轉換為pandas DataFrame"""
        import pandas as pd

        data = []
        for ema in self.ema_data:
            data.append(
                {
                    "time": ema.time,
                    "ema_value": ema.ema_value,
                }
            )

        df = pd.DataFrame(data)
        df.set_index("time", inplace=True)
        return df

    @classmethod
    def from_dataframe(
        cls, df: Any, symbol: str = "", timeframe: str = "1m", period: int = 12
    ) -> "EMAList":
        """從pandas DataFrame創建EMAList"""
        ema_data = []
        for _, row in df.iterrows():
            ema_data.append(
                EMAData(
                    time=row.name if hasattr(row, "name") else row["time"],
                    ema_value=float(row["ema_value"]),
                )
            )

        return cls(ema_data=ema_data, symbol=symbol, timeframe=timeframe, period=period)


@dataclass
class MACDData:
    """MACD指標資料模型"""

    time: datetime
    macd_line: float  # MACD線
    signal_line: float  # 信號線
    histogram: float  # 柱狀圖

    def to_dict(self) -> dict[str, Any]:
        """轉換為字典格式"""
        return {
            "time": self.time,
            "macd_line": self.macd_line,
            "signal_line": self.signal_line,
            "histogram": self.histogram,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MACDData":
        """從字典創建MACDData"""
        return cls(
            time=data["time"],
            macd_line=data["macd_line"],
            signal_line=data["signal_line"],
            histogram=data["histogram"],
        )


@dataclass
class MACDList:
    """MACD指標列表模型"""

    macd_data: list[MACDData] = field(default_factory=list)
    symbol: str = ""
    timeframe: str = "1m"

    def __len__(self) -> int:
        """返回MACD資料數量"""
        return len(self.macd_data)

    def __getitem__(self, index: int) -> MACDData:
        """支持索引訪問"""
        return self.macd_data[index]

    def __iter__(self):
        """支持迭代"""
        return iter(self.macd_data)

    def append(self, macd_data: MACDData) -> None:
        """添加MACD資料"""
        self.macd_data.append(macd_data)

    def extend(self, macd_data_list: list[MACDData]) -> None:
        """擴展MACD資料列表"""
        self.macd_data.extend(macd_data_list)

    def get_latest(self, count: int = 1) -> list[MACDData]:
        """取得最新的MACD資料"""
        return self.macd_data[-count:] if count > 0 else []

    def get_oldest(self, count: int = 1) -> list[MACDData]:
        """取得最舊的MACD資料"""
        return self.macd_data[:count] if count > 0 else []

    def to_dataframe(self) -> Any:
        """轉換為pandas DataFrame"""
        import pandas as pd

        data = []
        for macd in self.macd_data:
            data.append(
                {
                    "time": macd.time,
                    "macd_line": macd.macd_line,
                    "signal_line": macd.signal_line,
                    "histogram": macd.histogram,
                }
            )

        df = pd.DataFrame(data)
        df.set_index("time", inplace=True)
        return df

    @classmethod
    def from_dataframe(
        cls, df: Any, symbol: str = "", timeframe: str = "1m"
    ) -> "MACDList":
        """從pandas DataFrame創建MACDList"""
        macd_data = []
        for _, row in df.iterrows():
            macd_data.append(
                MACDData(
                    time=row.name if hasattr(row, "name") else row["time"],
                    macd_line=float(row["macd_line"]),
                    signal_line=float(row["signal_line"]),
                    histogram=float(row["histogram"]),
                )
            )

        return cls(macd_data=macd_data, symbol=symbol, timeframe=timeframe)


@dataclass
class Quote:
    """即時報價資料模型"""

    symbol: str
    price: int
    volume: int
    bid_price: int | None = None
    ask_price: int | None = None
    timestamp: datetime | None = None
