#!/bin/bash
# 期貨交易程式停止腳本

# 切換到專案目錄
cd /Users/pohanwww/Documents/Code/auto_trade

# 優雅地停止交易程式
pkill -TERM -f "uv run main"

# 等待5秒
sleep 5

# 如果還在運行，強制停止
if pgrep -f "uv run main" > /dev/null; then
    echo "$(date): 程式未優雅退出，強制停止" >> logs/trading_$(date +%Y%m%d).log
    pkill -KILL -f "uv run main"
else
    echo "$(date): 交易程式已優雅停止" >> logs/trading_$(date +%Y%m%d).log
fi
