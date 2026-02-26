"""Trading Dashboard - 即時倉位狀態面板

啟動: uv run dashboard
訪問: http://<your-ip>:8080

可選環境變數:
  DASHBOARD_TOKEN  - 設定後需在 URL 加上 ?token=xxx 才能訪問
  DASHBOARD_PORT   - 預設 8080
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Trading Dashboard")

STATE_DIR = Path("data/state")
LOGS_DIR = Path("logs")
POINT_VALUE = 50  # MXF: 1 point = NT$50


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _collect_strategies() -> list[dict]:
    """Scan data/state/*/position.json for all strategy data."""
    results = []
    if not STATE_DIR.exists():
        return results

    for strategy_dir in sorted(STATE_DIR.iterdir()):
        if not strategy_dir.is_dir():
            continue

        strategy_name = strategy_dir.name
        data = _read_json(strategy_dir / "position.json")
        if not data:
            continue

        info: dict = {"strategy": strategy_name, "has_position": False}

        # Live metadata written by the engine
        live = data.pop("_live", None)
        if live:
            info["current_price"] = live.get("current_price")
            info["timestamp"] = live.get("timestamp")
            info["strategy_state"] = live.get("strategy_state")

        # Position record (keyed by sub_symbol, e.g. "MXF202603")
        sub_sym = next(iter(data), None)
        if sub_sym and isinstance(data[sub_sym], dict):
            rec = data[sub_sym]
            info["has_position"] = True
            info["sub_symbol"] = sub_sym
            info["direction"] = rec.get("direction")
            info["entry_price"] = rec.get("entry_price")
            info["quantity"] = rec.get("quantity")
            info["stop_loss_price"] = rec.get("stop_loss_price")
            info["highest_price"] = rec.get("highest_price")
            info["trailing_stop_active"] = rec.get("trailing_stop_active", False)
            info["trailing_stop_price"] = rec.get("trailing_stop_price")
            info["entry_time"] = rec.get("entry_time")
            info["legs_info"] = rec.get("legs_info")

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


@app.get("/api/logs")
def api_logs_list(token: str | None = Query(None)):
    """List available log files, newest first."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not LOGS_DIR.exists():
        return []
    files = sorted(
        (
            {"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime}
            for f in LOGS_DIR.iterdir()
            if f.is_file() and f.suffix == ".log"
        ),
        key=lambda x: x["modified"],
        reverse=True,
    )
    return files


@app.get("/api/logs/{filename}")
def api_logs_content(
    filename: str,
    tail: int = Query(500, ge=1, le=10000),
    token: str | None = Query(None),
):
    """Read last N lines of a log file."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    safe_name = Path(filename).name
    log_path = LOGS_DIR / safe_name
    if not log_path.exists() or not log_path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        lines = log_path.read_text(errors="replace").splitlines()
        return {"filename": safe_name, "total_lines": len(lines), "lines": lines[-tail:]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
def index(token: str | None = Query(None)):
    if not _check_token(token):
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)

    token_param = f"&token={token}" if token else ""
    return HTMLResponse(_build_html(token_param))


@app.get("/logs", response_class=HTMLResponse)
def logs_page(token: str | None = Query(None)):
    if not _check_token(token):
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)

    token_param = f"&token={token}" if token else ""
    return HTMLResponse(_build_logs_html(token_param))


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
  nav {{
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }}
  nav a {{
    color: var(--text-muted);
    text-decoration: none;
    font-size: 0.9rem;
    font-weight: 500;
    padding: 4px 12px;
    border-radius: 6px;
    transition: all 0.15s;
  }}
  nav a:hover {{ color: var(--text); background: rgba(255,255,255,0.05); }}
  nav a.active {{ color: var(--blue); background: rgba(88,166,255,0.1); }}
  @media (max-width: 480px) {{
    body {{ padding: 12px; }}
    .grid {{ grid-template-columns: 1fr; }}
    .current-price {{ font-size: 1.6rem; }}
    .details {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
  <nav>
    <a href="/?{token_param.lstrip('&')}" class="active">Positions</a>
    <a href="/logs?{token_param.lstrip('&')}">Logs</a>
  </nav>
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
  const qty = d.quantity || 0;
  const engineOnline = !!d.timestamp;

  let pnlPts = null, pnlPerUnit = null, pnlTotal = null, pnlClass = 'zero';
  if (hasPos && cp && ep) {{
    // When legs exist, sum P&L per leg for accurate total
    const legs = d.legs_info && Object.keys(d.legs_info).length > 0 ? d.legs_info : null;
    if (legs) {{
      pnlTotal = 0;
      let totalPts = 0;
      Object.values(legs).forEach(leg => {{
        const legPts = isLong ? cp - leg.entry_price : leg.entry_price - cp;
        pnlTotal += legPts * POINT_VALUE * leg.quantity;
        totalPts += legPts * leg.quantity;
      }});
      pnlPts = qty > 0 ? Math.round(totalPts / qty) : 0;
      pnlPerUnit = qty > 0 ? Math.round(pnlTotal / qty) : 0;
    }} else {{
      pnlPts = isLong ? cp - ep : ep - cp;
      pnlPerUnit = pnlPts * POINT_VALUE;
      pnlTotal = pnlPts * POINT_VALUE * qty;
    }}
    pnlClass = pnlTotal > 0 ? 'positive' : pnlTotal < 0 ? 'negative' : 'zero';
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
    tsLabel = d.trailing_stop_active
      ? '<span class="ts-label active">TS Active</span>'
      : '<span class="ts-label inactive">TS Inactive</span>';
  }}

  // --- No position: simple display ---
  if (!hasPos) {{
    const engineStatus = engineOnline
      ? `<span style="font-size:0.75rem;color:var(--text-muted)">Updated ${{timeSince(d.timestamp)}}</span>`
      : '';
    let priceRow = cp
      ? `<div class="price-row"><div class="current-price">${{fmt(cp)}}</div><div class="pnl zero">No Position</div></div>`
      : '';

    // Strategy pending state (e.g. ORB key prices)
    let stateSection = '';
    const ss = d.strategy_state;
    if (ss && ss.or_high) {{
      const longLabel = ss.long_state === 'IDLE'
        ? '<span style="color:var(--text-muted)">Waiting</span>'
        : `<span style="color:var(--yellow)">${{ss.long_state}}</span>`;
      const shortLabel = ss.short_state === 'IDLE'
        ? '<span style="color:var(--text-muted)">Waiting</span>'
        : `<span style="color:var(--yellow)">${{ss.short_state}}</span>`;

      let barHtml = '';
      if (cp && ss.or_high && ss.or_low) {{
        const prices = [ss.or_low, ss.or_mid, ss.or_high, cp].filter(p => p != null);
        const lo = Math.min(...prices) - 30;
        const hi = Math.max(...prices) + 30;
        const range = hi - lo || 1;
        const pct = (v) => ((v - lo) / range * 100).toFixed(1);
        const cpAbove = cp > ss.or_high;
        const cpBelow = cp < ss.or_low;
        const cpColor = cpAbove ? 'var(--green)' : cpBelow ? 'var(--red)' : 'var(--text-primary)';
        barHtml = `
          <div style="margin-top:12px;">
            <div style="position:relative;height:6px;background:rgba(48,54,61,0.6);border-radius:3px;margin:8px 0 20px;">
              <div style="position:absolute;left:${{pct(ss.or_low)}}%;width:${{(pct(ss.or_high) - pct(ss.or_low))}}%;height:100%;background:rgba(255,255,255,0.08);border-radius:3px;"></div>
              <div style="position:absolute;left:${{pct(ss.or_low)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--red);" title="OR Low: ${{ss.or_low}}"></div>
              <div style="position:absolute;left:${{pct(ss.or_mid)}}%;top:-2px;width:2px;height:10px;border-radius:2px;background:var(--text-muted);" title="OR Mid: ${{ss.or_mid}}"></div>
              <div style="position:absolute;left:${{pct(ss.or_high)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--green);" title="OR High: ${{ss.or_high}}"></div>
              <div style="position:absolute;left:${{pct(cp)}}%;top:-4px;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid ${{cpColor}};transform:translateX(-5px);" title="Current: ${{cp}}"></div>
              <span style="position:absolute;left:${{pct(ss.or_low)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--red);white-space:nowrap;">${{fmt(ss.or_low)}}</span>
              <span style="position:absolute;left:${{pct(ss.or_mid)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--text-muted);white-space:nowrap;">${{fmt(ss.or_mid)}}</span>
              <span style="position:absolute;left:${{pct(ss.or_high)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--green);white-space:nowrap;">${{fmt(ss.or_high)}}</span>
              <span style="position:absolute;left:${{pct(cp)}}%;top:-18px;transform:translateX(-50%);font-size:0.68rem;color:${{cpColor}};white-space:nowrap;font-weight:600;">${{fmt(cp)}}</span>
            </div>
          </div>`;
      }}

      stateSection = `
        <div style="margin-top:12px;background:rgba(48,54,61,0.3);border-radius:8px;padding:12px;">
          <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;font-weight:600;">Opening Range</div>
          <div class="details" style="margin:0;">
            <div class="detail-item"><span class="label">OR High</span><span class="value green">${{fmt(ss.or_high)}}</span></div>
            <div class="detail-item"><span class="label">OR Low</span><span class="value red">${{fmt(ss.or_low)}}</span></div>
            <div class="detail-item"><span class="label">OR Mid</span><span class="value">${{fmt(ss.or_mid)}}</span></div>
            <div class="detail-item"><span class="label">OR Range</span><span class="value">${{fmt(ss.or_range)}} pts</span></div>
            <div class="detail-item"><span class="label">Long</span><span class="value">${{longLabel}}</span></div>
            <div class="detail-item"><span class="label">Short</span><span class="value">${{shortLabel}}</span></div>
          </div>
          ${{barHtml}}
        </div>`;
    }}

    return `<div class="card"><div class="card-header"><span class="name">${{d.strategy}}</span>${{badge}}</div>${{priceRow}}${{stateSection}}<div style="margin-top:10px;text-align:right;">${{engineStatus}}</div></div>`;
  }}

  // --- Has position ---
  const sl = d.stop_loss_price;
  const tsp = d.trailing_stop_price;
  const tp = d.take_profit_price;
  const effectiveStop = (tsp && sl) ? (isLong ? Math.max(tsp, sl) : Math.min(tsp, sl)) : (tsp || sl);

  // P&L hero section
  const pnlHero = `
    <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px;">
      <div>
        <div style="color:var(--text-muted);font-size:0.78rem;margin-bottom:2px;">Current Price</div>
        <div class="current-price">${{fmt(cp || ep)}}</div>
      </div>
      <div style="text-align:right;">
        <div style="color:var(--text-muted);font-size:0.78rem;margin-bottom:2px;">Unrealized P&L</div>
        <div class="pnl ${{pnlClass}}" style="font-size:1.6rem;">
          ${{pnlPts != null ? (pnlPts >= 0 ? '+' : '') + fmt(pnlPts) + ' pts' : '—'}}
        </div>
        <div class="pnl ${{pnlClass}}" style="font-size:1.1rem;">
          ${{pnlTotal != null ? (pnlTotal >= 0 ? '+' : '') + 'NT$' + fmt(pnlTotal) + ' (' + qty + ' lots)' : ''}}
        </div>
      </div>
    </div>`;

  // Details grid
  const details = `
    <div class="details">
      <div class="detail-item"><span class="label">Contract</span><span class="value">${{d.sub_symbol || '—'}}</span></div>
      <div class="detail-item"><span class="label">Quantity</span><span class="value">${{qty}} lots</span></div>
      <div class="detail-item"><span class="label">Entry</span><span class="value blue">${{fmt(ep)}}</span></div>
      <div class="detail-item"><span class="label">Held</span><span class="value">${{duration(d.entry_time)}}</span></div>
      <div class="detail-item"><span class="label">Hard Stop</span><span class="value red">${{fmt(sl)}}</span></div>
      <div class="detail-item"><span class="label">Trailing Stop</span><span class="value orange">${{tsp ? fmt(tsp) + (ep ? ' (' + (isLong ? (tsp >= ep ? '+' : '') + fmt(tsp - ep) : (ep >= tsp ? '+' : '') + fmt(ep - tsp)) + ' pts)' : '') : (d.trailing_stop_active ? 'Active (syncing...)' : 'Inactive')}}</span></div>
      <div class="detail-item"><span class="label">Effective Stop</span><span class="value red">${{fmt(effectiveStop)}}</span></div>
      <div class="detail-item"><span class="label">Highest</span><span class="value green">${{fmt(d.highest_price)}}</span></div>
      <div class="detail-item"><span class="label">Per Lot P&L</span><span class="value ${{pnlClass === 'positive' ? 'green' : pnlClass === 'negative' ? 'red' : ''}}">${{pnlPerUnit != null ? (pnlPerUnit >= 0 ? '+' : '') + 'NT$' + fmt(pnlPerUnit) : '—'}}</span></div>
    </div>`;

  // Legs detail (for addon positions)
  let legsSection = '';
  if (d.legs_info && Object.keys(d.legs_info).length > 1) {{
    let rows = '';
    const entries = Object.entries(d.legs_info);
    entries.forEach(([legId, info]) => {{
      const legEp = info.entry_price;
      const legQty = info.quantity;
      const legType = info.leg_type || 'TS';
      const isAddon = legId.includes('-A');
      let legPnl = '—';
      let legPnlClass = '';
      if (cp && legEp) {{
        const pts = isLong ? cp - legEp : legEp - cp;
        const twd = pts * POINT_VALUE * legQty;
        legPnlClass = pts > 0 ? 'green' : pts < 0 ? 'red' : '';
        legPnl = (pts >= 0 ? '+' : '') + fmt(pts) + ' pts / ' + (twd >= 0 ? '+' : '') + 'NT$' + fmt(twd);
      }}
      const label = isAddon ? '加碼' : '底倉';
      const tagColor = isAddon ? 'var(--yellow)' : 'var(--blue)';
      rows += `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid rgba(48,54,61,0.5);font-size:0.85rem;">
          <div>
            <span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:0.72rem;font-weight:600;background:rgba(255,255,255,0.06);color:${{tagColor}};margin-right:6px;">${{label}}</span>
            <span style="color:var(--text-muted)">${{legType}} x${{legQty}}</span>
            <span style="margin-left:8px;">@ ${{fmt(legEp)}}</span>
          </div>
          <div class="value ${{legPnlClass}}" style="font-variant-numeric:tabular-nums;font-weight:500;">${{legPnl}}</div>
        </div>`;
    }});
    legsSection = `
      <div style="margin-top:14px;background:rgba(48,54,61,0.3);border-radius:8px;padding:12px;">
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:6px;font-weight:600;">Legs Detail</div>
        ${{rows}}
      </div>`;
  }}

  // Stop bar
  let stopBar = '';
  if (ep && (sl || tsp)) {{
    const prices = [sl, ep, cp, tsp, tp].filter(p => p != null);
    const lo = Math.min(...prices) - 20;
    const hi = Math.max(...prices) + 20;
    const range = hi - lo || 1;
    const pct = (v) => ((v - lo) / range * 100).toFixed(1);

    let markers = '';
    let labelsBelow = '';
    let labelsAbove = '';

    if (sl) {{
      markers += `<div style="position:absolute;left:${{pct(sl)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--red);" title="SL: ${{sl}}"></div>`;
      labelsBelow += `<span style="position:absolute;left:${{pct(sl)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--red);white-space:nowrap;">SL ${{fmt(sl)}}</span>`;
    }}
    markers += `<div style="position:absolute;left:${{pct(ep)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--blue);" title="Entry: ${{ep}}"></div>`;
    labelsBelow += `<span style="position:absolute;left:${{pct(ep)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--blue);white-space:nowrap;">${{fmt(ep)}}</span>`;
    if (tsp) {{
      markers += `<div style="position:absolute;left:${{pct(tsp)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--orange);" title="TS: ${{tsp}}"></div>`;
      labelsBelow += `<span style="position:absolute;left:${{pct(tsp)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--orange);white-space:nowrap;">TS ${{fmt(tsp)}}</span>`;
    }}
    if (tp) {{
      markers += `<div style="position:absolute;left:${{pct(tp)}}%;top:-3px;width:3px;height:12px;border-radius:2px;background:var(--green);" title="TP: ${{tp}}"></div>`;
      labelsBelow += `<span style="position:absolute;left:${{pct(tp)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:var(--green);white-space:nowrap;">TP ${{fmt(tp)}}</span>`;
    }}
    if (cp) {{
      const cpCol = pnlPts > 0 ? 'var(--green)' : pnlPts < 0 ? 'var(--red)' : 'var(--text-primary)';
      markers += `<div style="position:absolute;left:${{pct(cp)}}%;top:-4px;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid ${{cpCol}};transform:translateX(-5px);" title="Current: ${{cp}}"></div>`;
      labelsAbove += `<span style="position:absolute;left:${{pct(cp)}}%;top:-18px;transform:translateX(-50%);font-size:0.68rem;color:${{cpCol}};white-space:nowrap;font-weight:600;">${{fmt(cp)}}</span>`;
    }}

    stopBar = `
      <div style="margin-top:16px;background:rgba(48,54,61,0.5);border-radius:6px;padding:12px;">
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;">Price Levels</div>
        <div style="position:relative;height:6px;background:rgba(48,54,61,0.6);border-radius:3px;margin:20px 0 22px;">
          ${{markers}}${{labelsBelow}}${{labelsAbove}}
        </div>
      </div>`;
  }}

  const engineStatus = engineOnline
    ? `<span style="font-size:0.75rem;color:var(--text-muted)">Updated ${{timeSince(d.timestamp)}}</span>`
    : '<span style="font-size:0.75rem;color:var(--yellow)">Engine offline — showing last saved state</span>';

  return `
    <div class="card">
      <div class="card-header">
        <span class="name">${{d.strategy}} ${{tsLabel}}</span>
        ${{badge}}
      </div>
      ${{pnlHero}}
      ${{details}}
      ${{legsSection}}
      ${{stopBar}}
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


def _build_logs_html(token_param: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Logs</title>
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
  nav {{
    display: flex;
    gap: 16px;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }}
  nav a {{
    color: var(--text-muted);
    text-decoration: none;
    font-size: 0.9rem;
    font-weight: 500;
    padding: 4px 12px;
    border-radius: 6px;
    transition: all 0.15s;
  }}
  nav a:hover {{ color: var(--text); background: rgba(255,255,255,0.05); }}
  nav a.active {{ color: var(--blue); background: rgba(88,166,255,0.1); }}
  .toolbar {{
    display: flex;
    gap: 12px;
    align-items: center;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }}
  select, button {{
    background: var(--card-bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 0.88rem;
    cursor: pointer;
    transition: border-color 0.15s;
  }}
  select:hover, button:hover {{ border-color: var(--blue); }}
  select:focus, button:focus {{ outline: none; border-color: var(--blue); }}
  select {{ min-width: 280px; }}
  button.active {{ background: rgba(88,166,255,0.15); border-color: var(--blue); }}
  .file-meta {{
    color: var(--text-muted);
    font-size: 0.8rem;
    margin-left: auto;
  }}
  .log-container {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }}
  .log-content {{
    padding: 16px;
    overflow-x: auto;
    max-height: calc(100vh - 200px);
    overflow-y: auto;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 0.82rem;
    line-height: 1;
    white-space: pre;
    tab-size: 4;
  }}
  .log-content .line {{
    display: block;
    padding: 0 12px;
    border-radius: 3px;
  }}
  .log-content .line:hover {{
    background: rgba(255,255,255,0.03);
  }}
  .log-content .line.error {{
    color: var(--red);
    background: rgba(248,81,73,0.06);
  }}
  .log-content .line.warn {{
    color: var(--yellow);
  }}
  .log-content .line.success {{
    color: var(--green);
  }}
  .log-content .line.info-blue {{
    color: var(--blue);
  }}
  .empty-state {{
    text-align: center;
    padding: 60px 20px;
    color: var(--text-muted);
  }}
  .line-num {{
    display: inline-block;
    width: 50px;
    color: var(--text-muted);
    opacity: 0.4;
    text-align: right;
    margin-right: 16px;
    user-select: none;
    font-size: 0.78rem;
  }}
  @media (max-width: 480px) {{
    body {{ padding: 12px; }}
    .toolbar {{ flex-direction: column; align-items: stretch; }}
    select {{ min-width: unset; }}
    .file-meta {{ margin-left: 0; }}
    .log-content {{ max-height: calc(100vh - 280px); font-size: 0.75rem; }}
    .line-num {{ width: 36px; margin-right: 8px; }}
  }}
</style>
</head>
<body>
  <nav>
    <a href="/?{token_param.lstrip('&')}">Positions</a>
    <a href="/logs?{token_param.lstrip('&')}" class="active">Logs</a>
  </nav>
  <div class="toolbar">
    <select id="fileSelect"><option value="">Select a log file...</option></select>
    <button id="btnTail" class="active" title="Show last 500 lines">Tail 500</button>
    <button id="btnFull" title="Load full file">Full</button>
    <button id="btnRefresh" title="Reload current file">Refresh</button>
    <label style="display:flex;align-items:center;gap:6px;color:var(--text-muted);font-size:0.85rem;">
      <input type="checkbox" id="autoScroll" checked> Auto-scroll
    </label>
    <span class="file-meta" id="fileMeta"></span>
  </div>
  <div class="log-container">
    <div class="log-content" id="logContent">
      <div class="empty-state">Select a log file to view</div>
    </div>
  </div>

<script>
const TOKEN_PARAM = "{token_param}";
let currentFile = '';
let tailMode = true;

function fmtSize(bytes) {{
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}}

function classifyLine(text) {{
  if (/\\u274c|error|failed|exception|traceback/i.test(text)) return 'error';
  if (/\\u26a0|warn|warning/i.test(text)) return 'warn';
  if (/\\u2705|success|\\u1f4c8|filled|open/i.test(text)) return 'success';
  if (/\\u1f680|\\u1f527|\\u1f4cb|started|config/i.test(text)) return 'info-blue';
  return '';
}}

async function loadFileList() {{
  const res = await fetch('/api/logs?_t=' + Date.now() + TOKEN_PARAM);
  const files = await res.json();
  const sel = document.getElementById('fileSelect');
  sel.innerHTML = '<option value="">Select a log file...</option>';
  files.forEach(f => {{
    const opt = document.createElement('option');
    opt.value = f.name;
    const d = new Date(f.modified * 1000);
    opt.textContent = f.name + '  (' + fmtSize(f.size) + ', ' + d.toLocaleDateString() + ')';
    sel.appendChild(opt);
  }});
  if (files.length > 0 && !currentFile) {{
    sel.value = files[0].name;
    currentFile = files[0].name;
    loadFile();
  }}
}}

async function loadFile() {{
  if (!currentFile) return;
  const tail = tailMode ? 500 : 10000;
  const res = await fetch('/api/logs/' + encodeURIComponent(currentFile) + '?tail=' + tail + '&_t=' + Date.now() + TOKEN_PARAM);
  if (!res.ok) return;
  const data = await res.json();

  const meta = document.getElementById('fileMeta');
  meta.textContent = data.total_lines + ' total lines' + (tailMode ? ' (showing last 500)' : '');

  const container = document.getElementById('logContent');
  const startLine = data.total_lines - data.lines.length + 1;
  container.innerHTML = data.lines.map((line, i) => {{
    const cls = classifyLine(line);
    const num = startLine + i;
    return '<span class="line' + (cls ? ' ' + cls : '') + '"><span class="line-num">' + num + '</span>' + escapeHtml(line) + '</span>';
  }}).join('\\n');

  if (document.getElementById('autoScroll').checked) {{
    container.scrollTop = container.scrollHeight;
  }}
}}

function escapeHtml(text) {{
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

document.getElementById('fileSelect').addEventListener('change', (e) => {{
  currentFile = e.target.value;
  if (currentFile) loadFile();
}});

document.getElementById('btnTail').addEventListener('click', () => {{
  tailMode = true;
  document.getElementById('btnTail').classList.add('active');
  document.getElementById('btnFull').classList.remove('active');
  loadFile();
}});

document.getElementById('btnFull').addEventListener('click', () => {{
  tailMode = false;
  document.getElementById('btnFull').classList.add('active');
  document.getElementById('btnTail').classList.remove('active');
  loadFile();
}});

document.getElementById('btnRefresh').addEventListener('click', () => loadFile());

loadFileList();
</script>
</body>
</html>"""


def main():
    import uvicorn

    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    print(f"🖥️  Dashboard starting on http://0.0.0.0:{port}")
    if os.environ.get("DASHBOARD_TOKEN"):
        print("🔒 Token auth enabled — append ?token=<your-token> to the URL")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
