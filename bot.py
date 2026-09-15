from flask import Flask, request
import requests
import os
import math

app = Flask(__name__)

# Read values from Render environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_KEY = os.getenv("TWELVE_DATA_KEY")
CHAT_ID = os.getenv("CHAT_ID")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def get_xau_data(interval, limit=100):
    """Fetch XAUUSD candles from Twelve Data"""
    url = "https://api.twelvedata.com/time_series"
    response = requests.get(url, params={
        "symbol": "XAU/USD",
        "interval": interval,
        "outputsize": limit,
        "apikey": TWELVE_DATA_KEY
    })
    data = response.json()
    return data.get("values", []) if "values" in data else []

def calc_ema(candles, period):
    """Calculate EMA for price"""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[:period]]
    return sum(closes) / period

def calc_rsi(candles, period=7):
    """Calculate RSI"""
    if len(candles) < period + 1:
        return None
    closes = [float(c["close"]) for c in reversed(candles[:period+1])]
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i-1] - closes[i]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            losses.append(-change)
            gains.append(0)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100 if avg_gain > 0 else 0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_vwap(candles):
    """Calculate VWAP"""
    if len(candles) < 5:
        return None, None, None
    typical_prices = []
    volumes = []
    for c in candles[:20]:
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        vol = float(c.get("volume", 1))
        typical_prices.append(tp)
        volumes.append(vol)
    
    cum_tp_vol = sum(tp * vol for tp, vol in zip(typical_prices, volumes))
    cum_vol = sum(volumes)
    vwap = cum_tp_vol / cum_vol if cum_vol > 0 else typical_prices[0]
    
    std_dev = math.sqrt(sum((tp - vwap)**2 for tp in typical_prices) / len(typical_prices))
    return vwap, vwap + std_dev, vwap - std_dev

def detect_market_structure(candles):
    """Detect higher lows (uptrend) or lower highs (downtrend)"""
    if len(candles) < 5:
        return None
    
    lows = [float(c["low"]) for c in candles[:5]]
    highs = [float(c["high"]) for c in candles[:5]]
    
    # Higher lows = uptrend
    if lows[0] < lows[1] < lows[2]:
        return "HL"  # Higher Lows
    # Lower highs = downtrend
    if highs[0] > highs[1] > highs[2]:
        return "LH"  # Lower Highs
    # Consolidation
    if all(abs(lows[i] - lows[i+1]) < (lows[0] * 0.001) for i in range(len(lows)-1)):
        return "CHOP"  # Choppy/consolidation
    
    return None

def rejection_confirmation(m5_candles):
    """Check if current candle is a rejection (wick 2x body, close in lower third)"""
    if len(m5_candles) < 2:
        return False
    
    current = m5_candles[0]
    prev = m5_candles[1]
    
    open_p = float(current["open"])
    close_p = float(current["close"])
    high_p = float(current["high"])
    low_p = float(current["low"])
    
    body = abs(close_p - open_p)
    wick_up = high_p - max(open_p, close_p)
    wick_down = min(open_p, close_p) - low_p
    
    if body == 0:
        return False
    
    # Wick at least 2x body
    upper_wick_reject = wick_up >= body * 2 and close_p < open_p
    lower_wick_reject = wick_down >= body * 2 and close_p > open_p
    
    return upper_wick_reject or lower_wick_reject

def chop_filter(m15_candles):
    """Detect if market is choppy (high alternation, tight VWAP range)"""
    if len(m15_candles) < 3:
        return False
    
    colors = [float(c["close"]) > float(c["open"]) for c in m15_candles[:3]]
    alternating = sum(1 for i in range(len(colors)-1) if colors[i] != colors[i+1])
    
    # If candles alternate more than once in 3 candles, market is choppy
    return alternating >= 2

def volatility_filter(candles):
    """Check if volatility is reasonable (not too low, not too high)"""
    if len(candles) < 10:
        return True
    
    closes = [float(c["close"]) for c in candles[:10]]
    avg_close = sum(closes) / len(closes)
    
    # Calculate ATR approximation
    ranges = []
    for i in range(len(candles[:10])):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        ranges.append(high - low)
    
    atr = sum(ranges) / len(ranges)
    atr_percent = (atr / avg_close) * 100
    
    # Accept if volatility is 0.1% to 1.5% per candle
    return 0.1 < atr_percent < 1.5

def analyze_signal(m5_data, m15_data):
    """Core analysis: all conditions must pass"""
    
    if not m5_data or not m15_data:
        return "NO TRADE", None, None, None, "No data"
    
    current_m5 = m5_data[0]
    current_m15 = m15_data[0]
    
    price = float(current_m5["close"])
    
    # Gate 1: Market Structure
    structure = detect_market_structure(m15_data)
    if structure is None or structure == "CHOP":
        return "NO TRADE", None, None, None, "No clear structure"
    
    # Gate 2: RSI confluence
    rsi_m15 = calc_rsi(m15_data, 7)
    if rsi_m15 is None:
        return "NO TRADE", None, None, None, "Insufficient M15 data"
    
    # Gate 3: Rejection confirmation on M5
    rejection = rejection_confirmation(m5_data)
    if not rejection:
        return "NO TRADE", None, None, None, "No rejection candle"
    
    # Gate 4: Chop filter (ensure not choppy)
    if chop_filter(m15_data):
        return "NO TRADE", None, None, None, "Market too choppy"
    
    # Gate 5: Volatility check
    if not volatility_filter(m5_data):
        return "NO TRADE", None, None, None, "Volatility out of range"
    
    # Gate 6: VWAP alignment
    vwap, upper_band, lower_band = calc_vwap(m5_data)
    if vwap is None:
        return "NO TRADE", None, None, None, "VWAP calc failed"
    
    # Gate 7: EMA trend confirmation
    ema9 = calc_ema(m5_data, 9)
    ema21 = calc_ema(m5_data, 21)
    if ema9 is None or ema21 is None:
        return "NO TRADE", None, None, None, "EMA not available"
    
    # Decision logic
    signal = None
    sl = None
    tp = None
    
    if structure == "HL" and rsi_m15 < 70 and ema9 > ema21 and price > vwap:
        # Uptrend: buy
        signal = "BUY"
        entry = price
        sl = entry - 8
        tp = entry + 15
    elif structure == "LH" and rsi_m15 > 30 and ema9 < ema21 and price < vwap:
        # Downtrend: sell
        signal = "SELL"
        entry = price
        sl = entry + 8
        tp = entry - 15
    else:
        return "NO TRADE", None, None, None, "Structure/RSI/EMA mismatch"
    
    return signal, entry, sl, tp, "ALL GATES PASSED"

def send_message(chat_id, text):
    """Send Telegram message"""
    requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text})

@app.route("/telegram", methods=["POST"])
def handle_update():
    """Handle Telegram webhook"""
    try:
        data = request.get_json()
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")
        
        if not chat_id:
            return {"ok": True}
        
        if text == "/start":
            send_message(chat_id, "🥇 Gold Scalper v2\nSophisticated analysis: RSI + VWAP + Market Structure\nSend /signal")
        
        elif text == "/signal":
            m5_candles = get_xau_data("5min")
            m15_candles = get_xau_data("15min")
            
            signal, entry, sl, tp, reason = analyze_signal(m5_candles, m15_candles)
            
            if signal == "BUY":
                reply = f"🟢 *BUY*\nEntry: ${entry:.2f}\nSL: ${sl:.2f}\nTP: ${tp:.2f}\n\n_Confluence: HL + EMA + RSI + VWAP_"
            elif signal == "SELL":
                reply = f"🔴 *SELL*\nEntry: ${entry:.2f}\nSL: ${sl:.2f}\nTP: ${tp:.2f}\n\n_Confluence: LH + EMA + RSI + VWAP_"
            else:
                reply = f"⚪ NO TRADE\n_{reason}_"
            
            send_message(chat_id, reply)
    
    except Exception as e:
        if 'chat_id' in locals():
            send_message(chat_id, f"⚠️ Error: {str(e)}")
    
    return {"ok": True}

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
