"""時間相關工具函數"""

import time
from datetime import datetime, timedelta


def calculate_and_wait_to_next_execution(
    current_time: datetime, interval_minutes: int, verbose: bool = False
) -> None:
    """
    計算並等待到下一個執行時間

    Args:
        current_time: 當前時間
        interval_minutes: 間隔分鐘數 (必須能被60整除，如1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)

    Examples:
        如果當前是 5:08，間隔是 5 分鐘：
        - 當前分鐘 = 8
        - 8 // 5 = 1，所以下一個是 (1+1) * 5 = 10
        - 下次執行時間 = 5:10

        如果當前是 5:22，間隔是 15 分鐘：
        - 當前分鐘 = 22
        - 22 // 15 = 1，所以下一個是 (1+1) * 15 = 30
        - 下次執行時間 = 5:30

        如果當前是 5:45，間隔是 15 分鐘：
        - 當前分鐘 = 45
        - 45 // 15 = 3，所以下一個是 (3+1) * 15 = 60 (即下一小時的0分)
        - 下次執行時間 = 6:00
    """
    # 驗證間隔能被60整除
    if 60 % interval_minutes != 0:
        raise ValueError(f"間隔分鐘數 {interval_minutes} 必須能被60整除")

    current_minute = current_time.minute

    # 計算到下一個間隔的時間
    next_interval_minute = ((current_minute // interval_minutes) + 1) * interval_minutes

    if next_interval_minute >= 60:
        # 如果超過60分鐘，移到下一小時
        next_time = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
    else:
        next_time = current_time.replace(
            minute=next_interval_minute, second=0, microsecond=0
        )

    wait_seconds = (next_time - current_time).total_seconds()

    if wait_seconds > 0:
        if verbose:
            print(f"下次執行時間: {next_time.strftime('%H:%M:%S')}")
            print(f"等待 {wait_seconds:.0f} 秒...")
        time.sleep(wait_seconds)


def wait_seconds(seconds: int, verbose: bool = False) -> None:
    """
    簡單等待指定秒數 (用於持倉時的高頻檢測)

    Args:
        seconds: 等待秒數 (最短3秒，最長60秒)

    Examples:
        wait_seconds(5)   # 等待5秒
        wait_seconds(1)   # 自動調整為3秒
        wait_seconds(100) # 自動調整為60秒
    """
    # 限制等待時間範圍
    seconds = max(3, min(seconds, 60))

    if verbose:
        print(f"等待 {seconds} 秒...")
    time.sleep(seconds)
