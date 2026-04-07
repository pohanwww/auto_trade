"""Trading Dashboard - 即時倉位狀態面板

啟動: uv run dashboard
訪問: http://<your-ip>:8080

可選環境變數:
  DASHBOARD_TOKEN  - 設定後需在 URL 加上 ?token=xxx 才能訪問
  DASHBOARD_PORT   - 預設 8080
"""

import json
import os
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

load_dotenv(override=True)

app = FastAPI(title="Trading Dashboard")

STATE_DIR = Path("data/state")
LOGS_DIR = Path("logs")
POINT_VALUE_MXF = 50  # MXF: 1 point = NT$50
POINT_VALUE_TXF = 200  # TXF: 1 point = NT$200

# ── Key Level Viewer ─────────────────────────────────────

PNG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "key_level_charts"
PNG_DIR.mkdir(parents=True, exist_ok=True)

_kl_api_lock = threading.Lock()
_kl_api_client = None
_kl_market_service = None

def _get_kl_market_service():
    global _kl_api_client, _kl_market_service
    if _kl_market_service is not None:
        return _kl_market_service
    with _kl_api_lock:
        if _kl_market_service is not None:
            return _kl_market_service
        from auto_trade.core.client import create_api_client
        from auto_trade.core.config import Config
        from auto_trade.services.market_service import MarketService

        config = Config()
        _kl_api_client = create_api_client(
            config.api_key,
            config.secret_key,
            config.ca_cert_path,
            config.ca_password,
            simulation=True,
        )
        _kl_market_service = MarketService(_kl_api_client)
        return _kl_market_service


OR_BARS = 3


def _generate_chart(
    target_date_str: str,
    timeframe: str,
    symbol: str = "MXF",
    sub_symbol: str = "MXFR1",
    session: str = "day",
    lookback: int = 1,
) -> dict:
    """Fetch data, compute levels, generate PNG.

    Key level calculation matches key_level_strategy.py exactly:
    - OHLC from latest prev session only
    - Swing/volume kbars aggregated from N recent sessions (N based on tf)
    - or_range from today's first OR_BARS bars (not prev_day range)
    - max_levels=20
    - today's kbars shown on chart but NOT used in calculation
    """
    import matplotlib.pyplot as plt
    import pandas as pd
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch

    from auto_trade.services.key_level_detector import (
        calculate_key_levels_from_kbars,
        split_sessions,
    )

    ms = _get_kl_market_service()
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d")

    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
    tf_min = tf_minutes.get(timeframe, 5)
    session_lookback = max(lookback, tf_min // 5)
    lookback_days = max(5, session_lookback * 3 + 2)

    # Primary: use tick-cache (same data source as live strategy)
    # days=30 to fetch ALL cached data — split_sessions + session_lookback
    # will select the correct sessions.  Using a smaller value risks cutting
    # sessions mid-way because the cutoff is based on datetime.now(), not
    # the target_date.
    kbar_list = None
    try:
        cache_key = (symbol, sub_symbol)
        if cache_key not in ms._symbol_cache:
            ms.subscribe_symbol(symbol, sub_symbol, init_days=30)
        kbar_list = ms.get_futures_kbars_with_timeframe(
            symbol, sub_symbol, timeframe, days=30,
        )
    except Exception as e:
        print(f"⚠️  KL chart: tick-cache unavailable ({e}), using API fallback")

    # Fallback: direct API fetch
    if kbar_list is None or len(kbar_list) == 0:
        start_date = target_date - timedelta(days=lookback_days)
        end_date = target_date + timedelta(days=2)
        kbar_list = ms.get_futures_kbars_by_date_range(
            symbol=symbol,
            sub_symbol=sub_symbol,
            start_date=start_date,
            end_date=end_date,
            timeframe=timeframe,
        )

    if len(kbar_list) == 0:
        return {"ok": False, "error": "No data fetched"}

    in_night = session == "night"
    signal_level_count = 7

    # --- OR calculation (before KL calc, same as strategy) ---
    # Need today_session_kbars for OR, so run split_sessions first
    _, _, today_kbars, today_night_kbars = split_sessions(
        kbar_list.kbars, target_date.date(), in_night_session=in_night,
    )
    today_session_kbars = today_night_kbars if in_night else today_kbars

    or_high: int | None = None
    or_low: int | None = None
    or_mid: int | None = None
    or_range = 1
    or_kbars_for_chart: list = []
    if today_session_kbars and len(today_session_kbars) >= OR_BARS:
        or_kbars = today_session_kbars[:OR_BARS]
        or_high = int(max(k.high for k in or_kbars))
        or_low = int(min(k.low for k in or_kbars))
        or_mid = (or_high + or_low) // 2
        or_range = max(or_high - or_low, 1)
        or_kbars_for_chart = or_kbars

    # --- Shared KL calculation (identical to strategy) ---
    kl = calculate_key_levels_from_kbars(
        kbar_list.kbars,
        target_date.date(),
        in_night_session=in_night,
        or_range=or_range,
        session_lookback=session_lookback,
        signal_level_count=signal_level_count,
    )

    levels = kl.levels
    day_ohlc = kl.day_ohlc
    night_ohlc = kl.night_ohlc
    today_open = kl.today_open
    day_sessions = kl.day_sessions
    night_sessions = kl.night_sessions
    today_kbars = kl.today_day_kbars
    today_night_kbars = kl.today_night_kbars
    agg_day_kbars = kl.agg_day_kbars
    agg_night_kbars = kl.agg_night_kbars

    signal_levels = set(lv.price for lv in levels[:signal_level_count])

    # Build chart kbars — always sorted chronologically
    if in_night:
        all_kbars = agg_night_kbars + agg_day_kbars + today_night_kbars
    elif session == "day":
        all_kbars = agg_day_kbars + agg_night_kbars + today_kbars
    else:
        all_kbars = agg_day_kbars + agg_night_kbars + today_kbars + today_night_kbars
    all_kbars = sorted(all_kbars, key=lambda k: k.time)
    if not all_kbars:
        return {"ok": False, "error": "No bars to plot"}

    rows = [
        {
            "Date": k.time,
            "Open": float(k.open),
            "High": float(k.high),
            "Low": float(k.low),
            "Close": float(k.close),
            "Volume": float(k.volume) if k.volume else 0.0,
        }
        for k in all_kbars
    ]
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)

    n_bars = len(df)
    xs = list(range(n_bars))
    bar_times = list(df.index)
    time_to_idx = {t: i for i, t in enumerate(bar_times)}
    width = 0.6

    fig = None
    try:
        fig, (ax_c, ax_v) = plt.subplots(
            2,
            1,
            figsize=(24, 14),
            height_ratios=[4, 1],
            gridspec_kw={"hspace": 0.05},
        )

        for i, row in enumerate(df.itertuples()):
            o, h, lo, c = row.Open, row.High, row.Low, row.Close
            color = "#26A69A" if c >= o else "#EF5350"
            ax_c.plot([i, i], [lo, h], color=color, linewidth=0.8)
            body_lo, body_hi = min(o, c), max(o, c)
            rect = FancyBboxPatch(
                (i - width / 2, body_lo),
                width,
                max(body_hi - body_lo, 0.3),
                boxstyle="round,pad=0.02",
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
            )
            ax_c.add_patch(rect)

        def _kbar_idx_range(kbars):
            if not kbars:
                return None, None
            first_t = kbars[0].time
            last_t = kbars[-1].time
            i0 = time_to_idx.get(first_t)
            i1 = time_to_idx.get(last_t)
            if i0 is None or i1 is None:
                return None, None
            return i0, i1

        if session_lookback > 1:
            # Multi-session: just mark history vs today
            if session == "night":
                history_kbars = sorted(agg_night_kbars + agg_day_kbars, key=lambda k: k.time)
                session_spans = [
                    ("History", history_kbars, "#E0E0E0"),
                    ("Tonight", today_night_kbars, "#F0E8E0"),
                ]
            elif session == "day":
                history_kbars = sorted(agg_day_kbars + agg_night_kbars, key=lambda k: k.time)
                session_spans = [
                    ("History", history_kbars, "#E0E0E0"),
                    ("Today", today_kbars, "#E0F0E0"),
                ]
            else:
                history_kbars = sorted(agg_day_kbars + agg_night_kbars, key=lambda k: k.time)
                session_spans = [
                    ("History", history_kbars, "#E0E0E0"),
                    ("Today", today_kbars, "#E0F0E0"),
                    ("Tonight", today_night_kbars, "#F0E8E0"),
                ]
        elif session == "night":
            session_spans = [
                ("Prev Night", agg_night_kbars, "#E8E0F0"),
                ("Prev Day", agg_day_kbars, "#E0E0E0"),
                ("Tonight", today_night_kbars, "#F0E8E0"),
            ]
        elif session == "day":
            session_spans = [
                ("Prev Day", agg_day_kbars, "#E0E0E0"),
                ("Prev Night", agg_night_kbars, "#E8E0F0"),
                ("Today", today_kbars, "#E0F0E0"),
            ]
        else:
            session_spans = [
                ("Prev Day", agg_day_kbars, "#E0E0E0"),
                ("Prev Night", agg_night_kbars, "#E8E0F0"),
                ("Today", today_kbars, "#E0F0E0"),
                ("Tonight", today_night_kbars, "#F0E8E0"),
            ]
        for label, kbars, bg in session_spans:
            i0, i1 = _kbar_idx_range(kbars)
            if i0 is None:
                continue
            ax_c.axvspan(i0 - 0.5, i1 + 0.5, alpha=0.12, color=bg, zorder=0)
            ax_c.text(
                (i0 + i1) / 2,
                ax_c.get_ylim()[1] if ax_c.get_ylim()[1] > 0 else 0,
                label,
                ha="center",
                va="top",
                fontsize=9,
                color="#666",
                fontweight="bold",
            )

        xmin_idx, xmax_idx = 0, n_bars - 1
        for kl in levels:
            is_signal = kl.price in signal_levels
            clr = "#FF8800" if is_signal else "#AAAAAA"
            lw = 1.0
            alpha = 0.85 if is_signal else 0.6
            ls_start = xmin_idx
            if kl.first_seen:
                for idx_t, bt in enumerate(bar_times):
                    if bt >= kl.first_seen:
                        ls_start = idx_t
                        break
            ax_c.hlines(
                kl.price,
                ls_start,
                xmax_idx,
                colors=clr,
                linewidth=lw,
                alpha=alpha,
                linestyles="-" if is_signal else "--",
            )
            src_short = ", ".join(kl.sources[:4])
            if len(kl.sources) > 4:
                src_short += "..."
            role = "SIG" if is_signal else "TRAIL"
            ax_c.text(
                xmax_idx + 0.5,
                kl.price,
                f" {kl.price}  [{role} s={kl.score:.1f}, {kl.touch_count}t]\n {src_short}",
                fontsize=6.5,
                color=clr,
                va="center",
                fontweight="bold" if is_signal else "normal",
            )

        # --- OR range visualization ---
        if or_high is not None and or_low is not None and or_kbars_for_chart:
            or_start_t = or_kbars_for_chart[0].time
            or_start_idx = time_to_idx.get(or_start_t, 0)
            or_end_idx = xmax_idx
            ax_c.fill_between(
                [or_start_idx - 0.5, or_end_idx + 0.5],
                or_low,
                or_high,
                alpha=0.08,
                color="#42A5F5",
                zorder=0,
            )
            or_lines = [
                (or_high, "#26A69A", "OR High"),
                (or_low, "#EF5350", "OR Low"),
            ]
            for price, clr, lbl in or_lines:
                ax_c.hlines(
                    price,
                    or_start_idx,
                    or_end_idx,
                    colors=clr,
                    linewidth=1.2,
                    alpha=0.9,
                    linestyles="--",
                    zorder=2,
                )
                ax_c.text(
                    or_start_idx - 0.8,
                    price,
                    f"{lbl} {price}",
                    fontsize=7,
                    color=clr,
                    va="center",
                    ha="right",
                    fontweight="bold",
                )
            if or_mid is not None:
                ax_c.hlines(
                    or_mid,
                    or_start_idx,
                    or_end_idx,
                    colors="#78909C",
                    linewidth=0.8,
                    alpha=0.6,
                    linestyles=":",
                    zorder=2,
                )
                ax_c.text(
                    or_start_idx - 0.8,
                    or_mid,
                    f"OR Mid {or_mid}",
                    fontsize=6.5,
                    color="#78909C",
                    va="center",
                    ha="right",
                )

        ax_c.set_ylabel("Price", fontsize=11)
        tick_step = max(1, n_bars // 20)
        tick_positions = list(range(0, n_bars, tick_step))
        tick_labels = [bar_times[i].strftime("%m/%d %H:%M") for i in tick_positions]
        ax_c.set_xticks(tick_positions)
        ax_c.set_xticklabels([])
        ax_c.tick_params(labelbottom=False)
        session_label = {"day": "Day", "night": "Night", "both": "Day+Night"}.get(
            session, "Day"
        )
        lb_tag = f" | lookback={session_lookback}" if session_lookback > 1 else ""
        ax_c.set_title(
            f"Key Level — {symbol} {timeframe} — {target_date_str} ({session_label}{lb_tag})",
            fontsize=14,
            fontweight="bold",
        )

        all_prices = [k.high for k in all_kbars] + [k.low for k in all_kbars]
        level_prices = [kl.price for kl in levels]
        or_prices = [p for p in [or_high, or_low] if p is not None]
        combined = all_prices + level_prices + or_prices
        ax_c.set_ylim(min(combined) - 30, max(combined) + 30)
        ax_c.set_xlim(-1, n_bars + n_bars * 0.08)

        legend_els = [
            Line2D(
                [0],
                [0],
                color="#FF8800",
                lw=1,
                label=f"Signal Level (top {signal_level_count})",
            ),
            Line2D(
                [0], [0], color="#AAAAAA", lw=1, linestyle="--", label="Trailing Level"
            ),
        ]
        if or_high is not None:
            legend_els.append(
                Line2D(
                    [0],
                    [0],
                    color="#42A5F5",
                    lw=1.2,
                    linestyle="--",
                    label=f"OR Range ({or_range} pts)",
                )
            )
        ax_c.legend(handles=legend_els, loc="upper left", fontsize=8)

        vol_colors = [
            "#26A69A" if r.Close >= r.Open else "#EF5350" for r in df.itertuples()
        ]
        ax_v.bar(xs, df["Volume"], width=width, color=vol_colors, alpha=0.7)
        ax_v.set_ylabel("Volume", fontsize=11)
        ax_v.set_xticks(tick_positions)
        ax_v.set_xticklabels(tick_labels, rotation=30, fontsize=8)
        ax_v.set_xlim(-1, n_bars + n_bars * 0.08)

        plt.tight_layout()
        sess_tag = f"_{session}" if session != "day" else ""
        lb_tag = f"_lb{session_lookback}" if session_lookback > 1 else ""
        fname = f"kl_{symbol}_{timeframe}_{target_date_str}{sess_tag}{lb_tag}.png"
        out_path = PNG_DIR / fname
        plt.savefig(str(out_path), dpi=150, bbox_inches="tight")

        levels_data = [
            {
                "price": kl.price,
                "score": kl.score,
                "touches": kl.touch_count,
                "sources": kl.sources,
            }
            for kl in levels
        ]

        return {
            "ok": True,
            "filename": fname,
            "levels": levels_data,
            "bars": len(all_kbars),
        }
    finally:
        if fig is not None:
            plt.close(fig)


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
            info["config_file"] = live.get("config_file")

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
            info["take_profit_price"] = rec.get("take_profit_price")
            info["start_trailing_stop_price"] = rec.get("start_trailing_stop_price")
            info["entry_time"] = rec.get("entry_time")
            info["legs_info"] = rec.get("legs_info")
            info["position_metadata"] = rec.get("position_metadata")

        results.append(info)
    return results


def _check_token(token: str | None) -> bool:
    expected = os.environ.get("DASHBOARD_TOKEN")
    if not expected:
        return True
    return token == expected


PROJECT_DIR = os.environ.get(
    "PROJECT_DIR", str(Path(__file__).resolve().parent.parent.parent)
)


def _engine_process_pattern(config_file: str) -> str:
    """Build pgrep pattern matching start_trading.sh convention."""
    return f"uv run main.*--config {config_file}"


def _is_engine_running(config_file: str) -> bool:
    pattern = _engine_process_pattern(config_file)
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


@app.get("/api/status")
def api_status(token: str | None = Query(None)):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    strategies = _collect_strategies()
    for s in strategies:
        cf = s.get("config_file")
        s["engine_running"] = _is_engine_running(cf) if cf else False
    return strategies


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
        return {
            "filename": safe_name,
            "total_lines": len(lines),
            "lines": lines[-tail:],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/logs/{filename}")
def api_logs_delete(filename: str, token: str | None = Query(None)):
    """Delete a log file."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    safe_name = Path(filename).name
    log_path = LOGS_DIR / safe_name
    if not log_path.exists() or not log_path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        log_path.unlink()
        return {"deleted": safe_name}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Strategy Control Endpoints ──────────────────────────────


def _get_config_file(strategy: str) -> str | None:
    """Read config_file from position.json _live metadata."""
    data = _read_json(STATE_DIR / strategy / "position.json")
    live = data.get("_live")
    return live.get("config_file") if live else None


@app.post("/api/strategy/{strategy}/stop")
def api_strategy_stop(strategy: str, token: str | None = Query(None)):
    """Stop a running engine process (SIGTERM)."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    config_file = _get_config_file(strategy)
    if not config_file:
        return JSONResponse(
            {"error": "config_file not found in _live metadata"}, status_code=400
        )

    if not _is_engine_running(config_file):
        return {"ok": False, "message": "Engine is not running"}

    pattern = _engine_process_pattern(config_file)
    subprocess.run(["pkill", "-TERM", "-f", pattern])
    return {"ok": True, "message": f"Sent SIGTERM to {strategy}"}


@app.post("/api/strategy/{strategy}/start")
def api_strategy_start(strategy: str, token: str | None = Query(None)):
    """Start an engine process via start_trading.sh."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    config_file = _get_config_file(strategy)
    if not config_file:
        return JSONResponse(
            {"error": "config_file not found in _live metadata"}, status_code=400
        )

    if _is_engine_running(config_file):
        return {"ok": False, "message": "Engine is already running"}

    script = Path(PROJECT_DIR) / "start_trading.sh"
    if not script.exists():
        return JSONResponse({"error": "start_trading.sh not found"}, status_code=500)

    subprocess.Popen(
        [str(script), config_file],
        cwd=PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": f"Starting {strategy}"}


@app.post("/api/strategy/{strategy}/clear-position")
def api_clear_position(strategy: str, token: str | None = Query(None)):
    """Clear position record from position.json, preserving _live metadata."""
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    path = STATE_DIR / strategy / "position.json"
    if not path.exists():
        return {"ok": False, "message": "No position.json found"}

    try:
        data = json.loads(path.read_text())
        live = data.get("_live")
        new_data = {"_live": live} if live else {}
        path.write_text(json.dumps(new_data, indent=2, ensure_ascii=False))
        return {"ok": True, "message": "Position cleared"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Key Level API Endpoints ──────────────────────────────


_kl_gen_lock = threading.Lock()


@app.get("/api/kl/generate")
def api_kl_generate(
    date: str = Query(..., description="YYYY-MM-DD"),
    timeframe: str = Query("5m"),
    symbol: str = Query("MXF"),
    sub_symbol: str = Query("MXFR1"),
    session: str = Query("day", description="day / night / both"),
    lookback: int = Query(1, ge=1, le=10, description="KL lookback sessions (1=default)"),
    token: str | None = Query(None),
):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _kl_gen_lock.acquire(blocking=False):
        return JSONResponse(
            {"ok": False, "error": "Another chart is being generated, please wait"}
        )
    try:
        result = _generate_chart(date, timeframe, symbol, sub_symbol, session=session, lookback=lookback)
        return JSONResponse(result)
    except Exception as e:
        import traceback

        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)})
    finally:
        _kl_gen_lock.release()


@app.get("/api/kl/list")
def api_kl_list(token: str | None = Query(None)):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    files = sorted(PNG_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return JSONResponse(
        [
            {
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
            for f in files
        ]
    )


@app.get("/api/kl/delete")
def api_kl_delete(name: str = Query(...), token: str | None = Query(None)):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    p = PNG_DIR / name
    if ".." in name or "/" in name:
        return JSONResponse({"ok": False, "error": "Invalid name"})
    if p.exists():
        p.unlink()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Not found"})


@app.get("/charts/{name}")
def serve_chart(name: str, token: str | None = Query(None)):
    if not _check_token(token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    p = PNG_DIR / name
    if ".." in name or not p.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(str(p), media_type="image/png")


# ── Page Routes ──────────────────────────────────────────


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


@app.get("/key-levels", response_class=HTMLResponse)
def key_levels_page(token: str | None = Query(None)):
    if not _check_token(token):
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)

    token_param = f"&token={token}" if token else ""
    return HTMLResponse(_build_key_levels_html(token_param))


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
  .ctrl-row {{
    display: flex;
    gap: 8px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
  }}
  .ctrl-btn {{
    flex: 1;
    padding: 7px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    background: transparent;
    font-family: inherit;
  }}
  .ctrl-btn:hover {{ filter: brightness(1.2); }}
  .ctrl-btn:active {{ transform: scale(0.97); }}
  .ctrl-btn:disabled {{ opacity: 0.4; cursor: not-allowed; filter: none; }}
  .ctrl-btn.stop {{ color: var(--red); border-color: rgba(248,81,73,0.3); }}
  .ctrl-btn.stop:hover {{ background: rgba(248,81,73,0.1); }}
  .ctrl-btn.start {{ color: var(--green); border-color: rgba(63,185,80,0.3); }}
  .ctrl-btn.start:hover {{ background: rgba(63,185,80,0.1); }}
  .ctrl-btn.clear {{ color: var(--yellow); border-color: rgba(210,153,34,0.3); }}
  .ctrl-btn.clear:hover {{ background: rgba(210,153,34,0.1); }}
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
    <a href="/?{token_param.lstrip("&")}" class="active">Positions</a>
    <a href="/logs?{token_param.lstrip("&")}">Logs</a>
    <a href="/key-levels?{token_param.lstrip("&")}">Key Levels</a>
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
function getPointValue(subSymbol) {{
  if (subSymbol && subSymbol.startsWith('MXF')) return {POINT_VALUE_MXF};
  return {POINT_VALUE_TXF};
}}
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

async function engineAction(strategy, action, btn) {{
  const labels = {{
    stop: ['Stopping...', `確定要停止 ${{strategy}} 嗎？`],
    start: ['Starting...', `確定要啟動 ${{strategy}} 嗎？`],
    'clear-position': ['Clearing...', `確定要清除 ${{strategy}} 的倉位紀錄嗎？\n\n⚠️ 這只會清除紀錄，不會實際平倉！`],
  }};
  const [busyText, confirmMsg] = labels[action];
  if (!confirm(confirmMsg)) return;

  btn.disabled = true;
  btn.textContent = busyText;
  try {{
    const res = await fetch(`/api/strategy/${{strategy}}/${{action}}?_t=${{Date.now()}}${{TOKEN_PARAM}}`, {{method: 'POST'}});
    const data = await res.json();
    if (!data.ok && data.error) alert(data.error);
  }} catch (e) {{
    alert('Request failed: ' + e.message);
  }}
  setTimeout(refresh, 1500);
}}

function buildCtrlRow(d) {{
  const s = d.strategy;
  const running = d.engine_running;
  let html = '<div class="ctrl-row">';
  if (running) {{
    html += `<button class="ctrl-btn stop" onclick="engineAction('${{s}}','stop',this)">Stop Engine</button>`;
  }} else {{
    html += `<button class="ctrl-btn start" onclick="engineAction('${{s}}','start',this)">Start Engine</button>`;
  }}
  if (d.has_position) {{
    html += `<button class="ctrl-btn clear" onclick="engineAction('${{s}}','clear-position',this)">Clear Position</button>`;
  }}
  html += '</div>';
  return html;
}}

function buildCard(d) {{
  const isLong = d.direction === 'Buy';
  const hasPos = d.has_position;
  const cp = d.current_price;
  const ep = d.entry_price;
  const qty = d.quantity || 0;
  const engineOnline = !!d.timestamp;

  const PV = getPointValue(d.sub_symbol);
  let pnlPts = null, pnlPerUnit = null, pnlTotal = null, pnlClass = 'zero';
  if (hasPos && cp && ep) {{
    const legs = d.legs_info && Object.keys(d.legs_info).length > 0 ? d.legs_info : null;
    if (legs) {{
      pnlTotal = 0;
      let totalPts = 0;
      Object.values(legs).forEach(leg => {{
        const legPts = isLong ? cp - leg.entry_price : leg.entry_price - cp;
        pnlTotal += legPts * PV * leg.quantity;
        totalPts += legPts * leg.quantity;
      }});
      pnlPts = qty > 0 ? Math.round(totalPts / qty) : 0;
      pnlPerUnit = qty > 0 ? Math.round(pnlTotal / qty) : 0;
    }} else {{
      pnlPts = isLong ? cp - ep : ep - cp;
      pnlPerUnit = pnlPts * PV;
      pnlTotal = pnlPts * PV * qty;
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

    return `<div class="card"><div class="card-header"><span class="name">${{d.strategy}}</span>${{badge}}</div>${{priceRow}}${{stateSection}}<div style="margin-top:10px;text-align:right;">${{engineStatus}}</div>${{buildCtrlRow(d)}}</div>`;
  }}

  // --- Has position ---
  const sl = d.stop_loss_price;
  const tsp = d.trailing_stop_price;
  const tp = d.take_profit_price;

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
        const twd = pts * PV * legQty;
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
    const ss = d.strategy_state;
    const orH = ss && ss.or_high ? ss.or_high : null;
    const orL = ss && ss.or_low ? ss.or_low : null;
    const prices = [sl, ep, cp, tsp, tp, orH, orL].filter(p => p != null);
    const lo = Math.min(...prices) - 20;
    const hi = Math.max(...prices) + 20;
    const range = hi - lo || 1;
    const pct = (v) => ((v - lo) / range * 100).toFixed(1);

    let markers = '';
    let labelsBelow = '';
    let labelsAbove = '';

    if (orL && orH) {{
      markers += `<div style="position:absolute;left:${{pct(orL)}}%;width:${{(pct(orH) - pct(orL))}}%;height:100%;background:rgba(255,200,50,0.1);border-radius:3px;"></div>`;
      markers += `<div style="position:absolute;left:${{pct(orL)}}%;top:-3px;width:2px;height:12px;border-radius:2px;background:var(--yellow);opacity:0.6;" title="OR Low: ${{orL}}"></div>`;
      markers += `<div style="position:absolute;left:${{pct(orH)}}%;top:-3px;width:2px;height:12px;border-radius:2px;background:var(--yellow);opacity:0.6;" title="OR High: ${{orH}}"></div>`;
      labelsAbove += `<span style="position:absolute;left:${{pct(orL)}}%;top:-18px;transform:translateX(-50%);font-size:0.62rem;color:var(--yellow);white-space:nowrap;opacity:0.7;">L ${{fmt(orL)}}</span>`;
      labelsAbove += `<span style="position:absolute;left:${{pct(orH)}}%;top:-18px;transform:translateX(-50%);font-size:0.62rem;color:var(--yellow);white-space:nowrap;opacity:0.7;">H ${{fmt(orH)}}</span>`;
    }}
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
      labelsBelow += `<span style="position:absolute;left:${{pct(cp)}}%;top:10px;transform:translateX(-50%);font-size:0.68rem;color:${{cpCol}};white-space:nowrap;font-weight:600;">Now ${{fmt(cp)}}</span>`;
    }}

    stopBar = `
      <div style="margin-top:16px;background:rgba(48,54,61,0.5);border-radius:6px;padding:12px;">
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;">Price Levels</div>
        <div style="position:relative;height:6px;background:rgba(48,54,61,0.6);border-radius:3px;margin:20px 0 22px;">
          ${{markers}}${{labelsBelow}}${{labelsAbove}}
        </div>
      </div>`;
  }}

  // Key levels section (ORB key_levels for TS/TP)
  let keyLevelsSection = '';
  const pm = d.position_metadata;
  if (pm && pm.key_levels && pm.key_levels.length > 0) {{
    const nextIdx = pm.next_key_level_idx || 0;
    const buf = pm.key_level_buffer || 0;
    const levels = pm.key_levels;
    const rows = levels.map((lv, i) => {{
      const broken = i < nextIdx;
      const isNext = i === nextIdx;
      const stopAt = isLong ? lv - buf : lv + buf;
      const icon = broken ? '✅' : isNext ? '👉' : '⬜';
      const color = broken ? 'var(--green)' : isNext ? 'var(--yellow)' : 'var(--text-muted)';
      const stopInfo = broken ? ` → stop ${{fmt(stopAt)}}` : '';
      return `<div style="display:flex;justify-content:space-between;padding:3px 0;color:${{color}};font-size:0.82rem;">` +
        `<span>${{icon}} Level ${{i+1}}: ${{fmt(lv)}}</span>` +
        `<span style="color:var(--text-muted)">${{stopInfo}}</span></div>`;
    }}).join('');
    const tpPts = pm.override_take_profit_points;
    const tsPts = pm.override_trailing_stop_points;
    const tsStartPts = pm.override_start_trailing_stop_points;
    let exitInfo = '';
    if (tpPts || tsPts || tsStartPts) {{
      const parts = [];
      if (tpPts) parts.push(`TP=${{tpPts}}pts`);
      if (tsStartPts) parts.push(`TS start=${{tsStartPts}}pts`);
      if (tsPts) parts.push(`TS dist=${{tsPts}}pts`);
      exitInfo = `<div style="margin-top:6px;font-size:0.75rem;color:var(--text-muted);">${{parts.join(' | ')}}</div>`;
    }}
    keyLevelsSection = `
      <div style="margin-top:12px;background:rgba(48,54,61,0.3);border-radius:8px;padding:12px;">
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:8px;font-weight:600;">Key Levels (${{nextIdx}}/${{levels.length}} broken)</div>
        ${{rows}}
        ${{exitInfo}}
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
      ${{keyLevelsSection}}
      <div style="margin-top:10px;text-align:right;">${{engineStatus}}</div>
      ${{buildCtrlRow(d)}}
    </div>`;
}}

let refreshing = false;
async function refresh() {{
  if (refreshing) return;
  refreshing = true;
  try {{
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    const res = await fetch('/api/status?_t=' + Date.now() + TOKEN_PARAM, {{signal: controller.signal}});
    clearTimeout(timeoutId);
    if (!res.ok) {{
      document.getElementById('lastUpdate').textContent = 'API error ' + res.status + ' — retrying...';
      return;
    }}
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
    document.getElementById('lastUpdate').textContent =
      'Update failed — retrying... (' + e.message + ')';
  }} finally {{
    refreshing = false;
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
    <a href="/?{token_param.lstrip("&")}">Positions</a>
    <a href="/logs?{token_param.lstrip("&")}" class="active">Logs</a>
    <a href="/key-levels?{token_param.lstrip("&")}">Key Levels</a>
  </nav>
  <div class="toolbar">
    <select id="fileSelect"><option value="">Select a log file...</option></select>
    <button id="btnTail" class="active" title="Show last 500 lines">Tail 500</button>
    <button id="btnFull" title="Load full file">Full</button>
    <button id="btnRefresh" title="Reload current file">Refresh</button>
    <button id="btnDelete" title="Delete current log file" style="color:var(--red);border-color:rgba(248,81,73,0.3);">Delete</button>
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

document.getElementById('btnDelete').addEventListener('click', async () => {{
  if (!currentFile) return;
  if (!confirm('Delete ' + currentFile + '?')) return;
  const res = await fetch('/api/logs/' + encodeURIComponent(currentFile) + '?_t=' + Date.now() + TOKEN_PARAM, {{method: 'DELETE'}});
  if (res.ok) {{
    currentFile = '';
    document.getElementById('logContent').innerHTML = '<div class="empty-state">File deleted</div>';
    document.getElementById('fileMeta').textContent = '';
    loadFileList();
  }} else {{
    const err = await res.json();
    alert('Delete failed: ' + (err.error || 'unknown'));
  }}
}});

loadFileList();
</script>
</body>
</html>"""


def _build_key_levels_html(token_param: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Key Levels — Trading Dashboard</title>
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
    min-height: 100vh;
  }}
  nav {{
    display: flex;
    gap: 16px;
    padding: 16px 20px 12px;
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
    align-items: center;
    gap: 12px;
    padding: 14px 20px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }}
  .toolbar label {{
    font-size: 0.82rem;
    color: var(--text-muted);
  }}
  .toolbar select, .toolbar input[type="date"] {{
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 0.88rem;
  }}
  .toolbar select:focus, .toolbar input:focus {{
    outline: none;
    border-color: var(--blue);
  }}
  .btn {{
    background: #238636;
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 7px 18px;
    font-size: 0.88rem;
    cursor: pointer;
    font-weight: 600;
    transition: background 0.15s;
  }}
  .btn:hover {{ background: #2ea043; }}
  .btn:disabled {{ background: #21262d; color: #484f58; cursor: not-allowed; }}
  .btn-danger {{
    background: transparent;
    color: var(--red);
    border: 1px solid rgba(248,81,73,0.3);
    padding: 4px 10px;
    font-size: 0.78rem;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 600;
  }}
  .btn-danger:hover {{ background: rgba(248,81,73,0.1); }}
  .layout {{
    display: flex;
    height: calc(100vh - 100px);
  }}
  .sidebar {{
    width: 280px;
    min-width: 280px;
    background: var(--card-bg);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    padding: 12px;
  }}
  .sidebar h3 {{
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .file-item {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 10px;
    border-radius: 6px;
    cursor: pointer;
    margin-bottom: 2px;
    border: 1px solid transparent;
    transition: all 0.1s;
  }}
  .file-item:hover {{ background: #21262d; }}
  .file-item.active {{ background: rgba(31,111,235,0.12); border-color: var(--blue); }}
  .file-name {{ font-size: 0.82rem; word-break: break-all; }}
  .file-meta {{ font-size: 0.72rem; color: #484f58; margin-top: 2px; }}
  .viewer {{
    flex: 1;
    overflow: auto;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
  }}
  .viewer img {{
    max-width: 100%;
    max-height: 100%;
    border-radius: 8px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
  }}
  .placeholder {{
    color: #484f58;
    font-size: 1rem;
    text-align: center;
  }}
  .toast {{
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 18px;
    font-size: 0.85rem;
    display: none;
    z-index: 100;
    box-shadow: 0 4px 16px rgba(0,0,0,0.4);
  }}
  .toast.show {{ display: block; }}
  @media (max-width: 640px) {{
    .layout {{ flex-direction: column; height: auto; }}
    .sidebar {{ width: 100%; min-width: unset; max-height: 200px; border-right: none; border-bottom: 1px solid var(--border); }}
    .viewer {{ min-height: 60vh; }}
  }}
</style>
</head>
<body>

<nav>
  <a href="/?{token_param.lstrip("&")}">Positions</a>
  <a href="/logs?{token_param.lstrip("&")}">Logs</a>
  <a href="/key-levels?{token_param.lstrip("&")}" class="active">Key Levels</a>
</nav>

<div class="toolbar">
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
  <label>Session</label>
  <select id="inp-session">
    <option value="day">日盤</option>
    <option value="night">夜盤</option>
  </select>
  <label>Symbol</label>
  <select id="inp-sym">
    <option value="MXF">MXF (小台)</option>
    <option value="TXF">TXF (大台)</option>
  </select>
  <label>Lookback</label>
  <select id="inp-lb">
    <option value="1" selected>1 (預設)</option>
    <option value="2">2 sessions</option>
    <option value="3">3 sessions</option>
    <option value="5">5 sessions</option>
    <option value="7">7 sessions</option>
    <option value="10">10 sessions</option>
  </select>
  <button class="btn" id="btn-gen" onclick="generate()">Generate</button>
</div>

<div class="layout">
  <div class="sidebar">
    <h3>Generated Charts</h3>
    <div id="file-list"></div>
  </div>
  <div class="viewer" id="viewer">
    <div class="placeholder">選擇圖表或點擊 Generate 產生新圖表</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const TOKEN_PARAM = "{token_param}";
const SUB_MAP = {{"MXF":"MXFR1", "TXF":"TXFR1"}};
let activeFile = null;

async function loadList() {{
  const res = await fetch('/api/kl/list?_t=' + Date.now() + TOKEN_PARAM);
  const files = await res.json();
  const el = document.getElementById('file-list');
  if (!files.length) {{ el.innerHTML = '<div class="file-meta" style="padding:8px;">No charts yet</div>'; return; }}
  if (!activeFile && files.length) {{ selectFile(files[0].name); }}
  el.innerHTML = files.map(f => `
    <div class="file-item ${{f.name === activeFile ? 'active' : ''}}"
         onclick="selectFile('${{f.name}}')">
      <div>
        <div class="file-name">${{f.name}}</div>
        <div class="file-meta">${{f.size_kb}} KB · ${{f.mtime}}</div>
      </div>
      <button class="btn-danger" onclick="event.stopPropagation(); deleteFile('${{f.name}}')">✕</button>
    </div>
  `).join('');
}}

function selectFile(name) {{
  activeFile = name;
  document.getElementById('viewer').innerHTML =
    `<img src="/charts/${{name}}?_t=${{Date.now()}}${{TOKEN_PARAM}}" alt="${{name}}">`;
  loadList();
}}

async function deleteFile(name) {{
  if (!confirm('Delete ' + name + '?')) return;
  await fetch('/api/kl/delete?name=' + encodeURIComponent(name) + '&_t=' + Date.now() + TOKEN_PARAM);
  if (activeFile === name) {{
    activeFile = null;
    document.getElementById('viewer').innerHTML =
      '<div class="placeholder">選擇圖表或點擊 Generate 產生新圖表</div>';
  }}
  loadList();
}}

async function generate() {{
  const btn = document.getElementById('btn-gen');
  const date = document.getElementById('inp-date').value;
  const tf = document.getElementById('inp-tf').value;
  const sess = document.getElementById('inp-session').value;
  const sym = document.getElementById('inp-sym').value;
  const lb = document.getElementById('inp-lb').value;
  const subSym = SUB_MAP[sym] || sym + 'R1';
  btn.disabled = true; btn.textContent = 'Generating...';
  showToast('Fetching data & computing levels...');
  try {{
    const res = await fetch(`/api/kl/generate?date=${{date}}&timeframe=${{tf}}&session=${{sess}}&symbol=${{sym}}&sub_symbol=${{subSym}}&lookback=${{lb}}&_t=${{Date.now()}}${{TOKEN_PARAM}}`);
    const data = await res.json();
    if (data.ok) {{
      showToast(`Generated: ${{data.filename}} (${{data.bars}} bars, ${{data.levels?.length || 0}} levels)`);
      activeFile = data.filename;
      await loadList();
      selectFile(data.filename);
    }} else {{
      showToast('Error: ' + (data.error || 'Unknown error'));
    }}
  }} catch(e) {{
    showToast('Error: ' + e.message);
  }}
  btn.disabled = false; btn.textContent = 'Generate';
}}

function showToast(msg) {{
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 5000);
}}

loadList();
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
