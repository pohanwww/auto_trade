#!/bin/bash
# 期貨交易程式啟動腳本

# 切換到專案目錄
cd /Users/pohanwww/Documents/Code/auto_trade

# 建立日誌目錄
mkdir -p logs

# 記錄 cron job 被觸發
echo "$(date): start_trading.sh 被執行" >> logs/trading_$(date +%Y%m%d).log

# 檢查是否已有程式在運行
if pgrep -f "uv run main" > /dev/null; then
    echo "$(date): 交易程式已在運行中，跳過啟動" >> logs/trading_$(date +%Y%m%d).log
    exit 0
fi

# 啟動交易程式 (uv run 會自動處理虛擬環境)
echo "$(date): 啟動交易程式" >> logs/trading_$(date +%Y%m%d).log
PYTHONUNBUFFERED=1 /Users/pohanwww/.local/bin/uv run main >> logs/trading_$(date +%Y%m%d).log 2>&1 &
