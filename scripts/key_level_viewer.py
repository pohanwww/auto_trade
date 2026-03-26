#!/usr/bin/env python3
"""Key Level Viewer — 互動式 Key Level 圖表瀏覽器

啟動:
    cd <project_root>
    uv run python scripts/key_level_viewer.py

訪問: http://localhost:8090

功能:
    - 選擇 timeframe (1m, 5m, 15m, 30m, 1h)
    - 選擇目標日期
    - 生成 Key Level 蠟燭圖 PNG
    - 瀏覽/刪除已生成的 PNG
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import matplotlib
matplotlib.use("Agg")

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

PNG_DIR = Path(__file__).resolve().parent.parent / "data" / "key_level_charts"
PNG_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get("KL_VIEWER_PORT", "8090"))

app = FastAPI(title="Key Level Viewer")

# Lazy-init API client (expensive, only create once)
_api_lock = threading.Lock()
_api_client = None
_market_service = None


def _get_market_service():
    global _api_client, _market_service
    if _market_service is not None:
        return _market_service
    with _api_lock:
        if _market_service is not None:
            return _market_service
        from auto_trade.core.client import create_api_client
        from auto_trade.core.config import Config
        from auto_trade.services.market_service import MarketService

        config = Config()
        _api_client = create_api_client(
            config.api_key, config.secret_key,
            config.ca_cert_path, config.ca_password,
            simulation=True,
        )
        _market_service = MarketService(_api_client)
        return _market_service


# ──────────────────────────────────────────────
# Reuse logic from visualize_key_levels_real.py
# ──────────────────────────────────────────────

DAY_START = time(8, 45)
DAY_END = time(13, 45)
NIGHT_START = time(15, 0)
NIGHT_BOUNDARY = time(5, 0)


def _split_sessions(kbar_list, target_date):
    from auto_trade.models.market import KBar
    today = target_date.date()
    prev_day, prev_night, today_day = [], [], []

    for kbar in kbar_list.kbars:
        d = kbar.time.date()
        t = kbar.time.time()
        if d == today and DAY_START <= t < DAY_END:
            today_day.append(kbar)
        elif DAY_START <= t < DAY_END and d < today:
            prev_day.append(kbar)
        elif t >= NIGHT_START and d < today:
            prev_night.append(kbar)
        elif t < NIGHT_BOUNDARY:
            ns_date = d - timedelta(days=1)
            if ns_date < today:
                prev_night.append(kbar)

    if prev_day:
        latest = max(k.time.date() for k in prev_day)
        prev_day = [k for k in prev_day if k.time.date() == latest]
    if prev_night:
        dates = set()
        for k in prev_night:
            t = k.time.time()
            dates.add(k.time.date() if t >= NIGHT_START else k.time.date() - timedelta(days=1))
        if dates:
            latest_n = max(dates)
            prev_night = [k for k in prev_night if (
                k.time.date() if k.time.time() >= NIGHT_START
                else k.time.date() - timedelta(days=1)
            ) == latest_n]

    prev_day.sort(key=lambda k: k.time)
    prev_night.sort(key=lambda k: k.time)
    today_day.sort(key=lambda k: k.time)
    return prev_day, prev_night, today_day


def _compute_ohlc(kbars):
    if not kbars:
        return {"open": 0, "high": 0, "low": 0, "close": 0}
    return {
        "open": int(kbars[0].open),
        "high": int(max(k.high for k in kbars)),
        "low": int(min(k.low for k in kbars)),
        "close": int(kbars[-1].close),
    }


def _generate_chart(target_date_str: str, timeframe: str, symbol: str = "MXF",
                    sub_symbol: str = "MXFR1") -> dict:
    """Fetch data, compute levels, generate PNG. Returns status dict."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    from matplotlib.patches import FancyBboxPatch
    from matplotlib.lines import Line2D
    from auto_trade.services.key_level_detector import (
        SessionData, find_confluence_levels, KeyLevel,
    )

    ms = _get_market_service()
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    end_date = target_date + timedelta(days=1)
    start_date = target_date - timedelta(days=5)

    kbar_list = ms.get_futures_kbars_by_date_range(
        symbol=symbol, sub_symbol=sub_symbol,
        start_date=start_date, end_date=end_date,
        timeframe=timeframe,
    )
    if len(kbar_list) == 0:
        return {"ok": False, "error": "No data fetched"}

    prev_day, prev_night, today_kbars = _split_sessions(kbar_list, target_date)
    pd_ohlc = _compute_ohlc(prev_day)
    pn_ohlc = _compute_ohlc(prev_night)
    today_open = int(today_kbars[0].open) if today_kbars else None

    session = SessionData(
        prev_day_high=pd_ohlc["high"], prev_day_low=pd_ohlc["low"],
        prev_day_close=pd_ohlc["close"],
        prev_night_high=pn_ohlc["high"] if pn_ohlc["high"] else None,
        prev_night_low=pn_ohlc["low"] if pn_ohlc["low"] else None,
        prev_night_close=pn_ohlc["close"] if pn_ohlc["close"] else None,
        today_open=today_open,
        or_range=max(pd_ohlc["high"] - pd_ohlc["low"], 50),
        prev_day_kbars=prev_day, prev_night_kbars=prev_night,
    )

    levels = find_confluence_levels(
        session, swing_period=10, cluster_tolerance=50,
        volume_bucket_size=10, zone_tolerance=50,
        round_scan_range=500, touch_weight=1.0,
    )

    all_kbars = prev_day + prev_night + today_kbars
    if not all_kbars:
        return {"ok": False, "error": "No bars to plot"}

    # Build DataFrame
    rows = [{"Date": k.time, "Open": k.open, "High": k.high,
             "Low": k.low, "Close": k.close, "Volume": k.volume} for k in all_kbars]
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)

    dates = mdates.date2num(df.index.to_pydatetime())
    width = 0.002

    fig, (ax_c, ax_v) = plt.subplots(
        2, 1, figsize=(24, 14), height_ratios=[4, 1],
        gridspec_kw={"hspace": 0.05},
    )

    for dt, row in zip(dates, df.itertuples()):
        o, h, lo, c = row.Open, row.High, row.Low, row.Close
        color = "#26A69A" if c >= o else "#EF5350"
        ax_c.plot([dt, dt], [lo, h], color=color, linewidth=0.8)
        body_lo, body_hi = min(o, c), max(o, c)
        rect = FancyBboxPatch(
            (dt - width / 2, body_lo), width, max(body_hi - body_lo, 0.3),
            boxstyle="round,pad=0.0005", facecolor=color, edgecolor=color, linewidth=0.5,
        )
        ax_c.add_patch(rect)

    # Session backgrounds
    for label, kbars, bg in [("Prev Day", prev_day, "#E0E0E0"),
                              ("Prev Night", prev_night, "#E8E0F0"),
                              ("Today", today_kbars, "#E0F0E0")]:
        if not kbars:
            continue
        t0 = mdates.date2num(kbars[0].time)
        t1 = mdates.date2num(kbars[-1].time)
        ax_c.axvspan(t0, t1, alpha=0.12, color=bg, zorder=0)
        ax_c.text((t0 + t1) / 2, ax_c.get_ylim()[1] if ax_c.get_ylim()[1] > 0 else 0,
                  label, ha="center", va="top", fontsize=9, color="#666", fontweight="bold")

    # Key level lines
    xmin, xmax = dates[0], dates[-1]
    for kl in levels:
        score = kl.score
        clr = "#FF4444" if score >= 15 else "#FF8800" if score >= 10 else "#4488FF" if score >= 5 else "#AAAAAA"
        alpha = min(0.3 + score / 15, 0.9)
        ls_start = mdates.date2num(kl.first_seen) if kl.first_seen else xmin
        ls_start = max(ls_start, xmin)
        ax_c.hlines(kl.price, ls_start, xmax, colors=clr, linewidth=1.0,
                     alpha=alpha, linestyles="-" if score >= 5 else "--")
        src_short = ", ".join(kl.sources[:4])
        if len(kl.sources) > 4:
            src_short += "..."
        ax_c.text(xmax + 0.003, kl.price,
                  f" {kl.price}  [s={kl.score:.1f}, {kl.touch_count}t]\n {src_short}",
                  fontsize=6.5, color=clr, va="center",
                  fontweight="bold" if score >= 5 else "normal")

    ax_c.set_ylabel("Price", fontsize=11)
    ax_c.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_c.tick_params(labelbottom=False)
    ax_c.set_title(
        f"Key Level — {symbol} {timeframe} — {target_date_str}",
        fontsize=14, fontweight="bold",
    )

    all_prices = [k.high for k in all_kbars] + [k.low for k in all_kbars]
    level_prices = [kl.price for kl in levels]
    combined = all_prices + level_prices
    ax_c.set_ylim(min(combined) - 30, max(combined) + 30)
    ax_c.set_xlim(xmin - 0.01, xmax + 0.08)

    legend_els = [
        Line2D([0], [0], color="#FF4444", lw=2, label="score ≥ 15"),
        Line2D([0], [0], color="#FF8800", lw=1.5, label="score ≥ 10"),
        Line2D([0], [0], color="#4488FF", lw=1, label="score ≥ 5"),
        Line2D([0], [0], color="#AAAAAA", lw=1, linestyle="--", label="score < 5"),
    ]
    ax_c.legend(handles=legend_els, loc="upper left", fontsize=8)

    vol_colors = ["#26A69A" if r.Close >= r.Open else "#EF5350" for r in df.itertuples()]
    ax_v.bar(dates, df["Volume"], width=width, color=vol_colors, alpha=0.7)
    ax_v.set_ylabel("Volume", fontsize=11)
    ax_v.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_v.set_xlim(xmin - 0.01, xmax + 0.08)
    plt.xticks(rotation=30)

    plt.tight_layout()
    fname = f"kl_{symbol}_{timeframe}_{target_date_str}.png"
    out_path = PNG_DIR / fname
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close()

    levels_data = [{"price": kl.price, "score": kl.score,
                    "touches": kl.touch_count, "sources": kl.sources}
                   for kl in levels]

    return {"ok": True, "filename": fname, "levels": levels_data,
            "bars": len(all_kbars)}


# ──────────────────────────────────────────────
# API Routes
# ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return _build_html()


@app.get("/api/generate")
async def api_generate(
    date: str = Query(..., description="YYYY-MM-DD"),
    timeframe: str = Query("5m"),
    symbol: str = Query("MXF"),
    sub_symbol: str = Query("MXFR1"),
):
    try:
        result = _generate_chart(date, timeframe, symbol, sub_symbol)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/list")
async def api_list():
    files = sorted(PNG_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return JSONResponse([
        {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
         "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
        for f in files
    ])


@app.get("/api/delete")
async def api_delete(name: str = Query(...)):
    p = PNG_DIR / name
    if ".." in name or "/" in name:
        return JSONResponse({"ok": False, "error": "Invalid name"})
    if p.exists():
        p.unlink()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Not found"})


@app.get("/charts/{name}")
async def serve_chart(name: str):
    p = PNG_DIR / name
    if ".." in name or not p.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(str(p), media_type="image/png")


# ──────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────

def _build_html() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Key Level Viewer</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background:#0d1117; color:#c9d1d9; }}
  .header {{ background:#161b22; padding:16px 24px; border-bottom:1px solid #30363d;
             display:flex; align-items:center; gap:16px; flex-wrap:wrap; }}
  .header h1 {{ font-size:20px; color:#58a6ff; white-space:nowrap; }}
  .controls {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  .controls label {{ font-size:13px; color:#8b949e; }}
  .controls select, .controls input {{
    background:#0d1117; color:#c9d1d9; border:1px solid #30363d;
    border-radius:6px; padding:6px 10px; font-size:14px; }}
  .btn {{ background:#238636; color:#fff; border:none; border-radius:6px;
          padding:8px 18px; font-size:14px; cursor:pointer; font-weight:600; }}
  .btn:hover {{ background:#2ea043; }}
  .btn:disabled {{ background:#21262d; color:#484f58; cursor:not-allowed; }}
  .btn-danger {{ background:#da3633; }}
  .btn-danger:hover {{ background:#f85149; }}
  .btn-sm {{ padding:4px 10px; font-size:12px; }}
  .main {{ display:flex; height:calc(100vh - 60px); }}
  .sidebar {{ width:280px; min-width:280px; background:#161b22;
              border-right:1px solid #30363d; overflow-y:auto; padding:12px; }}
  .sidebar h3 {{ font-size:14px; color:#8b949e; margin-bottom:8px; }}
  .file-item {{ display:flex; justify-content:space-between; align-items:center;
                padding:8px; border-radius:6px; cursor:pointer; margin-bottom:4px;
                border:1px solid transparent; }}
  .file-item:hover {{ background:#21262d; }}
  .file-item.active {{ background:#1f6feb22; border-color:#1f6feb; }}
  .file-name {{ font-size:13px; word-break:break-all; }}
  .file-meta {{ font-size:11px; color:#484f58; }}
  .content {{ flex:1; overflow:auto; display:flex; align-items:center;
              justify-content:center; padding:16px; }}
  .content img {{ max-width:100%; max-height:100%; border-radius:8px;
                  box-shadow:0 4px 20px rgba(0,0,0,0.4); }}
  .placeholder {{ color:#484f58; font-size:16px; text-align:center; }}
  .status {{ position:fixed; bottom:16px; right:16px; background:#161b22;
             border:1px solid #30363d; border-radius:8px; padding:12px 16px;
             font-size:13px; display:none; z-index:100; }}
  .status.show {{ display:block; }}
  .levels-table {{ margin-top:8px; font-size:12px; width:100%; }}
  .levels-table th {{ text-align:left; color:#8b949e; padding:4px; border-bottom:1px solid #30363d; }}
  .levels-table td {{ padding:4px; border-bottom:1px solid #21262d; }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 Key Level Viewer</h1>
  <div class="controls">
    <label>Date</label>
    <input type="date" id="inp-date" value="{today}">
    <label>Timeframe</label>
    <select id="inp-tf">
      <option value="1m">1m</option>
      <option value="5m" selected>5m</option>
      <option value="15m">15m</option>
      <option value="30m">30m</option>
      <option value="1h">1h</option>
    </select>
    <label>Symbol</label>
    <select id="inp-sym">
      <option value="MXF">MXF (小台)</option>
      <option value="TXF">TXF (大台)</option>
    </select>
    <button class="btn" id="btn-gen" onclick="generate()">Generate</button>
  </div>
</div>

<div class="main">
  <div class="sidebar">
    <h3>Generated Charts</h3>
    <div id="file-list"></div>
  </div>
  <div class="content" id="content">
    <div class="placeholder">Select a chart or generate a new one</div>
  </div>
</div>

<div class="status" id="status"></div>

<script>
const SUB_MAP = {{"MXF":"MXFR1", "TXF":"TXFR1"}};
let activeFile = null;

async function loadList() {{
  const res = await fetch('/api/list');
  const files = await res.json();
  const el = document.getElementById('file-list');
  if (!files.length) {{ el.innerHTML = '<div class="file-meta">No charts yet</div>'; return; }}
  el.innerHTML = files.map(f => `
    <div class="file-item ${{f.name === activeFile ? 'active' : ''}}"
         onclick="selectFile('${{f.name}}')">
      <div>
        <div class="file-name">${{f.name}}</div>
        <div class="file-meta">${{f.size_kb}} KB · ${{f.mtime}}</div>
      </div>
      <button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); deleteFile('${{f.name}}')">✕</button>
    </div>
  `).join('');
}}

function selectFile(name) {{
  activeFile = name;
  document.getElementById('content').innerHTML =
    `<img src="/charts/${{name}}?t=${{Date.now()}}" alt="${{name}}">`;
  loadList();
}}

async function deleteFile(name) {{
  if (!confirm('Delete ' + name + '?')) return;
  await fetch('/api/delete?name=' + encodeURIComponent(name));
  if (activeFile === name) {{
    activeFile = null;
    document.getElementById('content').innerHTML =
      '<div class="placeholder">Select a chart or generate a new one</div>';
  }}
  loadList();
}}

async function generate() {{
  const btn = document.getElementById('btn-gen');
  const date = document.getElementById('inp-date').value;
  const tf = document.getElementById('inp-tf').value;
  const sym = document.getElementById('inp-sym').value;
  const subSym = SUB_MAP[sym] || sym + 'R1';
  btn.disabled = true;  btn.textContent = 'Generating...';
  showStatus('⏳ Fetching data & computing levels...');
  try {{
    const res = await fetch(`/api/generate?date=${{date}}&timeframe=${{tf}}&symbol=${{sym}}&sub_symbol=${{subSym}}`);
    const data = await res.json();
    if (data.ok) {{
      showStatus(`✅ Generated: ${{data.filename}} (${{data.bars}} bars, ${{data.levels?.length || 0}} levels)`);
      activeFile = data.filename;
      await loadList();
      selectFile(data.filename);
    }} else {{
      showStatus('❌ ' + (data.error || 'Unknown error'));
    }}
  }} catch(e) {{
    showStatus('❌ ' + e.message);
  }}
  btn.disabled = false;  btn.textContent = 'Generate';
}}

function showStatus(msg) {{
  const el = document.getElementById('status');
  el.textContent = msg;  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 5000);
}}

loadList();
</script>
</body>
</html>"""


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    print(f"🚀 Key Level Viewer starting on http://localhost:{PORT}")
    print(f"📁 PNG directory: {PNG_DIR}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
