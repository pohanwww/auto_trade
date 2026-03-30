# Key Level Strategy — 研究報告

> 最後更新：2026-03-30
> 商品：MXF（小台指期貨）| 時間尺度：5 分鐘 | 口數：4 口

---

## 1. 當前正式配置

目前正式保留與使用的設定只有一組：

- `kl_allweather`
- 型態：**OR B BK**
- 含義：`use_or=true`、`both directions`、`breakout_only`

### 參數摘要

| 參數 | 值 |
|---|---|
| `timeframe` | `5m` |
| `entry_end_time` | `13:00` |
| `session_end_time` | `13:45` |
| `signal_level_count` | `7` |
| `breakout_buffer` | `0.3 × ATR` |
| `instant_threshold` | `0.3 × ATR` |
| `max_trades_per_day` | `2` |
| `sl_atr_multiplier` | `1.0` |
| `tp_atr_multiplier` | `0` |
| `key_level_trail_mode` | `previous` |
| `key_level_buffer` | `0.15 × ATR` |
| `use_breakout` | `true` |
| `use_bounce` | `false` |

---

## 2. 策略邏輯

### 進場

- **Key level detection**：整合 Swing、Pivot、Round Number、Session OHLC 等來源形成關鍵價位
- **Breakout signal**：收盤突破 `key_level ± breakout_buffer × ATR`
- **Instant breakout**：
  - live：用即時 `current_price`
  - backtest：用 K 棒 `high/low` 模擬盤中穿越
- **OR filter**：
  - 價格在 `OR High` 上方才允許做多
  - 價格在 `OR Low` 下方才允許做空

### 出場

- **初始 Stop Loss**
  - breakout：用訊號 key level 外側的下一層 key level，加上 `0.15 × ATR` buffer
  - fallback：`1.0 × ATR`
- **Trailing Stop**
  - 模式：`previous`
  - 價格突破某一層 key level 後，停損移到「前一層 key level ± 0.15 × ATR」
  - 若沒有前一層，fallback 到 `entry`
  - 已移除「最小距離門檻」機制
- **Force Exit**：`13:29`

### 近期修正

- live 的 instant breakout 改用 `current_price`
- backtest 的 instant breakout 改用 `high/low` 穿越，不再要求 close 留在 level 上方/下方
- 加入 `cooldown = 1 bar`
- 修復重啟後策略狀態恢復：`trades_today`、`cooldown_until`
- Sharpe 計算排除週末日期
- `previous` trailing stop 現在保留 `ATR-based buffer`
- 移除 `previous` 的最小距離門檻（實測對正式配置無優勢）

---

## 3. 已驗證結論

### 確定保留

| 項目 | 結論 |
|---|---|
| `instant_threshold = 0.3` | 保留 |
| `breakout_only` | 保留 |
| `key_level_trail_mode = previous` | 保留 |
| `key_level_buffer = 0.15 × ATR` | 保留 |
| `max_trades_per_day = 2` | 保留 |
| `signal_level_count = 7` | 保留 |
| `day_only` | 保留 |

### 確定移除

| 項目 | 原因 |
|---|---|
| `bounce` | 回測品質持續落後 breakout only |
| `previous` 最小距離門檻 | 修正 bug 後重新驗證，對正式配置無穩定優勢，且增加複雜度 |

---

## 4. 最新回測摘要

### 4.1 OR B BK（正式配置）

條件：

- `entry_end_time = 13:00`
- `5m`
- `max2/day`
- `previous + 0.15 × ATR buffer`

| 時段 | PnL | Trades | WR | PF | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|
| `2024` | +367,600 | 297 | 43.4% | 1.19 | 1.21 | 18.33% |
| `2025` | +647,000 | 300 | 40.0% | 1.34 | 2.20 | 16.24% |
| `2025H2` | +212,400 | 167 | 37.1% | 1.19 | 1.57 | 21.03% |
| `2026Q1` | +615,200 | 82 | 41.5% | 2.45 | 6.15 | 9.11% |
| `202603` | +79,200 | 28 | 39.3% | 1.40 | 2.26 | 10.40% |

**5 期總 PnL：`+1,921,400`**

### 4.2 與 OR L BK 對照

同條件下，`OR L BK` 的結果：

| 時段 | PnL | Trades | WR | PF | Sharpe | MaxDD |
|---|---:|---:|---:|---:|---:|---:|
| `2024` | +543,000 | 153 | 50.3% | 1.60 | 2.33 | 9.59% |
| `2025` | +478,800 | 157 | 40.1% | 1.55 | 2.09 | 15.39% |
| `2025H2` | +358,000 | 96 | 39.6% | 1.69 | 3.11 | 10.94% |
| `2026Q1` | +564,000 | 54 | 42.6% | 3.31 | 6.06 | 7.32% |
| `202603` | +69,000 | 16 | 43.8% | 1.54 | 2.29 | 9.12% |

**5 期總 PnL：`+2,012,800`**

### 4.3 解讀

- `OR B BK` 優勢：
  - 2025、2026Q1、202603 的 PnL 較高
  - 牛熊雙向都能參與
- `OR B BK` 劣勢：
  - 交易數明顯較多
  - `MaxDD` 普遍大於 OR L BK
  - `PF` 與 `Sharpe` 不如 OR L BK 穩定
- 如果只看「風險調整後報酬」，OR L BK 常常更乾淨
- 如果只保留單一全天候版本，OR B BK 仍然是較完整的雙向方案

---

## 5. 重要檢查結果

### 5.1 為什麼保留 OR B BK

- live 最終需求是只保留一組設定
- OR B BK 能同時處理多頭與空頭環境
- 雖然不是每個時段都最優，但作為單一常駐策略更完整

### 5.2 其他設定的保留發現

雖然正式配置只留 `OR B BK`，但其他設定的研究發現仍然有保存價值，因為未來重新調參時還會用到。

#### OR L BK

- 在 `2024`、`2025H2` 這類區段，`OR L BK` 的 `PF`、`Sharpe`、`MaxDD` 明顯優於 `OR B BK`
- 如果未來目標改成「降低交易數、降低回撤、只做偏多」，`OR L BK` 仍然是最有價值的備選
- 它不是沒被選上，而是因為目前 live 需求是保留一組能雙向運作的全天候版本

#### Pure B BK

- 在偏盤整階段，`Pure B BK` 有機會因為沒有 OR 過濾而抓到更多交易
- 但穩定性較差，遇到趨勢段時更容易回撤放大
- 結論不是「不能用」，而是比較適合明確判斷為盤整時再考慮

#### OR Filter

- OR 的最大價值不是放大利潤，而是避免在錯誤方向一直累積虧損
- 特別是逆勢環境，OR 常常能把大虧損壓到小虧甚至轉正
- 代價是盤整期可能過濾掉本來可以做的交易

#### Bounce

- `bounce` 在先前多組回測中持續落後 `breakout_only`
- 問題不是只有參數沒調好，而是訊號品質本身不夠乾淨
- 所以現在雖然文件保留這個研究結論，但正式配置不啟用

#### Instant breakout

- `instant_threshold = 0.3` 仍然是策略核心之一
- 不論最後選 `OR B BK` 或 `OR L BK`，instant breakout 都明顯優於等收盤才進場
- 也因此 live/backtest 對 instant 的判斷邏輯修正，是這輪最重要的系統修正之一

### 5.3 最小距離門檻驗證結果

曾測試 `previous` trailing stop 的最小距離：

- `0`
- `0.62 × ATR`
- `1.272 × ATR`

在修正 `key_level_atr` metadata bug 後重新驗證，對 `#005 OR L BK`：

- `0` 的整體結果最好
- `0.62 × ATR` 中性偏弱
- `1.272 × ATR` 在近期階段明顯劣化

結論：**這套機制已移除，不再保留。**

---

## 6. 已淘汰設定與原因

| 設定 | 狀態 | 原因 |
|---|---|---|
| `kl_consolidation` | 不放入正式 yaml | 盤整期可能更強，但全天候穩定性不足 |
| `kl_trend_long` | 不放入正式 yaml | 假設過強，只有特定市場狀態才適合 |
| `kl_trend_short` | 不放入正式 yaml | 同上，且 live 管理複雜度提高 |
| `bounce enabled` | 停用 | 訊號品質不佳，A/B 長期落後 breakout only |
| `previous + min_dist` | 停用 | 增加複雜度，沒有穩定帶來績效改善 |

---

## 7. 目前仍需注意的點

### 7.1 Backtest 對 instant breakout 仍偏樂觀

backtest 中，instant entry 同棒不會檢查同棒停損/移停，避免 look-ahead，但也會略微高估績效。

### 7.2 文件數字只代表當前版本

以下任一項改動都可能改變結果：

- `entry_end_time`
- `buffer`
- `instant_threshold`
- OR 過濾
- 交易成本與滑價

所以本文件只對應 **2026-03-30 當前版本**。

---

## 8. 報告位置

詳細回測報告（含逐筆交易）存放於：

- `data/backtest/key_level_sweep_*.txt`

最近常用檔案類型：

- `key_level_sweep_2024_trailing_*.txt`
- `key_level_sweep_2025_trailing_*.txt`
- `key_level_sweep_2025H2_trailing_*.txt`
- `key_level_sweep_2026Q1_trailing_*.txt`
- `key_level_sweep_202603_trailing_*.txt`
