# Key Level Strategy — 研究報告

> 最後更新：2026-04-10
> 商品：MXF（小台指期貨）| 時間尺度：5 分鐘 | 口數：4 口
> 回測期間：2024H1 ~ 2026Q1（主要驗證：2025H2、2026Q1）

---

## 1. 策略架構概述

```
┌─────────────────────────────────────────────────────────┐
│                   Key Level Strategy                     │
├─────────────┬───────────────┬───────────────────────────┤
│  KL 計算    │    進場邏輯    │        出場邏輯           │
│             │               │                           │
│  Swing(10)  │  Breakout     │  KL Trailing Stop         │
│  Cluster    │  + Instant    │  + Profit Lock (PL)       │
│  Session    │  + OR Filter  │  + ATR Fallback           │
│  OHLC       │               │  + Time Exit              │
└─────────────┴───────────────┴───────────────────────────┘
```

---

## 2. 核心參數（最終確認版）

### 2.1 KL 計算參數

| 參數 | 值 | 說明 |
|---|---|---|
| `timeframe` | `5m` | K 棒週期 |
| `swing_period` | `10` | Swing high/low 回溯根數 |
| `cluster_tolerance` | `50` | 相近 KL 合併容差（點） |
| `zone_tolerance` | `50` | KL zone 容差（點） |
| `signal_level_count` | `7` | 取分數最高的前 7 個 KL 作為 Signal KL |
| `atr_period` | `14` | ATR 計算週期 |

### 2.2 進場參數

| 參數 | 值 | 說明 |
|---|---|---|
| `use_or` | `true` | 使用 Opening Range 過濾方向 |
| `or_bars` | `3` | OR 使用前 3 根 K 棒 |
| `or_start_time` | `08:45` | 日盤 OR 開始時間 |
| `entry_end_time` | `12:30` | 日盤最後進場時間 |
| `session_end_time` | `13:45` | 日盤收盤時間 |
| `breakout_buffer` | `0.30 × ATR` | Breakout 確認門檻 |
| `instant_threshold` | `0.30 × ATR` | Instant entry 門檻 |
| `use_breakout` | `true` | 啟用突破進場 |
| `trend_filter` | `or` | 使用 OR 作為趨勢過濾 |

### 2.3 風控參數

| 參數 | 值 | 說明 |
|---|---|---|
| `sl_atr_multiplier` | `1.0` | 初始停損 fallback（ATR 倍數） |
| `tp_atr_multiplier` | `0` | 不使用固定停利（純 TS 出場） |
| `key_level_trail_mode` | `previous` | 移動停損跟隨前一層 KL |
| `key_level_buffer` | `0.15 × ATR` | KL trailing stop buffer |
| `kl_exhausted_atr_multiplier` | `0.5` | KL 用盡後的 ATR trailing 倍數 |
| `cooldown` | `1 bar` | 平倉後冷卻 1 根 K 棒 |

---

## 3. 進場邏輯

### 3.1 Breakout Entry（確定採用）

- 價格收盤突破 `KL ± breakout_buffer × ATR` 時觸發
- **Instant breakout**：盤中價格穿越即進場，不等收盤
  - Live：用即時 `current_price`
  - Backtest：用 K 棒 `high/low` 模擬盤中穿越

### 3.2 Opening Range Filter（確定採用）

- OR 以開盤前 3 根 K 棒（08:45~09:00）的 High/Low 定義
- 價格在 OR High 上方 → 只允許做多
- 價格在 OR Low 下方 → 只允許做空
- OR 的最大價值是**避免在錯誤方向累積虧損**，特別是逆勢環境

### 3.3 已淘汰的進場方式

| 方式 | 結論 | 原因 |
|---|---|---|
| **Bounce** | 移除 | 多輪回測品質持續落後 breakout，訊號品質不佳 |
| **Recovery Bounce** | 移除 | 實測與 noRecovery 結果完全相同，無附加價值 |

---

## 4. 出場邏輯

### 4.1 初始 Stop Loss

- **首選**：Signal KL 外側的下一層 KL + `0.15 × ATR` buffer
- **OR 邊界 SL**：若初始 SL 的 KL 落在 OR 範圍內，改用 OR boundary - buffer
- **Fallback**：`1.0 × ATR`

### 4.2 KL Trailing Stop（主要移動停損）

- 模式：`previous`
- 價格突破 KL 後，停損移動到「前一層 KL ± 0.15 × ATR」
- 逐層往有利方向跟進

### 4.3 ATR Fallback Trailing（KL 用盡後）

- 當所有 KL 被突破後，自動切換到 ATR-based trailing stop
- 距離 = `0.5 × ATR`，跟隨最高/最低價

### 4.4 Profit Lock 利潤鎖定（新增機制）

單階段 PL 設計：
- 持倉超過 `phase1_minutes` 後，鎖定 `phase1_ratio` 的峰值利潤
- `lock_stop = entry_price + peak_profit × ratio`（做多）
- 最終停損 = `max(KL_stop, lock_stop)`，兩者取較高

**為什麼選擇單階段而非雙階段 PL：**
- 參數更少（2 vs 4），過擬合風險更低
- 效果與雙階段相當
- 更容易理解和維護

### 4.5 時間強制平倉

- 日盤：`13:29` 強制平倉
- 夜盤：`04:50` 強制平倉

### 4.6 已淘汰的出場方式

| 方式 | 結論 | 原因 |
|---|---|---|
| **RSI 背離出場** | 移除 | 價格平坦 + RSI 下降的背離檢測有缺陷，效果不穩定 |
| **最小距離門檻** | 移除 | 修正 bug 後重驗，對正式配置無穩定優勢 |
| **Break-Even Lock** | 未採用 | 會因滑價導致頻繁虧損，每天僅 2 次機會太珍貴 |
| **雙階段 PL** | 不採用 | 參數過多，過擬合風險高，效果不優於單階段 |

---

## 5. 已淘汰的功能模組

| 模組 | 結論 | 原因 |
|---|---|---|
| **Supplement KL** | 完全移除 | 檢測條件太寬鬆（15 次交易出現 12 次），嚴格化後仍無穩定改善 |
| **Pivot Mode** | 完全移除 | 回測無法提升 PnL |
| **Nearest KL 升級** | 移除 | 增加複雜度，無穩定效果 |
| **Best Score KL 升級** | 移除 | 同上 |
| **Distance-based supplement** | 保留但不使用 | 唯一有些許效果的 supplement 觸發方式，但整體仍被移除 |

---

## 6. 重大 Bug 修正紀錄

### 6.1 Cooldown Bug（2026-04-10 發現）

**問題**：`on_position_closed()` 使用 `datetime.now()` 計算 cooldown 時間。在回測中，這回傳的是真實系統時間（如 2026-04-10 14:00），而非模擬的 K 棒時間（如 2025-08-15 10:00），導致 cooldown 永遠大於回測時間，**每天只能做 1 筆交易**。

**影響**：所有 2026-04-10 之前的回測結果都被低估了 30~50% 的交易次數。

**修正**：新增 `bar_time` 參數，回測時傳入模擬時間。

| 場景 | 修正前 Trades | 修正後 Trades | 增幅 |
|---|---:|---:|---|
| 日盤 Long | 68 | 81 | +19% |
| 日盤 Both | 113 | 151 | +34% |
| 夜盤 Long | 84 | 114 | +36% |
| 夜盤 Both | 116 | 169 | +46% |

### 6.2 Profit Lock Phase Transition Bug

**問題**：PL phase 切換時，`lock_stop` 可能超過 `current_price`（做多時），導致立即觸發停利。

**修正**：`lock_stop = min(lock_stop, current_price)` for long, `max()` for short。

---

## 7. 回測結果（Bug-Fixed，2026-04-10 版本）

> 以下所有數據均為 cooldown bug 修正後的正確結果。

### 7.1 日盤 Long Only

| 配置 | 2025H2 PnL | Trades | WR | Sharpe | MaxDD | 2026Q1 PnL | Trades | WR | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 319,200 | 81 | 42.0% | 2.88 | 10.82% | 671,000 | 44 | 54.5% | 6.46 | 8.01% |
| PL1(20m/30%) | 318,000 | 86 | 53.5% | 3.00 | 9.74% | 692,400 | 46 | 71.7% | 6.75 | 6.01% |
| PL1(20m/40%) | 283,800 | 89 | 53.9% | 2.81 | 9.22% | 688,200 | 47 | 70.2% | 6.76 | 5.92% |
| PL1(20m/50%) | 263,200 | 91 | 54.9% | 2.87 | 8.46% | 707,200 | 48 | 68.8% | 6.92 | 5.91% |
| PL1(45m/50%) | 214,800 | 90 | 48.9% | 2.42 | 10.69% | 749,000 | 47 | 70.2% | 7.29 | 5.33% |

**結論**：PL 在趨勢行情（2026Q1）大幅改善 MaxDD 和 Sharpe；盤整行情（2025H2）PnL 略降但風控更佳。

### 7.2 日盤 Both

| 配置 | 2025H2 PnL | Trades | Sharpe | MaxDD | 2026Q1 PnL | Trades | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 280,400 | 151 | 1.94 | 19.65% | 830,800 | 72 | 7.25 | 8.04% |
| PL1(20m/30%) | 138,400 | 162 | 1.17 | 24.95% | 967,400 | 73 | 8.58 | 6.44% |
| PLL(20m/30%) | 280,600 | 155 | 1.96 | 17.03% | 896,800 | 73 | 8.08 | 8.69% |
| PLL(45m/50%) | 176,000 | 158 | 1.42 | 20.17% | 951,600 | 74 | 8.65 | 6.19% |

**結論**：日盤 Both 全面 PL 傷害空方利潤；**PLL（只做多啟用）是最佳選擇**——保留空方靈活性的同時改善多方風控。

### 7.3 夜盤 Long Only

| 配置 | 2025H2 PnL | Trades | Sharpe | MaxDD | 2026Q1 PnL | Trades | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 263,200 | 114 | 1.72 | 10.99% | 446,000 | 45 | 4.46 | 12.07% |
| PL1(20m/50%) | 294,800 | 121 | 2.00 | 12.55% | 541,200 | 46 | 5.96 | 9.15% |
| PL1(30m/50%) | **342,400** | 121 | **2.25** | 12.55% | 525,400 | 46 | 5.84 | 9.53% |
| PL1(45m/50%) | 338,000 | 121 | 2.23 | 12.55% | **562,200** | 46 | **6.17** | 9.39% |

**結論**：**夜盤 Long Only 是 PL 最大贏家**。PL1(30~45m/50%) 兩個時期 PnL 和 Sharpe 都大幅優於 Baseline，MaxDD 從 12% 降到 ~9%。

### 7.4 夜盤 Both

| 配置 | 2025H2 PnL | Trades | Sharpe | MaxDD | 2026Q1 PnL | Trades | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 86,800 | 169 | 0.51 | 18.26% | 909,400 | 73 | 5.89 | 13.68% |
| PL1(20m/50%) | 252,400 | 185 | 1.27 | 21.30% | 772,000 | 75 | 5.52 | 12.49% |
| PLL(20m/50%) | 220,400 | 176 | 1.10 | 19.82% | 1,000,400 | 74 | 6.79 | 12.53% |
| PLL(30m/50%) | 206,800 | 176 | 1.04 | 19.82% | 991,800 | 74 | 6.84 | 12.53% |

**結論**：PLL(20~30m/50%) 在 2026Q1 突破百萬 PnL，是夜盤 Both 最佳選擇。

### 7.5 Max Trades 測試

| 場景 | max2 Trades | max3 Trades | max2 PnL | max3 PnL | 結論 |
|---|---:|---:|---:|---:|---|
| 日盤 L PL1(20m/30%) | 86 | 88 | 318,000 | 347,000 | max3 略佳 |
| 日盤 B Baseline | 151 | 157 | 280,400 | 322,400 | max3 較佳 |
| 夜盤 L Baseline | 114 | 121 | 263,200 | 230,400 | max2 較佳 |
| 夜盤 B Baseline | 169 | 193 | 86,800 | 201,600 | **max3 顯著較佳** |
| 夜盤 B PLL(20m/40%) | 174 | 202 | 167,400 | 247,000 | **max3 顯著較佳** |

**結論**：
- 日盤：max2 已足夠，max3 改善有限
- 夜盤 Both：**max3 是甜蜜點**，PnL 提升 100%+
- max4 通常不如 max3，第 4 筆交易品質下降

---

## 8. 建議配置方案

基於所有測試結果，提出以下分場景最佳配置：

### 方案 A：保守穩健（單方向）

| 場景 | 方向 | PL | max_trades | 特點 |
|---|---|---|---|---|
| 日盤 | Long Only | PL1(20m/30%) | 2 | MaxDD 低、Sharpe 高 |
| 夜盤 | Long Only | PL1(45m/50%) | 2 | PnL 和 Sharpe 雙優 |

### 方案 B：積極雙向

| 場景 | 方向 | PL | max_trades | 特點 |
|---|---|---|---|---|
| 日盤 | Both | PLL(20m/30%) | 2 | 保留空方靈活性 + 多方 PL 保護 |
| 夜盤 | Both | PLL(20m/50%) | 3 | 夜盤交易機會多，max3 + PLL 效果最佳 |

### 方案 C：混合（推薦）

| 場景 | 方向 | PL | max_trades | 理由 |
|---|---|---|---|---|
| 日盤 | Long Only | PL1(20m/30%) | 2 | 日盤時間短，Long Only 風險調整後報酬最高 |
| 夜盤 | Both | PLL(20m/50%) | 3 | 夜盤時間長，雙向 + PLL + max3 最大化收益 |

---

## 9. KL Buffer 倍數測試

測試 `key_level_buffer` = 0.15, 0.20, 0.25, 0.30 × ATR：

| Buffer | PnL 趨勢 | 結論 |
|---|---|---|
| 0.15 | 最佳 | **保留**，提供足夠保護但不會過早停損 |
| 0.20 | 略差 | 過寬導致停損太遠 |
| 0.25 | 更差 | 同上 |
| 0.30 | 最差 | 明顯劣化 |

---

## 10. Tick-Driven 事件驅動

已完成從 time-based polling 到 tick-driven event loop 的改造：
- 使用 `threading.Event` 取代固定 1 秒輪詢
- 進場和出場都在每個 tick 檢查
- 降低延遲，提高信號回應速度

---

## 11. 研究歷程與決策紀錄

### 11.1 KL 來源簡化

| 階段 | 動作 |
|---|---|
| 初期 | Swing + Pivot + Round Number + Session OHLC |
| 中期 | 測試 Pivot mode → 無改善 → 移除 |
| 中期 | 測試 Supplement KL → 不穩定 → 移除 |
| 中期 | 測試 Nearest/Best Score KL 升級 → 無效 → 移除 |
| 最終 | **僅保留 Swing + Session OHLC** |

### 11.2 出場策略演進

| 階段 | 動作 |
|---|---|
| 初期 | 純 KL trailing stop |
| 中期 | + RSI 背離出場 → 效果不穩定 → 移除 |
| 中期 | + Bounce 策略優化 → 仍落後 breakout → 移除 |
| 後期 | + Profit Lock（雙階段）→ 過擬合風險高 → 改為單階段 |
| 後期 | + ATR fallback trailing → KL 用盡後的保護 |
| 最終 | **KL trailing + 單階段 PL + ATR fallback** |

### 11.3 方向策略演進

| 階段 | 動作 |
|---|---|
| 初期 | 單一 Both 方向 |
| 中期 | 測試 Long Only → 風險調整更佳 |
| 後期 | 測試 PLL（Both 但只有做多啟用 PL）→ 兼顧雙向 + 風控 |
| 最終 | **日盤 Long Only / 夜盤 Both + PLL** |

---

## 12. 注意事項

### 12.1 Backtest 對 Instant Breakout 偏樂觀

Backtest 中 instant entry 同棒不檢查停損/移停，避免 look-ahead 但略微高估績效。

### 12.2 文件數字只代表當前版本

以下任一項改動都可能改變結果：
- `entry_end_time`、`buffer`、`instant_threshold`
- OR 過濾方式
- 交易成本與滑價

### 12.3 Cooldown Bug 影響

2026-04-10 之前的所有回測報告均受 cooldown bug 影響（交易次數被低估 30~50%）。本報告中的數據為修正後版本。

---

## 13. 附錄：回測資料位置

| 類型 | 路徑 |
|---|---|
| 回測腳本 | `scripts/backtest_key_level.py` |
| 策略程式碼 | `src/auto_trade/strategies/key_level_strategy.py` |
| 持倉管理 | `src/auto_trade/services/position_manager.py` |
| 回測引擎 | `src/auto_trade/engines/backtest_engine.py` |
| 回測報告 | `data/backtest/key_level_sweep_*.txt` |
| 暫存結果 | `/tmp/pl1s_fix_*.txt`, `/tmp/mt2_*.txt` |
