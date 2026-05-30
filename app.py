# ============================================
# PROP SMASHER BOT - 100% FREE VERSION
# No TradingView Pro needed
# Scans price data directly
# Sends alerts to Telegram
# Deploy on Render for free
# ============================================

from flask import Flask, request, jsonify
import requests
import json
import time
import threading
import pytz
import os
from datetime import datetime

app = Flask(__name__)

# ============================================
# CONFIGURATION
# Set these in Render Environment Variables
# ============================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TWELVE_API_KEY   = os.environ.get("TWELVE_API_KEY",   "")

# Trading Settings
ACCOUNT_SIZE     = 80000
RISK_PERCENT     = 1.5
RR_RATIO         = 3.0
RISK_DOLLAR      = ACCOUNT_SIZE * (RISK_PERCENT / 100)   # $1,200
REWARD_DOLLAR    = RISK_DOLLAR  * RR_RATIO                # $3,600
DAILY_MAX_LOSS   = ACCOUNT_SIZE * 0.05                    # $4,000
TARGET           = 12000

# Which asset to scan
# Options: "XAU/USD" for Gold
#          "US30/USD" for Dow Jones
SYMBOL           = "XAU/USD"
INTERVAL         = "1min"

# IST Timezone
IST = pytz.timezone("Asia/Kolkata")

# ============================================
# P&L TRACKER
# ============================================
pnl = {
    "wins"      : 0,
    "losses"    : 0,
    "total_pnl" : 0.0,
    "daily_pnl" : 0.0,
    "last_date" : ""
}

# ============================================
# ALERT STATE TRACKER
# Prevents duplicate alerts
# ============================================
state = {
    "pre_session_alerted"  : False,
    "ny_trap_alerted"      : False,
    "bull_setup_alerted"   : False,
    "bear_setup_alerted"   : False,
    "buy_signal_alerted"   : False,
    "sell_signal_alerted"  : False,
    "last_session"         : "",
    "last_candle_time"     : "",
    "scan_count"           : 0,
    "last_error"           : "",
    "bot_start_time"       : ""
}

# ============================================
# HELPER: GET IST TIME
# ============================================
def get_ist_time() -> str:
    return datetime.now(IST).strftime("%I:%M:%S %p IST")

def get_ist_date() -> str:
    return datetime.now(IST).strftime("%d %B %Y")

def get_ist_hour_minute() -> tuple:
    now = datetime.now(IST)
    return now.hour, now.minute

# ============================================
# HELPER: RESET DAILY P&L
# ============================================
def check_daily_reset():
    today = get_ist_date()
    if pnl["last_date"] != today:
        pnl["daily_pnl"] = 0.0
        pnl["last_date"] = today
        print(f"[{get_ist_time()}] Daily P&L reset")

# ============================================
# HELPER: GET SESSION
# ============================================
def get_session() -> str:
    hour, minute = get_ist_hour_minute()
    t = hour * 100 + minute

    # IST Times:
    # London Open  = 1:30 PM IST  = 0800 UTC
    # NY Open      = 7:00 PM IST  = 1330 UTC
    # Pre SB       = 7:25 PM IST  = 1355 UTC
    # Silver Bullet= 7:30 PM IST  = 1400 UTC
    # SB End       = 8:30 PM IST  = 1500 UTC
    # NY Kill Zone = 8:30 PM IST  = 1500 UTC
    # NKZ End      = 9:30 PM IST  = 1600 UTC

    if   1300 <= t < 1330 : return "LONDON"
    elif t == 1900         : return "NY_OPEN"
    elif 1900 <= t < 1925  : return "NY_TRAP"
    elif t == 1925         : return "PRE_SILVER_BULLET"
    elif 1930 <= t < 2030  : return "SILVER_BULLET"
    elif 2030 <= t < 2130  : return "NY_KILL_ZONE"
    else                   : return "OUT_OF_SESSION"

# ============================================
# HELPER: SEND TELEGRAM MESSAGE
# ============================================
def send_telegram(message: str) -> bool:
    try:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            print("ERROR: Telegram credentials missing")
            return False

        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id"    : TELEGRAM_CHAT_ID,
            "text"       : message,
            "parse_mode" : "Markdown"
        }
        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            print(f"[{get_ist_time()}] Telegram sent OK")
            return True
        else:
            print(f"[{get_ist_time()}] Telegram error: {response.text}")
            return False

    except Exception as e:
        print(f"[{get_ist_time()}] Telegram exception: {e}")
        state["last_error"] = str(e)
        return False

# ============================================
# HELPER: GET P&L SUMMARY
# ============================================
def get_pnl_summary() -> str:
    remaining   = max(0, TARGET - pnl["total_pnl"])
    progress    = min(100, round((pnl["total_pnl"] / TARGET) * 100, 1))
    wins_needed = max(0, -(-int(remaining) // int(REWARD_DOLLAR)))

    status = "🏆 TARGET HIT!" if pnl["total_pnl"] >= TARGET else f"{wins_needed} wins needed"

    return f"""
━━━━━━━━━━━━━━━━━━━━
📊 *P&L TRACKER*
✅ Wins:      *{pnl['wins']}*
❌ Losses:    *{pnl['losses']}*
💰 Total P&L: *${pnl['total_pnl']:,.0f}*
📅 Daily P&L: *${pnl['daily_pnl']:,.0f}*
📈 Progress:  *{progress}%*
🎯 Remaining: *${remaining:,.0f}*
🔢 Status:    *{status}*
━━━━━━━━━━━━━━━━━━━━"""

# ============================================
# PRICE DATA: FETCH FROM TWELVE DATA
# ============================================
def get_candles(symbol: str = SYMBOL,
                interval: str = INTERVAL,
                count: int = 10) -> list:
    try:
        url    = "https://api.twelvedata.com/time_series"
        params = {
            "symbol"     : symbol,
            "interval"   : interval,
            "outputsize" : count,
            "apikey"     : TWELVE_API_KEY,
            "timezone"   : "Asia/Kolkata"
        }
        response = requests.get(url, params=params, timeout=15)
        data     = response.json()

        if "values" not in data:
            print(f"API Error: {data.get('message', 'Unknown error')}")
            state["last_error"] = data.get("message", "API error")
            return []

        candles = data["values"]
        print(f"[{get_ist_time()}] Got {len(candles)} candles. Latest: {candles[0]['datetime']}")
        return candles

    except requests.exceptions.Timeout:
        print(f"[{get_ist_time()}] API timeout")
        state["last_error"] = "API timeout"
        return []
    except Exception as e:
        print(f"[{get_ist_time()}] Candle fetch error: {e}")
        state["last_error"] = str(e)
        return []

# ============================================
# ANALYSIS: DETECT FAIR VALUE GAP
# ============================================
def detect_fvg(candles: list) -> dict:
    result = {
        "bull_fvg"   : False,
        "bear_fvg"   : False,
        "fvg_top"    : 0.0,
        "fvg_bottom" : 0.0
    }

    if len(candles) < 3:
        return result

    try:
        # Candles are newest first (index 0 = latest)
        c0_high = float(candles[0]["high"])
        c0_low  = float(candles[0]["low"])
        c2_high = float(candles[2]["high"])
        c2_low  = float(candles[2]["low"])

        # Bullish FVG: Candle 0 low is above Candle 2 high
        if c0_low > c2_high:
            result["bull_fvg"]   = True
            result["fvg_top"]    = c0_low
            result["fvg_bottom"] = c2_high

        # Bearish FVG: Candle 0 high is below Candle 2 low
        if c0_high < c2_low:
            result["bear_fvg"]   = True
            result["fvg_top"]    = c2_low
            result["fvg_bottom"] = c0_high

    except (ValueError, KeyError) as e:
        print(f"FVG detection error: {e}")

    return result

# ============================================
# ANALYSIS: DETECT LIQUIDITY SWEEP
# ============================================
def detect_sweep(candles: list, lookback: int = 5) -> dict:
    result = {
        "sweep_high" : False,
        "sweep_low"  : False,
        "level"      : 0.0
    }

    if len(candles) < lookback + 1:
        return result

    try:
        # Current candle (newest)
        curr_high  = float(candles[0]["high"])
        curr_low   = float(candles[0]["low"])
        curr_close = float(candles[0]["close"])

        # Previous candles
        prev_highs = [float(c["high"]) for c in candles[1:lookback+1]]
        prev_lows  = [float(c["low"])  for c in candles[1:lookback+1]]

        recent_high = max(prev_highs)
        recent_low  = min(prev_lows)

        # Sweep High: Wick above recent high, closed back below
        if curr_high > recent_high and curr_close < recent_high:
            result["sweep_high"] = True
            result["level"]      = recent_high

        # Sweep Low: Wick below recent low, closed back above
        if curr_low < recent_low and curr_close > recent_low:
            result["sweep_low"] = True
            result["level"]     = recent_low

    except (ValueError, KeyError) as e:
        print(f"Sweep detection error: {e}")

    return result

# ============================================
# ANALYSIS: DETECT MARKET STRUCTURE SHIFT
# ============================================
def detect_mss(candles: list, lookback: int = 5) -> dict:
    result = {
        "bull_mss" : False,
        "bear_mss" : False
    }

    if len(candles) < lookback + 1:
        return result

    try:
        curr_close = float(candles[0]["close"])
        curr_open  = float(candles[0]["open"])

        prev_highs = [float(c["high"]) for c in candles[1:lookback+1]]
        prev_lows  = [float(c["low"])  for c in candles[1:lookback+1]]

        highest    = max(prev_highs)
        lowest     = min(prev_lows)

        # Bullish MSS: Close breaks above recent structure
        if curr_close > highest and curr_close > curr_open:
            result["bull_mss"] = True

        # Bearish MSS: Close breaks below recent structure
        if curr_close < lowest and curr_close < curr_open:
            result["bear_mss"] = True

    except (ValueError, KeyError) as e:
        print(f"MSS detection error: {e}")

    return result

# ============================================
# ALERTS: MESSAGE FORMATTERS
# ============================================
def msg_startup() -> str:
    return f"""
🚀 *PROP SMASHER BOT ONLINE* 🚀
━━━━━━━━━━━━━━━━━━━━
✅ Bot is running
🕐 Time: `{get_ist_time()}`
📅 Date: `{get_ist_date()}`
📊 Scanning: *{SYMBOL}*
⏱ Interval: *{INTERVAL}*
━━━━━━━━━━━━━━━━━━━━
⏰ *IST SCHEDULE TODAY:*
🔴 NY Trap:      *7:00 PM IST*
⚠️ Prepare:      *7:25 PM IST*
🔵 Silver Bullet: *7:30-8:30 PM IST*
━━━━━━━━━━━━━━━━━━━━
💰 Account:  *${ACCOUNT_SIZE:,}*
🛑 Risk:     *${RISK_DOLLAR:,.0f}/trade*
🎯 Reward:   *${REWARD_DOLLAR:,.0f}/trade*
🏆 Target:   *${TARGET:,}*
━━━━━━━━━━━━━━━━━━━━
✅ *All systems ready. Good luck!* 🇮🇳
    """

def msg_ny_trap(price: str) -> str:
    return f"""
⛔ *NY OPEN TRAP - DO NOT TRADE* ⛔
━━━━━━━━━━━━━━━━━━━━
🕐 Time:  `{get_ist_time()}`
📊 Asset: *{SYMBOL}*
💰 Price: *{price}*
━━━━━━━━━━━━━━━━━━━━
⚠️ *7:00 PM IST NY TRAP IS ACTIVE*

Banks are creating fake moves
to trap retail traders RIGHT NOW

❌ *DO NOT TRADE*
✅ *WAIT for 7:30 PM IST*
━━━━━━━━━━━━━━━━━━━━
Use this time to:
📋 Mark NY Open High and Low
💧 Spot liquidity pools
📊 Plan your direction
    """

def msg_pre_session(price: str) -> str:
    return f"""
🔔 *PRE-SESSION ALERT* 🔔
━━━━━━━━━━━━━━━━━━━━
🕐 Time:  `{get_ist_time()}`
📊 Asset: *{SYMBOL}*
💰 Price: *{price}*
━━━━━━━━━━━━━━━━━━━━
🚨 *ACTION REQUIRED NOW:*

✅ Step 1: Open Rebels Funding Terminal
✅ Step 2: Load {SYMBOL}
✅ Step 3: Set chart to 1-Minute
✅ Step 4: Watch for signals
━━━━━━━━━━━━━━━━━━━━
⏰ *Silver Bullet in 5 MINUTES*
🔵 *Starts at 7:30 PM IST*
{get_pnl_summary()}
    """

def msg_setup_forming(direction: str,
                      price: str,
                      fvg_top: float,
                      fvg_bottom: float) -> str:
    emoji = "🟢" if direction == "BUY" else "🔴"
    return f"""
{emoji} *{direction} SETUP FORMING* {emoji}
━━━━━━━━━━━━━━━━━━━━
🕐 Time:  `{get_ist_time()}`
📊 Asset: *{SYMBOL}*
💰 Price: *{price}*
━━━━━━━━━━━━━━━━━━━━
*SIGNAL CHECKLIST:*
✅ Liquidity Sweep: *DETECTED*
✅ Fair Value Gap:  *{fvg_bottom:.2f} - {fvg_top:.2f}*
⏳ MSS:             *PENDING...*
━━━━━━━━━━━━━━━━━━━━
👉 Prepare *{direction}* order on RF
👉 Wait for MSS confirmation
👉 Have finger ready on execute
━━━━━━━━━━━━━━━━━━━━
💵 Risk:   *${RISK_DOLLAR:,.0f}*
🎯 Reward: *${REWARD_DOLLAR:,.0f}*
📈 RR:     *1:{int(RR_RATIO)}*
━━━━━━━━━━━━━━━━━━━━
⚡ *NEXT ALERT = EXECUTE*
    """

def msg_execute(direction: str, price: str) -> str:
    emoji  = "🟢" if direction == "BUY"  else "🔴"
    sl_dir = "MINUS" if direction == "BUY" else "PLUS"
    tp_dir = "PLUS"  if direction == "BUY" else "MINUS"

    try:
        entry    = float(price)
        if "XAU" in SYMBOL:
            sl_val = entry - 3.0 if direction == "BUY" else entry + 3.0
            tp_val = entry + 9.0 if direction == "BUY" else entry - 9.0
            lots   = "10 LOTS"
            sl_str = f"{sl_val:.2f}"
            tp_str = f"{tp_val:.2f}"
        else:
            sl_val = entry - 20 if direction == "BUY" else entry + 20
            tp_val = entry + 60 if direction == "BUY" else entry - 60
            lots   = "6 LOTS"
            sl_str = f"{sl_val:.0f}"
            tp_str = f"{tp_val:.0f}"
    except Exception:
        sl_str = "Entry " + sl_dir + " 20pts"
        tp_str = "Entry " + tp_dir + " 60pts"
        lots   = "6 LOTS"

    return f"""
🚨🚨🚨 *EXECUTE {direction} NOW* 🚨🚨🚨
━━━━━━━━━━━━━━━━━━━━
{emoji} Direction: *{direction}*
📊 Asset:     *{SYMBOL}*
💰 Entry:     *{price}*
🕐 Time:      `{get_ist_time()}`
━━━━━━━━━━━━━━━━━━━━
📋 *TRADE DETAILS:*
📦 Lots:  *{lots}*
🛑 SL:    *{sl_str}*
🎯 TP:    *{tp_str}*
━━━━━━━━━━━━━━━━━━━━
💵 Risk:  *${RISK_DOLLAR:,.0f}*
💰 Reward: *${REWARD_DOLLAR:,.0f}*
📈 RR:    *1:{int(RR_RATIO)}*
{get_pnl_summary()}
⚡⚡ *EXECUTE ON RF TERMINAL NOW* ⚡⚡
    """

def msg_session_ended() -> str:
    return f"""
🔵 *SILVER BULLET SESSION ENDED*
━━━━━━━━━━━━━━━━━━━━
🕐 Time: `{get_ist_time()}`
━━━━━━━━━━━━━━━━━━━━
✅ *STOP TRADING FOR TODAY*
Close any open positions
Close RF Terminal
Rest and come back tomorrow
{get_pnl_summary()}
    """

# ============================================
# MAIN SCANNER LOOP
# Runs every 60 seconds in background
# ============================================
def scanner_loop():
    print(f"[{get_ist_time()}] Scanner thread started")
    state["bot_start_time"] = get_ist_time()

    # Send startup message
    time.sleep(3)
    send_telegram(msg_startup())

    session_end_alerted = False

    while True:
        try:
            check_daily_reset()

            hour, minute = get_ist_hour_minute()
            t            = hour * 100 + minute
            session      = get_session()
            state["scan_count"] += 1

            print(f"[{get_ist_time()}] Session: {session} | Scan: {state['scan_count']}")

            # ----------------------------------------
            # NY TRAP ALERT (7:00 PM IST)
            # ----------------------------------------
            if hour == 19 and minute == 0:
                if not state["ny_trap_alerted"]:
                    candles = get_candles()
                    price   = candles[0]["close"] if candles else "N/A"
                    send_telegram(msg_ny_trap(str(price)))
                    state["ny_trap_alerted"] = True
            else:
                if hour != 19:
                    state["ny_trap_alerted"] = False

            # ----------------------------------------
            # PRE SILVER BULLET ALERT (7:25 PM IST)
            # ----------------------------------------
            if hour == 19 and minute == 25:
                if not state["pre_session_alerted"]:
                    candles = get_candles()
                    price   = candles[0]["close"] if candles else "N/A"
                    send_telegram(msg_pre_session(str(price)))
                    state["pre_session_alerted"] = True
            else:
                if hour != 19:
                    state["pre_session_alerted"] = False

            # ----------------------------------------
            # SILVER BULLET SCANNING (7:30-8:30 PM IST)
            # ----------------------------------------
            if session == "SILVER_BULLET":
                session_end_alerted = False

                # Fetch latest candles
                candles = get_candles(count=10)

                if candles and len(candles) >= 6:
                    current_price    = candles[0]["close"]
                    current_candle   = candles[0]["datetime"]

                    # Skip if same candle already processed
                    if current_candle == state["last_candle_time"]:
                        print(f"[{get_ist_time()}] Same candle, skipping")
                        time.sleep(60)
                        continue

                    state["last_candle_time"] = current_candle

                    # Run analysis
                    fvg   = detect_fvg(candles)
                    sweep = detect_sweep(candles)
                    mss   = detect_mss(candles)

                    print(f"[{get_ist_time()}] Price: {current_price}")
                    print(f"[{get_ist_time()}] FVG: Bull={fvg['bull_fvg']} Bear={fvg['bear_fvg']}")
                    print(f"[{get_ist_time()}] Sweep: High={sweep['sweep_high']} Low={sweep['sweep_low']}")
                    print(f"[{get_ist_time()}] MSS: Bull={mss['bull_mss']} Bear={mss['bear_mss']}")

                    # ---- BULL SETUP FORMING ----
                    if (sweep["sweep_low"]  and
                        fvg["bull_fvg"]     and
                        not mss["bull_mss"] and
                        not state["bull_setup_alerted"]):

                        send_telegram(msg_setup_forming(
                            "BUY",
                            current_price,
                            fvg["fvg_top"],
                            fvg["fvg_bottom"]
                        ))
                        state["bull_setup_alerted"] = True
                        state["bear_setup_alerted"] = False

                    # ---- BEAR SETUP FORMING ----
                    if (sweep["sweep_high"] and
                        fvg["bear_fvg"]     and
                        not mss["bear_mss"] and
                        not state["bear_setup_alerted"]):

                        send_telegram(msg_setup_forming(
                            "SELL",
                            current_price,
                            fvg["fvg_top"],
                            fvg["fvg_bottom"]
                        ))
                        state["bear_setup_alerted"] = True
                        state["bull_setup_alerted"] = False

                    # ---- EXECUTE BUY ----
                    if (sweep["sweep_low"]  and
                        fvg["bull_fvg"]     and
                        mss["bull_mss"]     and
                        not state["buy_signal_alerted"]):

                        send_telegram(msg_execute("BUY", current_price))
                        state["buy_signal_alerted"]  = True
                        state["sell_signal_alerted"] = False
                        state["bull_setup_alerted"]  = False

                    # ---- EXECUTE SELL ----
                    if (sweep["sweep_high"] and
                        fvg["bear_fvg"]     and
                        mss["bear_mss"]     and
                        not state["sell_signal_alerted"]):

                        send_telegram(msg_execute("SELL", current_price))
                        state["sell_signal_alerted"] = True
                        state["buy_signal_alerted"]  = False
                        state["bear_setup_alerted"]  = False

            # ----------------------------------------
            # SILVER BULLET ENDED (8:30 PM IST)
            # ----------------------------------------
            if session == "NY_KILL_ZONE" and not session_end_alerted:
                send_telegram(msg_session_ended())
                session_end_alerted = True

                # Reset all signal states for next day
                state["bull_setup_alerted"]  = False
                state["bear_setup_alerted"]  = False
                state["buy_signal_alerted"]  = False
                state["sell_signal_alerted"] = False

            # ----------------------------------------
            # SESSION CHANGE RESET
            # ----------------------------------------
            if session != state["last_session"]:
                print(f"[{get_ist_time()}] Session: {state['last_session']} → {session}")
                state["last_session"] = session

                if session == "SILVER_BULLET":
                    # Fresh start for each Silver Bullet
                    state["bull_setup_alerted"]  = False
                    state["bear_setup_alerted"]  = False
                    state["buy_signal_alerted"]  = False
                    state["sell_signal_alerted"] = False
                    print(f"[{get_ist_time()}] Signal states reset for Silver Bullet")

        except Exception as e:
            print(f"[{get_ist_time()}] Scanner error: {e}")
            state["last_error"] = str(e)

        # Wait 60 seconds before next scan
        # Matches 1-minute candle interval
        time.sleep(60)

# ============================================
# FLASK ROUTES
# ============================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "bot"          : "Prop Smasher - 100% Free",
        "status"       : "running",
        "ist_time"     : get_ist_time(),
        "session"      : get_session(),
        "scan_count"   : state["scan_count"],
        "symbol"       : SYMBOL,
        "routes"       : {
            "/health"  : "GET  - Health check",
            "/status"  : "GET  - Status + Telegram",
            "/win"     : "GET  - Record a win",
            "/loss"    : "GET  - Record a loss",
            "/pnl"     : "GET  - View P&L",
            "/test"    : "GET  - Test Telegram"
        }
    })

# ----------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status"       : "healthy",
        "ist_time"     : get_ist_time(),
        "session"      : get_session(),
        "scan_count"   : state["scan_count"],
        "last_error"   : state["last_error"],
        "last_candle"  : state["last_candle_time"]
    }), 200

# ----------------------------------------
@app.route("/status", methods=["GET"])
def status():
    msg = f"""
📊 *BOT STATUS CHECK*
━━━━━━━━━━━━━━━━━━━━
✅ Bot:      *ONLINE*
🕐 Time:     `{get_ist_time()}`
📅 Date:     `{get_ist_date()}`
📡 Session:  *{get_session()}*
📊 Symbol:   *{SYMBOL}*
🔄 Scans:    *{state['scan_count']}*
━━━━━━━━━━━━━━━━━━━━
*SIGNAL STATES:*
Bull Setup: *{"✅ Alerted" if state['bull_setup_alerted'] else "⏳ Watching"}*
Bear Setup: *{"✅ Alerted" if state['bear_setup_alerted'] else "⏳ Watching"}*
Buy Signal: *{"✅ Fired"   if state['buy_signal_alerted'] else "⏳ Waiting"}*
Sell Signal: *{"✅ Fired"  if state['sell_signal_alerted'] else "⏳ Waiting"}*
{get_pnl_summary()}
    """
    send_telegram(msg)
    return jsonify({
        "status"  : "ok",
        "session" : get_session(),
        "pnl"     : pnl,
        "state"   : state
    })

# ----------------------------------------
@app.route("/win", methods=["GET", "POST"])
def record_win():
    check_daily_reset()
    pnl["wins"]      += 1
    pnl["total_pnl"] += REWARD_DOLLAR
    pnl["daily_pnl"] += REWARD_DOLLAR

    remaining = max(0, TARGET - pnl["total_pnl"])
    target_hit = pnl["total_pnl"] >= TARGET

    extra = "\n\n🏆🏆🏆 *TARGET HIT! STOP TRADING!* 🏆🏆🏆" if target_hit else ""

    msg = f"""
✅ *WIN RECORDED!* ✅
━━━━━━━━━━━━━━━━━━━━
💰 +${REWARD_DOLLAR:,.0f} added
{get_pnl_summary()}{extra}
    """
    send_telegram(msg)
    return jsonify({
        "status"     : "win recorded",
        "total_pnl"  : pnl["total_pnl"],
        "remaining"  : remaining,
        "target_hit" : target_hit
    })

# ----------------------------------------
@app.route("/loss", methods=["GET", "POST"])
def record_loss():
    check_daily_reset()
    pnl["losses"]    += 1
    pnl["total_pnl"] -= RISK_DOLLAR
    pnl["daily_pnl"] -= RISK_DOLLAR

    warning = ""
    if pnl["daily_pnl"] <= -(DAILY_MAX_LOSS * 0.75):
        warning = "\n⚠️ *WARNING: 1 trade left today!*"
    if pnl["daily_pnl"] <= -(DAILY_MAX_LOSS * 0.90):
        warning = "\n🛑 *STOP NOW - Near Daily Limit!*"

    msg = f"""
❌ *LOSS RECORDED* ❌
━━━━━━━━━━━━━━━━━━━━
💸 -${RISK_DOLLAR:,.0f} recorded
{get_pnl_summary()}{warning}
    """
    send_telegram(msg)
    return jsonify({
        "status"    : "loss recorded",
        "daily_pnl" : pnl["daily_pnl"],
        "total_pnl" : pnl["total_pnl"]
    })

# ----------------------------------------
@app.route("/pnl", methods=["GET"])
def view_pnl():
    send_telegram(get_pnl_summary())
    return jsonify({"pnl": pnl})

# ----------------------------------------
@app.route("/test", methods=["GET"])
def test_telegram():
    success = send_telegram(f"""
✅ *TEST MESSAGE*
━━━━━━━━━━━━━━━━━━━━
🕐 Time: `{get_ist_time()}`
✅ Telegram connection working
✅ Bot is ready
━━━━━━━━━━━━━━━━━━━━
    """)
    return jsonify({
        "telegram_ok" : success,
        "ist_time"    : get_ist_time()
    })

# ============================================
# START SCANNER THREAD
# ============================================
scanner = threading.Thread(
    target = scanner_loop,
    daemon = True,
    name   = "PriceScanner"
)
scanner.start()
print(f"[{get_ist_time()}] Scanner thread started")

# ============================================
# RUN APP
# ============================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Prop Smasher Bot Starting")
    print(f"🕐 IST: {get_ist_time()}")
    print(f"📊 Symbol: {SYMBOL}")
    print(f"🌐 Port: {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
