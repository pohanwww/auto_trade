# 期貨交易程式 Cron Job 設定說明

## 市場時間
- **日盤**: 8:45~13:45 (週一到週五)
- **夜盤**: 15:00~隔天5:00 (週一到週六)
- **週六**: 早上5:00結束

## Cron Job 設定

### 1. 設定 Cron Job
```bash
# 編輯 crontab
crontab -e

# 或者直接載入設定檔
crontab crontab.txt

# 刪除所有的Cron Job
crontab -r
```

### 2. Cron Job 時間表
```
# 日盤啟動 (週一到週五 8:30)
30 8 * * 1-5 /Users/pohanwww/Documents/Code/auto_trade/start_trading.sh

# 夜盤啟動 (週一到週五 14:45)
45 14 * * 1-5 /Users/pohanwww/Documents/Code/auto_trade/start_trading.sh

# 週六夜盤啟動 (週六 14:45)
45 14 * * 6 /Users/pohanwww/Documents/Code/auto_trade/start_trading.sh

# 週六早上5:00結束
0 5 * * 6 /Users/pohanwww/Documents/Code/auto_trade/stop_trading.sh

# 週日早上5:00清理
0 5 * * 0 /Users/pohanwww/Documents/Code/auto_trade/stop_trading.sh
```

### 3. 手動操作
```bash
# 手動啟動
./start_trading.sh

# 手動停止
./stop_trading.sh

# 檢查是否運行
pgrep -f "python src/auto_trade/main.py"

# 查看日誌
tail -f logs/trading_$(date +%Y%m%d).log
```

### 4. 日誌管理
- 日誌位置: `logs/trading_YYYYMMDD.log`
- 自動建立日誌目錄
- 包含啟動、停止和錯誤訊息

### 5. 優點
- ✅ 市場開盤前15分鐘自動啟動
- ✅ 市場收盤後自動停止
- ✅ 避免長時間 sleep 的計時器問題
- ✅ 系統重啟後自動恢復
- ✅ 完整的日誌記錄
- ✅ 防止重複啟動

### 6. 注意事項
- 確保腳本有執行權限: `chmod +x *.sh`
- 檢查路徑是否正確
- 定期清理舊日誌檔案
- 監控 cron job 是否正常運行
