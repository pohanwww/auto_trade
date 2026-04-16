# Auto Trade - 自動交易系統

台灣期貨自動交易系統，支援多策略配置、風險管理、Google Sheets 交易紀錄等功能。

---

## 🚀 快速開始

### 1. 環境設定

```bash
# 安裝 uv（Python 包管理器）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 複製環境變數範例檔
cp .env.example .env

# 編輯 .env，填入您的 API 金鑰
vim .env
```

### 2. 策略配置

```bash
# 複製策略配置範例檔
cp config/strategy.example.yaml config/strategy.yaml

# 編輯策略配置（選擇策略、商品、參數等）
vim config/strategy.yaml
```

### 3. 執行程式

```bash
# 執行交易程式
uv run main
```

---

## 📁 專案結構

```
auto_trade/
├── config/
│   ├── strategy.example.yaml    # 策略配置範例（提交到 Git）
│   └── strategy.yaml            # 實際配置（.gitignore）
├── credentials/
│   ├── Sinopac.pfx             # 永豐憑證（.gitignore）
│   └── google_credentials.json # Google API 憑證（.gitignore）
├── data/
│   └── position_records.json   # 本地持倉記錄（.gitignore）
├── logs/                        # 交易日誌（.gitignore）
├── src/auto_trade/
│   ├── core/                   # 核心配置
│   ├── models/                 # 資料模型
│   ├── services/               # 業務邏輯
│   ├── utils/                  # 工具函式
│   └── main.py                 # 程式入口
├── .env                        # 環境變數（.gitignore）
├── .env.example                # 環境變數範例
├── start_trading.sh            # 啟動腳本
├── stop_trading.sh             # 停止腳本
└── crontab.txt                 # Cron 定時任務設定
```

---

## 🎯 使用方式

### 切換策略

直接編輯 `config/strategy.yaml` 的第一行：

```yaml
# === 當前啟用的策略 ===
active_strategy: "default"  # 改為 "higher" 或 "middle"
```

**無需修改程式碼！**程式會自動讀取 `active_strategy` 的設定。

### 切換交易商品

編輯 `config/strategy.yaml` 的 `symbol` 區塊：

```yaml
# === 商品設定 ===
symbol:
  current: "MXF"           # 商品代碼
  contract: "MXF202511"    # 合約月份
  name: "小台指期貨"
  exchange: "TAIFEX"
```

### 程式使用

`main.py` 超級簡潔：

```python
# 自動從 YAML 讀取配置
config = Config()

# 所有設定都已載入
print(config)  # 顯示當前策略摘要
```

---

## ⏰ 自動化排程 (Cron Job)

### 設定自動啟動/停止

系統支援使用 cron job 自動在交易時段啟動和停止程式。

#### 1. 安裝 Cron Job

```bash
# 給予腳本執行權限
chmod +x start_trading.sh stop_trading.sh

# 從 crontab.txt 安裝定時任務
crontab crontab.txt

# 驗證安裝
crontab -l
```

#### 2. 排程時間表

| 時段 | 啟動時間 | 停止時間 | 說明 |
|------|----------|----------|------|
| **日盤** | 周一～五 08:45 | 周一～五 13:45 | 早盤交易時段 |
| **夜盤** | 周一～五 15:00 | 周二～六 05:00 | 夜盤交易時段 |

#### 3. macOS 權限設定

macOS 需要給予 cron 完全磁盤訪問權限：

1. 開啟 **系統設置** → **隱私與安全性**
2. 點擊 **完全磁盤訪問權限**
3. 點擊 `+` 添加：`/usr/sbin/cron`
   - 找不到時按 `Cmd+Shift+G`，輸入 `/usr/sbin/cron`

#### 4. 測試與監控

```bash
# 手動測試啟動
./start_trading.sh

# 查看運行狀態
pgrep -f "uv run main" -l

# 查看日誌
tail -f logs/trading_$(date +%Y%m%d).log

# 手動停止
./stop_trading.sh
```

#### 5. 常用 Cron 命令

```bash
# 查看當前的 cron jobs
crontab -l

# 編輯 cron jobs
crontab -e

# 刪除所有 cron jobs
crontab -r

# 查看 cron 日誌（macOS）
log show --predicate 'process == "cron"' --last 1h
```

#### 6. 自訂排程時間

編輯 `crontab.txt` 修改時間：

```bash
# 分 時 日 月 週
# │ │ │ │ │
# │ │ │ │ └─── 星期 (0-7, 0和7都是周日)
# │ │ │ └───── 月份 (1-12)
# │ │ └─────── 日期 (1-31)
# │ └───────── 小時 (0-23)
# └─────────── 分鐘 (0-59)

# 例如：每天早上 8:45 啟動
45 8 * * 1-5 cd /path/to/auto_trade && /bin/bash start_trading.sh
```

修改後重新安裝：
```bash
crontab crontab.txt
```

#### ⚠️ 重要提醒

1. **假日處理**: Cron 不知道台灣假期，遇假日需手動處理
2. **首次運行**: 建議先手動測試確認一切正常
3. **日誌監控**: 定期檢查 `logs/` 目錄
4. **測試模式**: 建議先在 `simulation=true` 模式下測試

---

## 📊 策略參數說明

### 交易參數 (trading)

所有參數都在 `trading` 區塊中：

| 參數 | 說明 | 預設值 | 激進 | 保守 |
|------|------|--------|------|------|
| `order_quantity` | 每次下單數量 | 1 | 2 | 1 |
| `timeframe` | K線時間尺度 | 30m | 15m | 30m |
| `stop_loss_points` | 初始停損點數 | 80 | 50 | 100 |
| `start_trailing_stop_points` | 啟動移動停損的獲利點數 | 200 | 100 | 300 |
| `trailing_stop_points` | 移動停損點數 | 200 | 100 | 250 |
| `take_profit_points` | 獲利了結點數 | 500 | 300 | 800 |

> 📌 固定點數也可改用百分比：
> - `stop_loss_points_rate`: 例如 `0.01` = 進場價 × 1%，會覆蓋 `stop_loss_points`
> - `trailing_stop_points_rate`: 移動停損採用百分比
> - `take_profit_points_rate`: 獲利了結採用百分比
> 同一項設定只需擇一，寫在 `config/strategy.yaml` 即可生效。

### 檢測頻率 (monitoring)

| 參數 | 說明 | 預設值 | 激進 | 保守 |
|------|------|--------|------|------|
| `signal_check_interval` | 訊號檢測間隔（分鐘） | 5 | 3 | 10 |

有倉時的停損／移停／停利檢查由行情 **tick** 驅動，不再透過設定檔調整間隔。

---

## 🔧 自訂策略

### 方法 1：修改現有策略

直接編輯 `config/strategy.yaml`，例如修改預設策略：

```yaml
default:
  trading:
    order_quantity: 1
    timeframe: "30m"
    stop_loss_points: 100        # 改為 100 點停損
    start_trailing_stop_points: 250
    trailing_stop_points: 200
    take_profit_points: 600
  
  monitoring:
    signal_check_interval: 5
```

### 方法 2：新增自訂策略

在 `config/strategy.yaml` 中新增一個策略區塊：

```yaml
# 自訂策略
my_strategy:
  trading:
    order_quantity: 1
    timeframe: "30m"
    stop_loss_points: 90
    start_trailing_stop_points: 250
    trailing_stop_points: 220
    take_profit_points: 600
  
  monitoring:
    signal_check_interval: 7
```

然後修改 `active_strategy` 來啟用：
```yaml
active_strategy: "my_strategy"
```

---

## 🚀 快速範例

### 完整配置結構

```yaml
# === 當前啟用的策略 ===
active_strategy: "default"

# === 商品設定 ===
symbol:
  current: "MXF"
  contract: "MXF202511"
  name: "小台指期貨"
  exchange: "TAIFEX"

# === 策略定義 ===
default:
  trading:
    order_quantity: 1
    timeframe: "30m"
    stop_loss_points: 80
    start_trailing_stop_points: 200
    trailing_stop_points: 200
    take_profit_points: 500
  monitoring:
    signal_check_interval: 5

aggressive:
  trading:
    order_quantity: 2
    timeframe: "15m"
    stop_loss_points: 50
    # ... 其他參數
```

### 切換策略的步驟

1. **編輯配置檔**
   ```bash
   vim config/strategies.yaml
   # 或使用任何編輯器
   ```

2. **修改第一行**
   ```yaml
   active_strategy: "aggressive"  # 從 default 改為 aggressive
   ```

3. **重啟程式**
   ```bash
   uv run main
   ```

4. **驗證生效**
   - 程式啟動時會顯示當前策略摘要
   - 檢查輸出確認策略已切換

---

## ⚠️ 注意事項

1. **配置檔案可以提交到 Git**
   - YAML 配置檔案不包含敏感資訊，可以版本控制
   - 敏感資訊（API 金鑰等）存放在 `.env` 檔案中

2. **合約月份設定**
   - 記得定期更新 `symbol.contract` 欄位
   - 期貨合約到期前需要換月

3. **參數調整建議**
   - 先在模擬環境測試
   - 記錄每次調整及其效果
   - 逐步優化，避免大幅改動

4. **策略命名規則**
   - 策略名稱只能包含字母、數字和底線
   - 建議使用有意義的名稱，如：`default`、`aggressive`、`conservative`

5. **修改配置後需重啟**
   - YAML 配置在程式啟動時載入
   - 修改後需要重新啟動程式才會生效

---

## 📝 配置優先順序

1. **環境變數** (`.env`) - 最高優先級
   - API 金鑰
   - 憑證路徑
   - Simulation mode

2. **YAML 配置檔案** (`strategy.yaml`) - 第二優先級
   - 策略選擇 (`active_strategy`)
   - 交易商品設定 (`symbol`)
   - 策略參數 (`default`/`aggressive`/`conservative`)

3. **程式碼預設值** - 最低優先級
   - 僅在前兩者都未設定時使用

---

## 💡 最佳實踐

### 首次使用 Git Clone

```bash
# Clone 專案
git clone https://github.com/pohanwww/auto_trade.git
cd auto_trade

# 複製配置範例檔
cp config/strategy.example.yaml config/strategy.yaml
cp .env.example .env

# 編輯您的個人設定
vim config/strategy.yaml
vim .env

# 安裝依賴並執行
uv run main
```

**重要**: `config/strategy.yaml` 和 `.env` 不會被提交到 Git，保護您的個人設定。

### 多環境配置

如果需要不同環境使用不同配置：

```bash
# 生產環境
cp config/strategy.yaml config/strategy.prod.yaml

# 開發環境
cp config/strategy.yaml config/strategy.dev.yaml

# 使用環境變數切換（需修改 config.py 支援）
export CONFIG_FILE="strategies.prod.yaml"
```

### 策略回測

記錄每次策略調整：

```yaml
# 在策略定義上方加註解
# 2024-10-09: 調整停損點數從 80 -> 100，觀察是否降低虧損次數
default:
  trading:
    stop_loss_points: 100  # 原值: 80
    # ...
```
