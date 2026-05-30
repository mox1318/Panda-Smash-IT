# ============================================
# FREE ALTERNATIVE: Python Price Scanner
# No TradingView Pro needed
# Uses Yahoo Finance (FREE) or 
# Twelve Data (FREE tier)
# ============================================

import requests
import pytz
import time
import threading
from datetime import datetime
from flask import Flask, jsonify
import os

app  = Flask(__name__)
IST  = pytz.timezone("Asia/Kolkata")

# ============================================
# CONFIGURATION
# ============================================
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TWELVE_API_KEY   = os.environ.get("TWELVE_API_KEY")  # Free at twelvedata.com

# ============================================
# HELPER FUNCTIONS
# ============================================
def get_ist_time() -> str:
    return datetime.now(IST).strftime("%I:%M:%S %p IST")

def get_ist_hour_minute():
    now = datetime.now(IST)
    return now.hour, now.minute

def send_telegram(message: str):
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id"    : TELEGRAM_CHAT_ID,
            "text"       : message,
            "parse_mode" : "Markdown"
        }
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================
# GET PRICE DATA (FREE - Twelve Data)
# Sign up free at twelvedata.com
# Free tier = 800 API calls/day
# ============================================
def get_price_data(symbol: str = "XAU/USD", interval: str = "1min"):
    try:
        url    = f"https://api.twelvedata.com/time_series"
        params = {
            "symbol"    : symbol,
            "interval"  : interval,
            "outputsize": 10,
            "apikey"    : TWELVE_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data     = response.json()

        if "values" not in data:
            print(f"API error: {data}")
            return None

        candles = data["values"]
        return candles

    except Exception as e:
        print(f"Price fetch error: {e}")
        return None

# ============================================
# DETECT FAIR VALUE GAP (FVG)
# ============================================
def detect_fvg(candles: list) -> dict:
    result = {"bull_fvg": False, "bear_fvg": False,
              "fvg_top": 0, "fvg_bottom": 0}

    if len(candles) < 3:
        return result

    # Most recent 3 candles (index 0 = newest)
    c0_low   = float(candles[0]["low"])
    c0_high  = float(candles[0]["high"])
    c2_low   = float(candles[2]["low"])
    c2_high  = float(candles[2]["high"])

    # Bullish FVG: Gap between candle 0 low and candle 2 high
    if c0_low > c2_high:
        result["bull_fvg"]    = True
        result["fvg_top"]     = c0_low
        result["fvg_bottom"]  = c2_high

    # Bearish FVG: Gap between candle 0 high and candle 2 low
    if c0_high < c2_low:
        result["bear_fvg"]    = True
        result["fvg_top"]     = c2_low
        result["fvg_bottom"]  = c0_high

    return result

# ============================================
# DETECT LIQUIDITY SWEEP
# ============================================
def detect_sweep(candles: list, lookback: int = 5) -> dict:
    result = {"sweep_high": False, "sweep_low": False, "level": 0}

    if len(candles) < lookback + 1:
        return result

    # Current candle
    curr_high  = float(candles[0]["high"])
    curr_low   = float(candles[0]["low"])
    curr_close = float(candles[0]["close"])

    # Previous candles (skip index 0)
    prev_highs = [float(c["high"]) for c in candles[1:lookback+1]]
    prev_lows  = [float(c["low"])  for c in candles[1:lookback+1]]

    recent_high = max(prev_highs)
    recent_low  = min(prev_lows)

    # Sweep High: Wick above recent high but closed below it
    if curr_high > recent_high and curr_close < recent_high:
        result["sweep_high"] = True
        result["level"]      = recent_high

    # Sweep Low: Wick below recent low but closed above it
    if curr_low < recent_low and curr_close > recent_low:
        result["sweep_low"] = True
        result["level"]     = recent_low

    return result

# ============================================
# DETECT MARKET STRUCTURE SHIFT (MSS)
# ============================================
def detect_mss(candles: list, lookback: int = 5) -> dict:
    result = {"bull_mss": False, "bear_mss": False}

    if len(candles) < lookback + 1:
        return result

    curr_close = float(candles[0]["close"])

    prev_highs = [float(c["high"]) for c in candles[1:lookback+1]]
    prev_lows  = [float(c["low"])  for c in candles[1:lookback+1]]

    highest    = max(prev_highs)
    lowest     = min(prev_lows)

    if curr_close > highest:
        result["bull_mss"] = True

    if curr_close < lowest:
        result["bear_mss"] = True

    return result

# ============================================
# IST SESSION CHECKER
# ============================================
def get_current_session() -> str:
    hour, minute = get_ist_hour_minute()
    time_val     = hour * 100 + minute

    if 1330 <= time_val < 1400:
        return "NY_TRAP"
    elif time_val == 1355:
        return "PRE_SILVER_BULLET"
    elif 1400 <= time_val < 1500:
        return "SILVER_BULLET"
    elif 1500 <= time_val < 1600:
        return "NY_KILL_ZONE"
    elif 800  <= time_val < 930:
        return "LONDON"
    else:
        return "OUT_OF_SESSION"

# ============================================
# ALERT STATE (Prevent duplicate alerts)
# ============================================
alert_state = {
    "pre_session_sent"   : False,
    "ny_trap_sent"       : False,
    "bull_setup_sent"    : False,
    "bear_setup_sent"    : False,
    "buy_signal_sent"    : False,
    "sell_signal_sent"   : False,
    "last_session"       : "",
    "last_signal_bar"    : ""
}

# ============================================
# MAIN SCANNER LOOP
# ============================================
def scanner_loop():
    print(f"[{get_ist_time()}] Scanner started")
    send_telegram(f"🚀 *Prop Smasher Bot Online*\n🕐 `{get_ist_time()}`\n✅ Scanning for setups...")

    while True:
        try:
            hour, minute = get_ist_hour_minute()
            session      = get_current_session()

            # ---- PRE SESSION ALERT (7:25 PM IST) ----
            if hour == 19 and minute == 25:
                if not alert_state["pre_session_sent"]:
                    send_telegram(f"""
🔔 *PRE-SESSION ALERT* 🔔
━━━━━━━━━━━━━━━━━━━━
🕐 Time: `{get_ist_time()}`
━━━━━━━━━━━━━━━━━━━━
✅ Step 1: Open Rebels Funding Terminal
✅ Step 2: Load US30 or XAUUSD  
✅ Step 3: Set chart to 1-Minute
✅ Step 4: Watch for SWEEP signal
⏰ *Silver Bullet starts at 7:30 PM IST*
━━━━━━━━━━━━━━━━━━━━
⏰ *5 MINUTES TO GET READY*
                    """)
                    alert_state["pre_session_sent"] = True
            else:
                alert_state["pre_session_sent"] = False

            # ---- NY TRAP ALERT (7:00 PM IST) ----
            if hour == 19 and minute == 0:
                if not alert_state["ny_trap_sent"]:
                    send_telegram(f"""
⛔ *NY TRAP - DO NOT TRADE* ⛔
━━━━━━━━━━━━━━━━━━━━
🕐 Time: `{get_ist_time()}`
❌ *DO NOT TRADE NOW*
✅ *WAIT for 7:30 PM IST Silver Bullet*
                    """)
                    alert_state["ny_trap_sent"] = True
            else:
                alert_state["ny_trap_sent"] = False

            # ---- SIGNAL SCANNING (Silver Bullet Only) ----
            if session == "SILVER_BULLET":
                candles = get_price_data("XAU/USD", "1min")

                if candles and len(candles) >= 6:
                    fvg   = detect_fvg(candles)
                    sweep = detect_sweep(candles)
                    mss   = detect_mss(candles)

                    current_price = candles[0]["close"]
                    current_bar   = candles[0]["datetime"]

                    # ---- BULL SETUP FORMING ----
                    if (sweep["sweep_low"] and
                        fvg["bull_fvg"] and
                        not mss["bull_mss"]):

                        if not alert_state["bull_setup_sent"]:
                            send_telegram(f"""
🟡 *BULL SETUP FORMING* 🟡
━━━━━━━━━━━━━━━━━━━━
🕐 Time:  `{get_ist_time()}`
💰 Price: `{current_price}`
━━━━━━━━━━━━━━━━━━━━
✅ Sweep Low:   *DETECTED*
✅ Bull FVG:    *{fvg['fvg_bottom']:.2f} - {fvg['fvg_top']:.2f}*
⏳ MSS:         *PENDING*
━━━━━━━━━━━━━━━━━━━━
👉 Prepare *BUY* order
⚡ *NEXT ALERT = EXECUTE*
                            """)
                            alert_state["bull_setup_sent"] = True
                    else:
                        alert_state["bull_setup_sent"] = False

                    # ---- BEAR SETUP FORMING ----
                    if (sweep["sweep_high"] and
                        fvg["bear_fvg"] and
                        not mss["bear_mss"]):

                        if not alert_state["bear_setup_sent"]:
                            send_telegram(f"""
🟡 *BEAR SETUP FORMING* 🟡
━━━━━━━━━━━━━━━━━━━━
🕐 Time:  `{get_ist_time()}`
💰 Price: `{current_price}`
━━━━━━━━━━━━━━━━━━━━
✅ Sweep High:  *DETECTED*
✅ Bear FVG:    *{fvg['fvg_bottom']:.2f} - {fvg['fvg_top']:.2f}*
⏳ MSS:         *PENDING*
━━━━━━━━━━━━━━━━━━━━
👉 Prepare *SELL* order
⚡ *NEXT ALERT = EXECUTE*
                            """)
                            alert_state["bear_setup_sent"] = True
                    else:
                        alert_state["bear_setup_sent"] = False

                    # ---- EXECUTE BUY SIGNAL ----
                    if (sweep["sweep_low"] and
                        fvg["bull_fvg"]   and
                        mss["bull_mss"]   and
                        current_bar != alert_state["last_signal_bar"]):

                        if not alert_state["buy_signal_sent"]:
                            send_telegram(f"""
🚨🚨🚨 *EXECUTE BUY NOW* 🚨🚨🚨
━━━━━━━━━━━━━━━━━━━━
🟢 Direction: *BUY*
💰 Entry:     *{current_price}*
🕐 Time:      `{get_ist_time()}`
━━━━━━━━━━━━━━━━━━━━
📦 Lots:  *6 LOTS*
🛑 SL:    *20 pts below entry*
🎯 TP:    *60 pts above entry*
━━━━━━━━━━━━━━━━━━━━
💵 Risk:   *$1,200*
💰 Reward: *$3,600*
📈 RR:     *1:3*
━━━━━━━━━━━━━━━━━━━━
⚡⚡ *OPEN RF AND EXECUTE NOW* ⚡⚡
                            """)
                            alert_state["buy_signal_sent"]  = True
                            alert_state["last_signal_bar"]  = current_bar
                    else:
                        alert_state["buy_signal_sent"] = False

                    # ---- EXECUTE SELL SIGNAL ----
                    if (sweep["sweep_high"] and
                        fvg["bear_fvg"]    and
                        mss["bear_mss"]    and
                        current_bar != alert_state["last_signal_bar"]):

                        if not alert_state["sell_signal_sent"]:
                            send_telegram(f"""
🚨🚨🚨 *EXECUTE SELL NOW* 🚨🚨🚨
━━━━━━━━━━━━━━━━━━━━
🔴 Direction: *SELL*
💰 Entry:     *{current_price}*
🕐 Time:      `{get_ist_time()}`
━━━━━━━━━━━━━━━━━━━━
📦 Lots:  *6 LOTS*
🛑 SL:    *20 pts above entry*
🎯 TP:    *60 pts below entry*
━━━━━━━━━━━━━━━━━━━━
💵 Risk:   *$1,200*
💰 Reward: *$3,600*
📈 RR:     *1:3*
━━━━━━━━━━━━━━━━━━━━
⚡⚡ *OPEN RF AND EXECUTE NOW* ⚡⚡
                            """)
                            alert_state["sell_signal_sent"] = True
                            alert_state["last_signal_bar"]  = current_bar
                    else:
                        alert_state["sell_signal_sent"] = False

            # Reset signals when session changes
            if session != alert_state["last_session"]:
                alert_state["bull_setup_sent"]  = False
                alert_state["bear_setup_sent"]  = False
                alert_state["buy_signal_sent"]  = False
                alert_state["sell_signal_sent"] = False
                alert_state["last_session"]     = session
                print(f"[{get_ist_time()}] Session changed to: {session}")

        except Exception as e:
            print(f"Scanner error: {e}")

        # Scan every 60 seconds (matches 1-minute candles)
        time.sleep(60)

# ============================================
# FLASK ROUTES
# ============================================
pnl_tracker = {
    "wins": 0, "losses": 0,
    "total_pnl": 0.0, "daily_pnl": 0.0
}

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "bot"     : "Prop Smasher - FREE Version",
        "status"  : "running",
        "time"    : get_ist_time(),
        "session" : get_current_session()
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "time": get_ist_time()})

@app.route("/status", methods=["GET"])
def status():
    send_telegram(f"""
📊 *BOT STATUS*
━━━━━━━━━━━━━━━━━━━━
✅ Bot: *ONLINE*
🕐 Time: `{get_ist_time()}`
📡 Session: *{get_current_session()}*
━━━━━━━━━━━━━━━━━━━━
✅ Wins:    *{pnl_tracker['wins']}*
❌ Losses:  *{pnl_tracker['losses']}*
💰 P&L:    *${pnl_tracker['total_pnl']:,.0f}*
🏆 Target: *$12,000*
    """)
    return jsonify({"status": "ok", "pnl": pnl_tracker})

@app.route("/win", methods=["POST", "GET"])
def win():
    pnl_tracker["wins"]      += 1
    pnl_tracker["total_pnl"] += 3600
    pnl_tracker["daily_pnl"] += 3600
    remaining = 12000 - pnl_tracker["total_pnl"]
    send_telegram(f"""
✅ *WIN RECORDED* ✅
💰 +$3,600
📊 Total P&L: *${pnl_tracker['total_pnl']:,.0f}*
🏆 Remaining: *${remaining:,.0f}*
🔢 Wins: *{pnl_tracker['wins']}*
    """)
    return jsonify({"status": "win", "pnl": pnl_tracker})

@app.route("/loss", methods=["POST", "GET"])
def loss():
    pnl_tracker["losses"]    += 1
    pnl_tracker["total_pnl"] -= 1200
    pnl_tracker["daily_pnl"] -= 1200
    warning = ""
    if pnl_tracker["daily_pnl"] <= -2400:
        warning = "\n⚠️ *WARNING: 1 trade left today!*"
    if pnl_tracker["daily_pnl"] <= -3600:
        warning = "\n🛑 *STOP TRADING - Near Daily Limit!*"
    send_telegram(f"""
❌ *LOSS RECORDED* ❌
💸 -$1,200
📊 Total P&L: *${pnl_tracker['total_pnl']:,.0f}*
🔢 Losses: *{pnl_tracker['losses']}*{warning}
    """)
    return jsonify({"status": "loss", "pnl": pnl_tracker})

# ============================================
# START SCANNER IN BACKGROUND
# ============================================
scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
scanner_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting Prop Smasher FREE Version")
    print(f"🕐 IST Time: {get_ist_time()}")
    app.run(host="0.0.0.0", port=port, debug=False)
