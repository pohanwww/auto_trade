"""點數計算工具函式"""

from __future__ import annotations


def calculate_points(
    base_points: int,
    rate: float | None,
    reference_price: int | float | None,
) -> int:
    """根據固定點數或百分比計算實際點數

    Args:
        base_points: 預設的固定點數
        rate: 以價格為基準的百分比（例如 0.01 表示 1%）
        reference_price: 參考價格（進場價 / 當前價）

    Returns:
        int: 計算後的點數
    """
    if rate is not None and reference_price is not None:
        return int(reference_price * rate)
    return int(base_points)
