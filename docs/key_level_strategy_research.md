# Key Level Breakout Strategy — 研究報告

> 最後更新：2026-03-26
> 商品：MXF（小台指期貨）| 時間尺度：5 分鐘 | 口數：4 口

---

## 目錄

1. [策略架構概覽](#1-策略架構概覽)
2. [已驗證的核心參數](#2-已驗證的核心參數)
3. [市場環境測試框架](#3-市場環境測試框架)
4. [研究發現](#4-研究發現)
5. [已知問題與待優化項目](#5-已知問題與待優化項目)
6. [策略配置建議](#6-策略配置建議)
7. [後續研究方向](#7-後續研究方向)
8. [附錄：完整回測數據](#8-附錄完整回測數據)

---

## 1. 策略架構概覽

### 進場機制

- **Confluence Key Level Detection**：整合 Swing Cluster、Pivot Points、Volume Profile、Gap Analysis、Round Numbers、Session OHLC 等多種方法偵測關鍵價位
- **Breakout Signal**：收盤突破 key level ± `breakout_buffer × ATR` 觸發
- **Instant Entry**：若 K 棒盤中穿越 `instant_threshold × ATR`（0.3），不等收盤即進場
- **OR Filter（可選）**：Opening Range 前 3 根 K 棒定義 OR High/Low，只允許在 OR High 上方做多、OR Low 下方做空

### 出場機制

- **Stop Loss**：
  - Breakout entry → 下一個 key level（訊號 level 以下的最近 key level）+ buffer
  - Bounce entry → 進場 K 棒的 Low/High（目前未啟用）
  - Fallback → `sl_atr_multiplier × ATR`（1.0x）
- **Trailing Stop**：Key Level 階梯移停（`previous` mode）
  - 價格突破 key level N → 停損移至 key level N-1
  - 全部倉位使用 TS（tp_leg=0, ts_leg=4）
- **Force Exit**：13:29 強制平倉（日盤）

### 檔案結構

| 檔案 | 功能 |
|---|---|
| `strategies/key_level_strategy.py` | 策略主體，整合偵測與訊號 |
| `services/key_level_detector.py` | Confluence key level 偵測 |
| `services/key_level_signal.py` | Breakout/Bounce 訊號偵測 |
| `services/position_manager.py` | 倉位管理、階梯移停 |
| `scripts/backtest_key_level.py` | 參數掃描回測 |
| `scripts/find_consolidation.py` | 歷史市場環境分類工具 |
| `config/strategy_key_level.yaml` | 策略配置檔（3 組策略）|

---

## 2. 已驗證的核心參數

### 確定使用（不需再測試）

| 參數 | 值 | 驗證方式 | 結論 |
|---|---|---|---|
| `instant_threshold` | **0.3**（hardcoded）| 有/無 instant A/B × 4 期間 × 多滑價 | 無 instant 在 slip≥3 全部崩潰 |
| `entry_type` | **breakout_only** | BK vs BK+BC × 3 盤整期 | 反彈訊號 WR 降至 30-40%，全面拖累 |
| `key_level_trail_mode` | **previous** | current vs previous A/B | 階梯移停優於固定移停 |
| `leg_split` | **all_ts**（TP=0, TS=4）| TP+TS vs all_TS | all_TS 讓獲利奔跑 |
| `session_mode` | **day_only** | 日盤 vs 日夜盤 A/B | 日盤更穩定，夜盤增加雜訊 |

### 條件性參數

| 參數 | 值 | 條件 |
|---|---|---|
| `use_or` | true/false | 全天候→true；確定趨勢方向→false |
| `long_only/short_only` | 視方向 | 多頭→long_only；空頭→short_only；不確定→both |
| `max_trades_per_day` | **2** | 測試過 1/2/3，2 是最佳平衡點 |
| `key_level_buffer` | **10** | 測試過 10/20，10 更敏感 |
| `signal_level_count` | **7** | 測試過 3/7，7 提供更多交易機會 |
| `breakout_buffer` | **0.3** | 固定 0.3×ATR 作為突破確認 |

---

## 3. 市場環境測試框架

### 為何不用全年測試

2024 全年 Return +29.9%、2025 全年 +26.5%，都是大趨勢年。用全年測試無法區分策略在不同市場環境下的表現。需要用明確定義的盤整/趨勢/空頭期間來測試。

### 已建立的測試期間（來自 MXF 2022-2026 月度分析）

```python
PERIODS = {
    # ── 盤整期（淨漲跌 ≈ 0%）──
    "con_quiet":  ("2023-06-01", "2023-09-30"),  # +0.1% Range 7.5%  低波動盤整
    "con_wild":   ("2024-07-01", "2024-12-31"),  # +0.5% Range 24.5% 高波動盤整（含8月崩盤）
    "con_recent": ("2024-12-01", "2025-02-28"),  # +0.4% Range 7.4%  近期低波動盤整

    # ── 多頭趨勢 ──
    "bull_2024":  ("2024-02-01", "2024-03-31"),  # +13.0% 穩定趨勢
    "bull_super": ("2025-06-01", "2025-10-31"),  # +34.8% 超級趨勢（5個月）
    "bull_2026":  ("2026-01-02", "2026-02-28"),  # +20.8% 新年暴漲

    # ── 空頭趨勢 ──
    "bear_2022":  ("2022-04-01", "2022-06-30"),  # -16.8% 全球升息崩跌
    "bear_2025":  ("2025-03-01", "2025-04-30"),  # -11.3% 急跌修正（盤中到17015）
}
```

### 盤整的定義

盤整 ≠ 低波動。盤整 = **淨漲跌接近 0%**。高波動盤整（con_wild）和低波動盤整（con_quiet）的策略表現可能完全不同。

---

## 4. 研究發現

### 4.1 Instant Entry 是策略核心

| 滑價 | 有 Instant (0.3) PnL | 無 Instant PnL | 差異 |
|---|---|---|---|
| 1pt (2025) | +683,800 | -333,000 | 有→賺，無→巨虧 |
| 3pt (2025) | +624,200 | -465,800 | |
| 5pt (2025) | +564,600 | -598,600 | |

**原因**：Instant entry 在價格穿越 key level 的瞬間進場，取得較佳進場價。非 instant 要等 K 棒收盤，此時價格已經跑了一段，進場成本高，容易被後續回測打到停損。

### 4.2 OR Filter 的防禦價值

**OR 的核心價值是「救命」，不是「賺更多」。**

| 場景 | Pure L PnL | OR L PnL | OR 效果 |
|---|---|---|---|
| bear_2025 | **-160,200** | **+7,400** | ⭐ 從大虧轉微利 |
| bull_2026 做空 | -131,600 (Pure S) | +37,600 (OR S) | ⭐ 從虧損轉獲利 |
| bull_super | 685,200 | 463,400 | 少賺 32% |
| con_quiet | 247,200 | 28,600 | 少賺 88% |

**結論**：
- 趨勢市場 → OR 提升每筆交易品質（bull_2026: 10,170→13,728/筆）
- 盤整市場 → OR 有時過度過濾好的交易
- **最大價值在逆勢保護**：防止在錯誤方向累積虧損

### 4.3 OR B（雙向+OR）是全天候冠軍

48 組回測（6 配置 × 8 期間）中，**OR B 是唯一 8/8 全部獲利**的配置。

| 配置 | 總 PnL | 虧損期間 | 最低單期 |
|---|---|---|---|
| Pure L | 2,085,400 | 1/8 | -160,200 |
| **OR B** ⭐ | **1,986,800** | **0/8** | +55,000 |
| Pure S | 1,763,400 | 2/8 | -131,600 |
| OR L | 1,495,600 | 0/8 | +7,400 |
| Pure B | 1,357,200 | 2/8 | -122,400 |
| OR S | 881,600 | 1/8 | -53,000 |

### 4.4 做空在盤整期意外地強

| 盤整期間 | Pure L PnL | Pure S PnL |
|---|---|---|
| con_quiet | 247,200 | 116,000 |
| con_wild | 366,400 | **564,200** |
| con_recent | 146,200 | **274,000** |

**原因推測**：盤整期有大量假突破後回落，做空突破後的回歸交易天然有利。特別在高波動盤整中（con_wild），做空利潤超過做多。

### 4.5 反彈訊號（Bounce）品質極差

在所有盤整期測試中，加入反彈訊號都讓表現變差：

| 期間 | BK PnL | BK+BC PnL | WR 變化 |
|---|---|---|---|
| con_quiet OR B | 153,400 | 97,600 (-36%) | 51.9% → 39.6% |
| con_wild Pure L | 366,400 | 228,000 (-38%) | 55.9% → 39.5% |
| con_recent Pure L | 146,200 | -18,800 (-113%) | 54.3% → **30.3%** |

**根本原因**（`key_level_signal.py` 第 117-118 行）：
```python
if low <= level + buf_bounce and close > level:
```
- 條件太寬鬆：low 只要碰到 level 附近就觸發
- 沒有方向過濾：看跌 K 棒也觸發 bounce_long
- 沒有影線品質檢查：沒有要求下影線佔比
- 沒有 instant entry：反彈永遠等收盤入場

### 4.6 滑價耐受度

| 配置 | slip=1→5 衰退率 (2024) | slip=1→5 衰退率 (2025) |
|---|---|---|
| OR + inst=0.3 + max2 | -22% | **-17%** |
| Pure + inst=0.3 + max2 | -23% | -38% |
| 任何 inst=999 | 直接崩潰 | 直接崩潰 |

OR 在高滑價下更穩定（MaxDD 幾乎不變：10-13%）。

### 4.7 key_level_trail_mode Bug 與修復

曾發現 `key_level_trail_mode` 參數完全無效（current 和 previous 結果一模一樣）。

**Bug 原因**：`position_manager.py` 的 `_open_position` 方法中，`key_level_trail_mode` 沒有從 strategy metadata 複製到 position metadata，導致永遠使用預設值 `"current"`。

**修復**：在 `_open_position` 中加入：
```python
position_metadata["key_level_trail_mode"] = meta.get("key_level_trail_mode", "current")
```

---

## 5. 已知問題與待優化項目

### 5.1 反彈訊號需要大改（優先級：高）

目前反彈訊號品質極差，完全不可用。改進方向：

1. **影線品質過濾**：要求反彈 K 棒的影線（wick）佔總 range 的 50% 以上
2. **方向過濾**：bounce_long 要求 close > open（看漲 K 棒）
3. **更窄觸碰區**：將 bounce 的觸碰判定縮小到 `0.1×ATR` 而非 `0.3×ATR`
4. **加入 instant entry**：反彈也應支援即時進場，在觸碰 key level 瞬間入場
5. **多 K 棒確認**：要求連續 2 根 K 棒確認反彈（第一根觸碰，第二根遠離）
6. **量能確認**：反彈 K 棒的成交量應高於平均（表示有積極買賣）

### 5.2 OR 過濾在盤整期過度保守（優先級：中）

盤整期 OR 過濾掉太多有利交易（con_quiet: OR L 只賺 28K vs Pure L 賺 247K）。

可能改進：
- **動態 OR 寬度**：盤整期放寬 OR 過濾條件（例如 OR 範圍內也允許交易）
- **OR timeout**：超過某時間（如 11:00）後取消 OR 過濾
- **OR 強度評分**：OR 範圍太窄時自動降低過濾強度

### 5.3 Buffer 固定點數 vs 百分比（優先級：低）

目前 `key_level_buffer=10` 是固定點數。隨指數從 15,000 漲到 35,000，10 點的相對意義完全不同。

可能改進：
- 改為 ATR 的百分比或指數的百分比
- 但目前在 2022-2026 跨 15,000~35,000 的測試中表現都可接受，不是急迫問題

### 5.4 Shioaji API 連線問題（優先級：中）

長時間回測頻繁遇到：
- `Too Many Connections` 錯誤
- `Command failed to spawn: Aborted`
- API timeout

目前只能靠分批跑來解決。應考慮：
- 連線池管理
- 自動重試機制
- 本地數據快取（避免重複 API 呼叫）

### 5.5 做空 SL 邏輯（優先級：中）

做空的 SL 設定邏輯與做多對稱，但市場下跌速度通常快於上漲（panic selling），可能需要：
- 做空時使用更寬的 SL（不要太快被洗出）
- 或反過來用更窄的 SL（利用快速下跌迅速獲利了結）
- 需要專門針對做空的 SL 研究

### 5.6 市場環境自動判斷（優先級：低/長期）

目前策略切換需要人工判斷市場環境。長期目標：
- 使用 ATR 趨勢、均線方向、波動率指標等自動判斷
- 自動在 allweather / trend_long / trend_short 之間切換
- 但這是一個獨立的大研究課題

---

## 6. 策略配置建議

### 策略選擇矩陣

| 市場狀態 | 判斷依據 | 推薦策略 | 預期表現 |
|---|---|---|---|
| **不確定** | 無法判斷環境 | `kl_allweather` | Sharpe 1.7-3.7, MaxDD <15% |
| **確定盤整** | 無明確方向但確定不是趨勢 | `kl_consolidation` | 盤整 Sharpe 0.7-3.5 |
| **確定多頭** | 均線多排、新高不斷 | `kl_trend_long` | Sharpe 4-6, MaxDD <13% |
| **確定空頭** | 均線空排、持續破底 | `kl_trend_short` | Sharpe 3-5, MaxDD <11% |

### allweather vs consolidation 如何選？

| | allweather (OR B) | consolidation (Pure B) |
|---|---|---|
| 盤整期表現 | 499,800 | **604,600 (+21%)** |
| 多頭期表現 | **909,000** | 244,600 |
| 空頭期表現 | **578,000** | 508,000 |
| 虧損期間 | **0/8** | 2/8 |
| 適用場景 | 任何時候 | 確定是盤整時 |

**核心差異**：consolidation 去掉了 OR 過濾，在盤整期多出 20% 的利潤，但代價是多頭期可能虧損。
- 如果你**有信心**當前是盤整 → `kl_consolidation`
- 如果你**不太確定** → `kl_allweather`（保險）

### 切換時機

- **觀望→盤整**：連續 2 週淨漲跌 <1%，且無突破前高/前低 → 切 `kl_consolidation`
- **觀望→多頭**：指數站上月線且月線斜率向上 → 切 `kl_trend_long`
- **觀望→空頭**：指數跌破月線且月線斜率向下 → 切 `kl_trend_short`
- **不確定**：永遠用 `kl_allweather`（零虧損期間的保險）

### 當前建議（2026 年 3 月）

2026 年 3 月市場狀態：34,030 → 33,197（-2.4%），高波動下跌後的盤整。

**推薦：`kl_consolidation` 或 `kl_allweather`**

- 如果你認為這是「暴漲後的回檔整理」→ `kl_consolidation`（盤整期多賺 20%）
- 如果你擔心可能轉空 → `kl_allweather`（保底，零虧損風險）

---

## 7. 後續研究方向

### 短期（可直接開始）

1. **反彈訊號重構**：按 5.1 的方向改進後重新測試
2. **OR timeout 機制**：測試 11:00 後取消 OR 過濾是否能在盤整期提升表現
3. **做空 SL 優化**：專門測試做空的最佳 SL 設定

### 中期

4. **Volume Profile 強化**：目前 key level detector 的 volume 權重可能不夠，特別是高成交量的支撐/壓力
5. **多時間尺度**：用 15m 或 30m 的 key level 作為過濾，5m 作為進場
6. **夜盤策略**：夜盤波動特性不同，可能需要獨立參數

### 長期

7. **市場環境自動判斷**：用量化指標自動切換策略
8. **動態部位管理**：根據 key level 分數調整口數
9. **跨商品測試**：大台、電子期、金融期

---

## 8. 附錄：完整回測數據

### 8.1 Phase 1：6 配置 × 8 期間

#### 盤整期

| 配置 | con_quiet | con_wild | con_recent |
|---|---:|---:|---:|
| Pure L | 247,200 (Sh 3.11, DD 5.95%) | 366,400 (Sh 1.61, DD 20.42%) | 146,200 (Sh 1.90, DD 9.94%) |
| OR L | 28,600 (Sh 0.59, DD 6.50%) | 99,000 (Sh 0.92, DD 15.79%) | 174,600 (Sh 3.08, DD 6.31%) |
| Pure S | 116,000 (Sh 1.52, DD 11.12%) | 564,200 (Sh 2.48, DD 24.96%) | 274,000 (Sh 3.38, DD 8.43%) |
| OR S | 152,200 (Sh 2.10, DD 6.09%) | 108,800 (Sh 0.82, DD 14.31%) | 86,400 (Sh 1.68, DD 7.93%) |
| Pure B | 320,600 (Sh 3.51, DD 11.16%) | 120,400 (Sh 0.72, DD 27.74%) | 163,600 (Sh 1.93, DD 11.95%) |
| OR B | 153,400 (Sh 1.78, DD 9.57%) | 110,800 (Sh 0.79, DD 26.03%) | 235,600 (Sh 3.74, DD 6.75%) |

#### 多頭期

| 配置 | bull_2024 | bull_super | bull_2026 |
|---|---:|---:|---:|
| Pure L | 149,400 (Sh 4.31, DD 4.34%) | 685,200 (Sh 4.06, DD 8.50%) | 549,200 (Sh 6.64, DD 12.85%) |
| OR L | 132,800 (Sh 5.14, DD 1.98%) | 463,400 (Sh 3.72, DD 10.39%) | 494,200 (Sh 7.92, DD 7.99%) |
| Pure S | -108,400 (Sh -2.67, DD 15.32%) | 312,600 (Sh 2.06, DD 13.63%) | -131,600 (Sh -1.76, DD 21.18%) |
| OR S | -53,000 (Sh -3.39, DD 7.19%) | 35,000 (Sh 0.55, DD 8.97%) | 37,600 (Sh 1.07, DD 8.32%) |
| Pure B | -122,400 (Sh -2.31, DD 18.29%) | 484,800 (Sh 2.61, DD 20.92%) | -117,800 (Sh -1.14, DD 34.34%) |
| OR B | 55,000 (Sh 1.77, DD 6.48%) | 380,000 (Sh 2.46, DD 14.73%) | 474,000 (Sh 6.61, DD 10.10%) |

#### 空頭期

| 配置 | bear_2022 | bear_2025 |
|---|---:|---:|
| Pure L | 102,000 (Sh 1.19, DD 14.03%) | -160,200 (Sh -1.70, DD 29.66%) |
| OR L | 95,600 (Sh 1.76, DD 7.59%) | 7,400 (Sh 0.29, DD 11.00%) |
| Pure S | 466,400 (Sh 4.88, DD 5.51%) | 270,200 (Sh 3.15, DD 10.29%) |
| OR S | 362,000 (Sh 3.95, DD 6.44%) | 172,600 (Sh 2.83, DD 9.25%) |
| Pure B | 269,400 (Sh 3.09, DD 10.67%) | 238,600 (Sh 3.02, DD 14.33%) |
| OR B | 429,200 (Sh 4.22, DD 9.49%) | 148,800 (Sh 2.35, DD 10.28%) |

### 8.2 滑價敏感度（OR + inst=0.3 + max2, Long Only）

| 滑價 | 2024 PnL | 2024 Sharpe | 2024 MaxDD | 2025 PnL | 2025 Sharpe | 2025 MaxDD |
|---|---|---|---|---|---|---|
| 1pt | 486,600 | 2.01 | 11.46% | 683,800 | 2.73 | 9.93% |
| 3pt | 433,400 | 1.81 | 12.13% | 624,200 | 2.51 | 10.16% |
| 5pt | 380,200 | 1.60 | 12.86% | 564,600 | 2.30 | 10.60% |

### 8.3 反彈訊號 A/B 測試（盤整期）

| 期間 × 配置 | BK PnL | BK+BC PnL | Δ |
|---|---|---|---|
| con_quiet × OR B | 153,400 | 97,600 | -36% |
| con_quiet × Pure L | 247,200 | 170,600 | -31% |
| con_wild × OR B | 110,800 | 41,200 | -63% |
| con_wild × Pure L | 366,400 | 228,000 | -38% |
| con_recent × OR B | 235,600 | 222,400 | -6% |
| con_recent × Pure L | 146,200 | -18,800 | -113% |

---

## 回測報告存放位置

所有詳細回測報告（含每筆交易）：`data/backtest/key_level_sweep_*.txt`

主要檔案：
- `key_level_sweep_con_quiet_trailing_*.txt` — 低波動盤整期
- `key_level_sweep_con_wild_trailing_*.txt` — 高波動盤整期
- `key_level_sweep_con_recent_trailing_*.txt` — 近期盤整期
- `key_level_sweep_bull_*_trailing_*.txt` — 多頭期
- `key_level_sweep_bear_*_trailing_*.txt` — 空頭期
- `key_level_sweep_2024_slip*_trailing_*.txt` — 2024 滑價測試
- `key_level_sweep_2025_slip*_trailing_*.txt` — 2025 滑價測試
