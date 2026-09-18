import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ============================================================
# НАСТРОЙКИ (CONFIG)
# ============================================================
PAPER_BALANCE = 20.00          
TRADE_AMOUNT = 5.00            

PAIRS_TO_SCAN = [
    {"symbol": "XXBTZUSD", "name": "BTC/USDT"},
    {"symbol": "XETHZUSD", "name": "ETH/USDT"},
    {"symbol": "SOLUSD",   "name": "SOL/USDT"},
    {"symbol": "XXRPZUSD", "name": "XRP/USDT"},
    {"symbol": "ADAUSD",   "name": "ADA/USDT"},
    {"symbol": "DOTUSD",   "name": "DOT/USDT"},
    {"symbol": "LTCUSD",   "name": "LTC/USDT"},
    {"symbol": "POLUSD", "name": "POL/USDT"},
    {"symbol": "LINKUSD",  "name": "LINK/USDT"},
    {"symbol": "UNIUSD",   "name": "UNI/USDT"},
    {"symbol": "AVAXUSD",  "name": "AVAX/USDT"},
    {"symbol": "XLMUSD",   "name": "XLM/USDT"}
]

CHECK_INTERVAL_SEC = 50        
STATE_FILE = "paper_state.json"

TELEGRAM_TOKEN = ""  
CHAT_ID = ""         

def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "cash": PAPER_BALANCE,
        "position": None,
        "stats": {"total": 0, "win": 0, "loss": 0}
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print("Ошибка сохранения состояния:", e)

state = load_state()

def api_get_monitored(path, params=None):
    """Запрос к API с проверкой задержки (ping/latency) и ошибок"""
    start_time = time.time()
    try:
        url = "https://api.kraken.com" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "crypto-ultimate-bot/8.0"})
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Предупреждение о высокой задержке
            if elapsed_ms > 1500:
                print(f"⚠️ [ЗАДЕРЖКА СЕТИ] Kraken ответил за {elapsed_ms:.0f}ms!")

            data = json.loads(resp.read().decode())
            if data.get("error") and len(data["error"]) > 0:
                print(f"❌ Ошибка API Kraken: {data['error']}")
                return None
            return data.get("result")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Ошибка {e.code}: Возможно превышен лимит запросов.")
        return None
    except Exception as e:
        print(f"❌ Сетевая ошибка соединения: {e}")
        return None

def get_candles_validated(pair_symbol, interval):
    """Загрузка свечей с валидацией структуры и свежести данных"""
    res = api_get_monitored("/0/public/OHLC", {"pair": pair_symbol, "interval": interval})
    if not res:
        return []
    
    pair_key = [k for k in res.keys() if k != "last"][0]
    candles = res.get(pair_key, [])
    
    if not candles or len(candles) < 2:
        return []
        
    # Проверка актуальности данных (свежесть последней свечи)
    last_candle_time = int(candles[-1][0])
    current_time = int(datetime.now(timezone.utc).timestamp())
    delay_sec = current_time - last_candle_time

    # Если последняя свеча отстает больше чем на 3 минуты (180 сек)
    if delay_sec > 180 and interval == 1:
        print(f"⚠️ [УСТАРЕВШИЕ ДАННЫЕ] Свечи {pair_symbol} отстают на {delay_sec} сек.")
        return []

    return candles

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = 0, 0
    for i in range(-period, 0):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))

def analyze_market(pair_symbol):
    candles_1m = get_candles_validated(pair_symbol, 1)
    time.sleep(0.35)  # Защита от банов Rate Limit
    
    candles_1h = get_candles_validated(pair_symbol, 60)
    time.sleep(0.35)

    if len(candles_1m) < 40 or len(candles_1h) < 10:
        return "HOLD", 50, 0, 50, 0, 0

    closes_1m = [float(c[4]) for c in candles_1m]
    volumes_1m = [float(c[6]) for c in candles_1m]
    current_price = closes_1m[-1]

    closes_1h = [float(c[4]) for c in candles_1h]
    ma24_1h = sum(closes_1h[-24:]) / min(24, len(closes_1h))
    global_trend_up = closes_1h[-1] > ma24_1h

    ma20_1m = sum(closes_1m[-20:]) / 20
    rsi_1m = calculate_rsi(closes_1m)
    avg_vol = sum(volumes_1m[-15:]) / 15

    last_c = candles_1m[-2]
    o_p, h_p, l_p, c_p, v_p = float(last_c[1]), float(last_c[2]), float(last_c[3]), float(last_c[4]), float(last_c[6])
    body = c_p - o_p

    score = 50

    if global_trend_up: score += 15
    else: score -= 15

    if current_price > ma20_1m: score += 15
    else: score -= 15

    if v_p > avg_vol * 1.3:
        if body > 0: score += 15
        else: score -= 15

    if rsi_1m < 32: score += 20
    elif rsi_1m > 68: score -= 20

    probability = max(10, min(95, score))

    if probability >= 72:
        signal = "BUY"
    elif probability <= 28:
        signal = "SELL"
    else:
        signal = "HOLD"

    return signal, probability, current_price, rsi_1m, ma20_1m, v_p

def run_bot():
    global state
    print("=" * 60)
    print("🤖 Бот с системой диагностики задержек и качества данных запущен...")
    print("=" * 60)
    send_telegram("🚀 Торговый бот с проверкой связи запущен в облаке!")

    consecutive_errors = 0

    while True:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        position = state["position"]
        
        if position is not None:
            active_pair_symbol = position["symbol"]
            active_pair_name = position["name"]
            candles = get_candles_validated(active_pair_symbol, 1)
            
            if candles:
                consecutive_errors = 0
                price = float(candles[-1][4])
                entry = position["entry_price"]
                pnl_pct = ((price - entry) / entry) * 100
                print(f"[{now_str}] 📌 Позиция: {active_pair_name} | Вход: {entry:.2f} | PnL: {pnl_pct:+.2f}%")
                
                if pnl_pct >= 1.3 or pnl_pct <= -0.8:
                    result_usd = position["amount"] * (pnl_pct / 100)
                    state["cash"] += position["amount"] + result_usd
                    state["stats"]["total"] += 1
                    
                    if pnl_pct > 0:
                        state["stats"]["win"] += 1
                        msg = f"✅ УСПЕХ ({active_pair_name}): +{result_usd:.2f} USD"
                    else:
                        state["stats"]["loss"] += 1
                        msg = f"❌ УБЫТОК ({active_pair_name}): {result_usd:.2f} USD"
                        
                    print(msg)
                    send_telegram(msg)
                    state["position"] = None
                    save_state(state)
            else:
                consecutive_errors += 1
                print(f"⚠️ Не удалось подгрузить активную позицию ({consecutive_errors}/5)")
        else:
            best_pair = None
            best_prob = 0
            best_price = 0

            for p in PAIRS_TO_SCAN:
                signal, prob, price, rsi, ma20, vol = analyze_market(p["symbol"])
                
                if price > 0:
                    consecutive_errors = 0
                    print(f"[{now_str}] Скан {p['name']}: Сигнал={signal} ({prob}%) | RSI={rsi:.1f}")
                    if signal == "BUY" and prob > best_prob:
                        best_pair = p
                        best_prob = prob
                        best_price = price
                else:
                    consecutive_errors += 1
                    print(f"⚠️ [СБОЙ] Не удалось подгрузить данные по {p['name']}")

            if best_pair and best_prob >= 72 and state["cash"] >= TRADE_AMOUNT:
                state["position"] = {
                    "symbol": best_pair["symbol"],
                    "name": best_pair["name"],
                    "entry_price": best_price,
                    "amount": TRADE_AMOUNT,
                }
                state["cash"] -= TRADE_AMOUNT
                msg = f"🟢 ВХОД: LONG по {best_pair['name']} по цене {best_price:.2f} USD (Уверенность: {best_prob}%)"
                print(msg)
                send_telegram(msg)
                save_state(state)

        # Защита от частых сбоев сети
        if consecutive_errors >= 5:
            print("❌ Слишком много ошибок сети подряд. Пауза 15 секунд для восстановления...")
            time.sleep(15)
            consecutive_errors = 0

        print(f"💰 Баланс: {state['cash']:.2f} USD | Сделок: {state['stats']['total']} (Побед: {state['stats']['win']})")
        print("-" * 60)
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    run_bot()
