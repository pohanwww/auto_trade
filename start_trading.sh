#!/bin/bash
# 期貨交易程式啟動腳本
#
# 用法：
#   ./start_trading.sh strategy_key_level.yaml              # 啟動日盤 Key Level
#   ./start_trading.sh strategy_key_level_night.yaml        # 啟動夜盤 Key Level
#   ./start_trading.sh strategy_key_level.yaml strategy_key_level_night.yaml  # 同時啟動日盤+夜盤

PROJECT_DIR="/home/pohanwwwgame/auto_trade"
UV_BIN="/home/pohanwwwgame/.local/bin/uv"
cd "$PROJECT_DIR"

mkdir -p logs

if [ $# -eq 0 ]; then
    echo "用法: $0 <config1.yaml> [config2.yaml ...]"
    echo "範例: $0 strategy_key_level.yaml strategy_key_level_night.yaml"
    exit 1
fi

DATE_TAG=$(date +%Y%m%d)

for CONFIG in "$@"; do
    STRATEGY_TAG="${CONFIG%.yaml}"  # strategy_macd.yaml → strategy_macd
    LOG_FILE="logs/${STRATEGY_TAG}_${DATE_TAG}.log"

    # 檢查是否已有該策略在運行
    if pgrep -f "uv run main.*--config $CONFIG" > /dev/null 2>&1; then
        echo "$(date): [$CONFIG] 已在運行中，跳過" >> "$LOG_FILE"
        continue
    fi

    echo "$(date): [$CONFIG] 啟動交易程式" >> "$LOG_FILE"
    PYTHONUNBUFFERED=1 "$UV_BIN" run main --config "$CONFIG" >> "$LOG_FILE" 2>&1 &
    echo "$(date): [$CONFIG] PID=$!" >> "$LOG_FILE"
done
