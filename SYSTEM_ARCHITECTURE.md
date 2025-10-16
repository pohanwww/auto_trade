# 自動交易系統架構文檔

## 🏗️ 系統整體架構

```
┌─────────────────────────────────────────────────────────────────┐
│                    自動交易系統架構                                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Config        │    │   Main.py       │    │   Line Bot      │
│   (配置管理)      │    │   (主程式)       │    │   (通知服務)     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TradingService                               │
│                   (交易服務核心)                                 │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    服務層 (Services)                             │
├─────────────────┬─────────────────┬─────────────────┬─────────┤
│  MarketService  │  OrderService   │  AccountService  │RecordSvc│
│  (市場數據)      │  (訂單管理)      │  (帳戶管理)       │(記錄)   │
└─────────────────┴─────────────────┴─────────────────┴─────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Shioaji API                                  │
│                 (永豐證券 API)                                   │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    數據存儲                                      │
├─────────────────┬─────────────────┬─────────────────┬─────────┤
│  本地 JSON      │  Google Sheets  │  回測結果       │  日誌   │
│  (持倉記錄)      │  (交易記錄)      │  (回測報告)      │(系統)   │
└─────────────────┴─────────────────┴─────────────────┴─────────┘
```

## 🔄 系統流程圖

### 1. 系統啟動流程

```
開始
  │
  ▼
載入配置 (Config)
  │
  ▼
初始化 Shioaji API 客戶端
  │
  ▼
建立服務層
├─ MarketService (市場數據)
├─ OrderService (訂單管理)  
├─ AccountService (帳戶管理)
├─ RecordService (記錄管理)
└─ LineBotService (通知服務)
  │
  ▼
初始化 TradingService
  │
  ▼
設定交易參數
  │
  ▼
發送啟動通知到 Line Bot
  │
  ▼
開始執行策略循環
```

### 2. 交易策略執行流程

```
策略循環開始
  │
  ▼
檢查是否有持倉？
├─ 有持倉 ──→ 檢查平倉條件
│              ├─ 停損觸發 ──→ 平倉
│              ├─ 獲利了結 ──→ 平倉  
│              └─ 移動停損 ──→ 更新停損價格
│
└─ 無持倉 ──→ 檢查開倉信號
              ├─ MACD 金叉 ──→ 開多倉
              ├─ MACD 死叉 ──→ 開空倉
              └─ 無信號 ──→ 等待
  │
  ▼
等待下一個檢查週期
  │
  ▼
回到策略循環開始
```

### 3. 開倉流程

```
收到開倉信號
  │
  ▼
計算停損價格 (前30根K線最低點-80點)
  │
  ▼
下市價單
  │
  ▼
等待成交
  │
  ▼
成交成功？
├─ 是 ──→ 保存持倉記錄到本地 JSON
│         │
│         ▼
│         更新 Google Sheets
│         │
│         ▼
│         發送開倉通知到 Line Bot
│         │
│         ▼
│         設定持倉狀態
│
└─ 否 ──→ 等待60秒後重試
```

### 4. 平倉流程

```
觸發平倉條件
  │
  ▼
下平倉單
  │
  ▼
等待成交
  │
  ▼
成交成功？
├─ 是 ──→ 移除本地持倉記錄
│         │
│         ▼
│         更新 Google Sheets (平倉記錄)
│         │
│         ▼
│         獲取 Google Sheets 最新數據
│         │
│         ▼
│         發送平倉通知到 Line Bot (含統計)
│         │
│         ▼
│         重置持倉狀態
│
└─ 否 ──→ 記錄錯誤並重試
```

## 📊 數據流向圖

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Shioaji    │───▶│  Market     │───▶│  Strategy   │
│  API        │    │  Service    │    │  Service    │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Order      │    │  Account    │    │  Trading    │
│  Service    │    │  Service    │    │  Service    │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Record     │    │  Line Bot   │    │  Backtest   │
│  Service    │    │  Service    │    │  Service    │
└─────────────┘    └─────────────┘    └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ 本地 JSON   │    │  Line Bot   │    │ 回測報告    │
│ Google Sheets│    │ 通知訊息    │    │ 統計分析    │
└─────────────┘    └─────────────┘    └─────────────┘
```

## 🔧 核心組件說明

### 1. 配置層
- **Config**: 統一配置管理，讀取 YAML 配置和環境變數
- **strategy.yaml**: 交易策略參數配置

### 2. 服務層
- **TradingService**: 交易核心邏輯，策略執行
- **MarketService**: 市場數據獲取，K線、報價
- **OrderService**: 訂單管理，下單、查詢
- **AccountService**: 帳戶管理，資金、持倉查詢
- **RecordService**: 記錄管理，本地+Google Sheets
- **LineBotService**: 通知服務，Line Bot 訊息

### 3. 數據層
- **本地 JSON**: 持倉記錄存儲
- **Google Sheets**: 交易記錄和統計
- **回測結果**: 策略回測報告

### 4. 外部服務
- **Shioaji API**: 永豐證券交易 API
- **Line Bot**: 即時通知服務

## 📁 文件結構

```
auto_trade/
├── src/auto_trade/
│   ├── main.py                 # 主程式入口
│   ├── core/
│   │   ├── config.py           # 配置管理
│   │   └── client.py           # API 客戶端
│   ├── services/
│   │   ├── trading_service.py   # 交易服務核心
│   │   ├── market_service.py    # 市場數據服務
│   │   ├── order_service.py     # 訂單管理服務
│   │   ├── account_service.py   # 帳戶管理服務
│   │   ├── record_service.py    # 記錄管理服務
│   │   ├── strategy_service.py  # 策略服務
│   │   └── line_bot_service.py  # Line Bot 服務
│   ├── web/
│   │   ├── __init__.py         # Web 模組初始化
│   │   └── line_bot_server.py  # Line Bot Webhook 服務器
│   ├── models/
│   │   ├── position_record.py   # 持倉記錄模型
│   │   ├── order.py            # 訂單模型
│   │   ├── market.py           # 市場數據模型
│   │   └── backtest.py         # 回測模型
│   └── backtest/
│       └── backtest.py         # 回測腳本
├── config/
│   └── strategy.yaml           # 策略配置
├── data/
│   ├── position_records.json   # 本地持倉記錄
│   └── backtest/              # 回測結果
├── logs/                       # 系統日誌
├── credentials/                # 憑證文件
│   └── google_credentials.json # Google Sheets 憑證
└── line_bot_server.py         # Line Bot Webhook 服務器
```

## 🎯 關鍵特性

1. **模組化設計**: 各服務獨立，易於維護
2. **統一配置**: 集中管理所有參數
3. **多重記錄**: 本地+雲端雙重備份
4. **即時通知**: Line Bot 即時推送
5. **回測支援**: 完整的回測框架
6. **錯誤處理**: 完善的異常處理機制

## 📱 Line Bot 通知格式

### 開倉通知
```
📈 開倉通知
━━━━━━━━━━━━━━━━━━━━
時間: 2025-01-16 10:30:00
商品: MXF (MXF202511)
開倉價格: 21,850.0
數量: 1
方向: Buy
停損價格: 21,770.0
━━━━━━━━━━━━━━━━━━━━
```

### 平倉通知
```
📉 平倉通知
━━━━━━━━━━━━━━━━━━━━
時間: 2025-01-16 11:00:00
商品: MXF (MXF202511)
平倉價格: 21,900.0
平倉原因: TAKE_PROFIT
━━━━━━━━━━━━━━━━━━━━
📊 Google Sheets 最新記錄:
開倉時間: 2025-01-16 10:30:00
商品代碼: MXF
子商品代碼: MXF202511
開倉價格: 21850
平倉價格: 21900
盈虧點數: 50
盈虧金額: 2500
總盈虧: 2500
勝率: 100%
...
```

## 🚀 運行方式

### 1. 主交易系統
```bash
uv run -m src.auto_trade.main
```

### 2. Line Bot 服務器
```bash
uv run -m src.auto_trade.web.line_bot_server
```

### 3. 回測系統
```bash
uv run -m src.auto_trade.backtest.backtest
```

## 🔐 環境變數

```bash
# Shioaji API
SHIOAJI_USER_ID=your_user_id
SHIOAJI_PASSWORD=your_password

# Google Sheets
GOOGLE_CREDENTIALS_PATH=credentials/google_credentials.json
GOOGLE_SPREADSHEET_NAME=your_spreadsheet_name

# Line Bot
LINE_CHANNEL_ID=your_channel_id
LINE_CHANNEL_SECRET=your_channel_secret
LINE_MESSAGING_API_TOKEN=your_messaging_api_token
LINE_USER_ID=your_user_id
```

這個系統架構清晰，流程完整，能夠穩定運行自動交易策略！
