"""Trading Dashboard - 即時倉位狀態面板

啟動: uv run dashboard
訪問: http://<your-ip>:8080

可選環境變數:
  DASHBOARD_TOKEN  - 設定後需在 URL 加上 ?token=xxx 才能訪問
  DASHBOARD_PORT   - 預設 8080
"""

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Trading Dashboard")

STATE_DIR = Path("data/state")
POINT_VALUE = 50  # MXF: 1 point = NT$50


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _collect_strategies() -> list[dict]:
    """Scan data/state/*/ for status.json and position.json"""
    results = []
    if not STATE_DIR.exists():
        return results

    for strategy_dir in sorted(STATE_DIR.iterdir()):
        if not strategy_dir.is_dir():
            continue

        strategy_name = strategy_dir.name
        status = _read_json(strategy_dir / "status.json")
        position = _read_json(strategy_dir / "position.json")

        if not status and not position:
            continue

        # Merge: status.json is authoritative for live data, position.json for stored record
        info: dict = {"strategy": strategy_name, "has_position": False}

        if status:
            info.update(status)
        elif position:
            # Fallback: only position.json exists (engine not running or no status yet)
            sub_sym = next(iter(position), None)
            if sub_sym and isinstance(position[sub_sym], dict):
                rec = position[sub_sym]
                info["has_position"] = True
                info["sub_symbol"] = sub_sym
                info["direction"] = rec.get("direction")
                info["entry_price"] = rec.get("entry_price")
                info["quantity"] = rec.get("quantity")
                info["stop_loss_price"] = rec.get("stop_loss_price")
                info["highest_price"] = rec.get("highest_price")
                info["trailing_stop_active"] = rec.get("trailing_stop_active", False)
                info["entry_time"] = rec.get("entry_time")
            else:
                info["has_position"] = False

        results.append(info)
    return results


def _check_token(token: str | None) -> bool:
    expected = os.environ.get("DASHBOARD_TOKEN")
    if not expected:
        return True
    return token == expected


@app.get("/api/status")
def api_status(token: str | None = Query(None)):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _collect_strategies()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, token: str | None = Query(None)):
    if not _check_token(token):
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)

    token_param = f"&token={token}" if token else ""
    return HTMLResponse(_build_html(token_param))


def _build_html(token_param: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Dashboard</title>
<style>
  :root {{
    --bg: #0d1117;
    --card-bg: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-muted: #8b949e;
    --green: #3fb950;
    --red: #f85149;
    --blue: #58a6ff;
    --yellow: #d29922;
    --orange: #db6d28;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 20px;
    min-height: 100vh;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .header h1 {{
    font-size: 1.5rem;
    font-weight: 600;
  }}
  .header .meta {{
    color: var(--text-muted);
    font-size: 0.85rem;
    text-align: right;
  }}
  .header .meta .live {{
    display: inline-block;
    width: 8px; height: 8px;
    background: var(--green);
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 2s infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
    gap: 20px;
  }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    transition: border-color 0.2s;
  }}
  .card:hover {{ border-color: var(--blue); }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }}
  .card-header .name {{
    font-size: 1.1rem;
    font-weight: 600;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .badge.long {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .badge.short {{ background: rgba(248,81,73,0.15); color: var(--red); }}
  .badge.flat {{ background: rgba(139,148,158,0.15); color: var(--text-muted); }}
  .badge.offline {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .price-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 12px;
  }}
  .current-price {{
    font-size: 2rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}
  .pnl {{
    font-size: 1.3rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }}
  .pnl.positive {{ color: var(--green); }}
  .pnl.negative {{ color: var(--red); }}
  .pnl.zero {{ color: var(--text-muted); }}
  .details {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px 16px;
  }}
  .detail-item {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid rgba(48,54,61,0.5);
    font-size: 0.88rem;
  }}
  .detail-item .label {{ color: var(--text-muted); }}
  .detail-item .value {{ font-weight: 500; font-variant-numeric: tabular-nums; }}
  .detail-item .value.green {{ color: var(--green); }}
  .detail-item .value.red {{ color: var(--red); }}
  .detail-item .value.orange {{ color: var(--orange); }}
  .detail-item .value.blue {{ color: var(--blue); }}
  .stop-bar {{
    margin-top: 16px;
    background: rgba(48,54,61,0.5);
    border-radius: 6px;
    padding: 12px;
  }}
  .stop-bar .bar-title {{
    font-size: 0.8rem;
    color: var(--text-muted);
    margin-bottom: 8px;
  }}
  .bar-track {{
    position: relative;
    height: 8px;
    background: #21262d;
    border-radius: 4px;
    overflow: visible;
  }}
  .bar-marker {{
    position: absolute;
    top: -4px;
    width: 16px; height: 16px;
    border-radius: 50%;
    transform: translateX(-50%);
    z-index: 1;
  }}
  .bar-marker.sl {{ background: var(--red); }}
  .bar-marker.entry {{ background: var(--blue); }}
  .bar-marker.current {{ background: var(--text); }}
  .bar-marker.ts {{ background: var(--orange); }}
  .bar-marker.tp {{ background: var(--green); }}
  .bar-labels {{
    display: flex;
    justify-content: space-between;
    margin-top: 8px;
    font-size: 0.72rem;
    color: var(--text-muted);
  }}
  .empty-state {{
    text-align: center;
    padding: 60px 20px;
    color: var(--text-muted);
  }}
  .empty-state h2 {{ margin-bottom: 8px; font-weight: 500; }}
  .ts-label {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.75rem;
    padding: 2px 6px;
    border-radius: 4px;
  }}
  .ts-label.active {{ background: rgba(219,109,40,0.15); color: var(--orange); }}
  .ts-label.inactive {{ background: rgba(139,148,158,0.1); color: var(--text-muted); }}
  @media (max-width: 480px) {{
    body {{ padding: 12px; }}
    .grid {{ grid-template-columns: 1fr; }}
    .current-price {{ font-size: 1.6rem; }}
    .details {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
  <div class="header">
    <h1>Trading Dashboard</h1>
    <div class="meta">
      <span class="live"></span>
      <span id="lastUpdate">Loading...</span>
    </div>
  </div>
  <div id="app" class="grid"></div>

<script>
const REFRESH_MS = 3000;
const POINT_VALUE = {POINT_VALUE};
const TOKEN_PARAM = "{token_param}";

function fmt(n) {{
  if (n == null) return '—';
  return Number(n).toLocaleString();
}}

function duration(isoStr) {{
  if (!isoStr) return '—';
  const ms = Date.now() - new Date(isoStr).getTime();
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h > 24) {{
    const d = Math.floor(h / 24);
    return d + 'd ' + (h % 24) + 'h';
  }}
  return h + 'h ' + m + 'm';
}}

function timeSince(isoStr) {{
  if (!isoStr) return '';
  const s = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (s < 10) return 'just now';
  if (s < 60) return s + 's ago';
  if (s < 300) return Math.floor(s/60) + 'm ago';
  return '> 5m ago (engine may be offline)';
}}

function buildCard(d) {{
  const isLong = d.direction === 'Buy';
  const hasPos = d.has_position;
  const cp = d.current_price;
  const ep = d.entry_price;
  const engineOnline = !!d.timestamp;

  let pnlPts = null, pnlAmt = null, pnlClass = 'zero';
  if (hasPos && cp && ep) {{
    pnlPts = isLong ? cp - ep : ep - cp;
    pnlAmt = pnlPts * POINT_VALUE * (d.quantity || 1);
    pnlClass = pnlPts > 0 ? 'positive' : pnlPts < 0 ? 'negative' : 'zero';
  }}

  let badge = '';
  if (!engineOnline && !hasPos) {{
    badge = '<span class="badge offline">Offline</span>';
  }} else if (!hasPos) {{
    badge = '<span class="badge flat">Flat</span>';
  }} else if (isLong) {{
    badge = '<span class="badge long">Long</span>';
  }} else {{
    badge = '<span class="badge short">Short</span>';
  }}

  let tsLabel = '';
  if (hasPos) {{
    if (d.trailing_stop_active) {{
      tsLabel = '<span class="ts-label active">TS Active</span>';
    }} else {{
      tsLabel = '<span class="ts-label inactive">TS Inactive</span>';
    }}
  }}

  let priceRow = '';
  if (hasPos) {{
    priceRow = `
      <div class="price-row">
        <div class="current-price">${{fmt(cp || ep)}}</div>
        <div>
          <div class="pnl ${{pnlClass}}">
            ${{pnlPts != null ? (pnlPts >= 0 ? '+' : '') + fmt(pnlPts) + ' pts' : '—'}}
          </div>
          <div class="pnl ${{pnlClass}}" style="font-size:0.9rem;">
            ${{pnlAmt != null ? (pnlAmt >= 0 ? '+' : '') + 'NT$' + fmt(pnlAmt) : ''}}
          </div>
        </div>
      </div>`;
  }} else if (cp) {{
    priceRow = `<div class="price-row"><div class="current-price">${{fmt(cp)}}</div><div class="pnl zero">No Position</div></div>`;
  }}

  let details = '';
  if (hasPos) {{
    const sl = d.stop_loss_price;
    const tsp = d.trailing_stop_price;
    const tp = d.take_profit_price;
    const effectiveStop = (tsp && sl) ? (isLong ? Math.max(tsp, sl) : Math.min(tsp, sl)) : (tsp || sl);

    details = `
      <div class="details">
        <div class="detail-item"><span class="label">Contract</span><span class="value">${{d.sub_symbol || '—'}}</span></div>
        <div class="detail-item"><span class="label">Quantity</span><span class="value">${{d.quantity || '—'}}</span></div>
        <div class="detail-item"><span class="label">Entry</span><span class="value blue">${{fmt(ep)}}</span></div>
        <div class="detail-item"><span class="label">Held</span><span class="value">${{duration(d.entry_time)}}</span></div>
        <div class="detail-item"><span class="label">Stop Loss</span><span class="value red">${{fmt(sl)}}</span></div>
        <div class="detail-item"><span class="label">Trailing Stop</span><span class="value orange">${{fmt(tsp) || '—'}}</span></div>
        <div class="detail-item"><span class="label">Effective Stop</span><span class="value red">${{fmt(effectiveStop)}}</span></div>
        <div class="detail-item"><span class="label">Highest</span><span class="value green">${{fmt(d.highest_price)}}</span></div>
      </div>`;

    // Stop bar visualization
    if (ep && (sl || tsp)) {{
      const prices = [sl, ep, cp, tsp, tp].filter(p => p != null);
      const lo = Math.min(...prices) - 20;
      const hi = Math.max(...prices) + 20;
      const range = hi - lo || 1;
      const pct = (v) => ((v - lo) / range * 100).toFixed(1);

      let markers = '';
      if (sl) markers += `<div class="bar-marker sl" style="left:${{pct(sl)}}%" title="SL: ${{sl}}"></div>`;
      markers += `<div class="bar-marker entry" style="left:${{pct(ep)}}%" title="Entry: ${{ep}}"></div>`;
      if (tsp) markers += `<div class="bar-marker ts" style="left:${{pct(tsp)}}%" title="TS: ${{tsp}}"></div>`;
      if (cp) markers += `<div class="bar-marker current" style="left:${{pct(cp)}}%" title="Current: ${{cp}}"></div>`;
      if (tp) markers += `<div class="bar-marker tp" style="left:${{pct(tp)}}%" title="TP: ${{tp}}"></div>`;

      let labels = '<span>';
      if (sl) labels += `<span style="color:var(--red)">SL ${{fmt(sl)}}</span>`;
      labels += '</span><span>';
      labels += `<span style="color:var(--blue)">Entry ${{fmt(ep)}}</span>`;
      if (tsp) labels += ` | <span style="color:var(--orange)">TS ${{fmt(tsp)}}</span>`;
      if (cp) labels += ` | Current ${{fmt(cp)}}`;
      labels += '</span>';

      details += `
        <div class="stop-bar" style="grid-column: 1 / -1;">
          <div class="bar-title">Price Levels</div>
          <div class="bar-track">${{markers}}</div>
          <div class="bar-labels">${{labels}}</div>
        </div>`;
    }}
  }}

  const engineStatus = engineOnline
    ? `<span style="font-size:0.75rem;color:var(--text-muted)">Updated ${{timeSince(d.timestamp)}}</span>`
    : (hasPos ? '<span style="font-size:0.75rem;color:var(--yellow)">Engine offline — showing last saved state</span>' : '');

  return `
    <div class="card">
      <div class="card-header">
        <span class="name">${{d.strategy}} ${{tsLabel}}</span>
        ${{badge}}
      </div>
      ${{priceRow}}
      ${{details}}
      <div style="margin-top:10px;text-align:right;">${{engineStatus}}</div>
    </div>`;
}}

async function refresh() {{
  try {{
    const res = await fetch('/api/status?_t=' + Date.now() + TOKEN_PARAM);
    if (!res.ok) return;
    const data = await res.json();
    const app = document.getElementById('app');

    if (!data.length) {{
      app.innerHTML = '<div class="empty-state"><h2>No strategies found</h2><p>Start a trading engine to see data here.</p></div>';
    }} else {{
      app.innerHTML = data.map(buildCard).join('');
    }}

    document.getElementById('lastUpdate').textContent =
      new Date().toLocaleTimeString();
  }} catch(e) {{
    console.error('Refresh failed:', e);
  }}
}}

refresh();
setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>"""


def main():
    import uvicorn

    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    print(f"🖥️  Dashboard starting on http://0.0.0.0:{port}")
    if os.environ.get("DASHBOARD_TOKEN"):
        print(f"🔒 Token auth enabled — append ?token=<your-token> to the URL")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
