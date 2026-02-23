#!/bin/bash
# 期貨交易程式停止腳本
#
# 用法：
#   ./stop_trading.sh                               # 停止所有策略
#   ./stop_trading.sh strategy_macd.yaml             # 只停止 MACD
#   ./stop_trading.sh strategy_orb.yaml              # 只停止 ORB

PROJECT_DIR="/Users/pohanwww/Documents/Code/auto_trade"
cd "$PROJECT_DIR"

mkdir -p logs
DATE_TAG=$(date +%Y%m%d)

stop_strategy() {
    local PATTERN="$1"
    local LABEL="$2"
    local LOG_FILE="logs/${LABEL}_${DATE_TAG}.log"

    if ! pgrep -f "$PATTERN" > /dev/null 2>&1; then
        echo "$(date): [$LABEL] 未在運行" >> "$LOG_FILE"
        return
    fi

    # 優雅停止
    pkill -TERM -f "$PATTERN"
    sleep 5

    # 確認是否已停止
    if pgrep -f "$PATTERN" > /dev/null 2>&1; then
        echo "$(date): [$LABEL] 未優雅退出，強制停止" >> "$LOG_FILE"
        pkill -KILL -f "$PATTERN"
    else
        echo "$(date): [$LABEL] 已優雅停止" >> "$LOG_FILE"
    fi
}

if [ $# -eq 0 ]; then
    # 無參數：停止所有策略
    stop_strategy "uv run main.*--config strategy_macd" "strategy_macd"
    stop_strategy "uv run main.*--config strategy_orb" "strategy_orb"
    # 也停止未指定 config 的舊版程式
    stop_strategy "uv run main" "trading"
else
    for CONFIG in "$@"; do
        STRATEGY_TAG="${CONFIG%.yaml}"
        stop_strategy "uv run main.*--config $CONFIG" "$STRATEGY_TAG"
    done
fi
