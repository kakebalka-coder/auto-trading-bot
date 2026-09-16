import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

PAPER_BALANCE = 20.00

# Риск на одну сделку: 1% виртуального капитала
RISK_PER_TRADE = 0.01

# При маленьком балансе держим максимум одну позицию
MAX_POSITIONS = 1

# Максимальная доля капитала, которую можно использовать
MAX_POSITION_PCT = 0.25

# Комиссия для расчётов
FEE = 0.0026

# Таймфрейм Kraken
INTERVAL = 15

# Как часто сканировать рынок
CHECK_EVERY = 60

# Минимальный объём в USD за 24 часа
MIN_24H_VOLUME_USD = 100000

# Минимальная оценка сигнала
MIN_SCORE = 5

# После закрытия пары не входить в неё сразу снова
COOLDOWN_MINUTES = 30

# Ограничение дневного убытка
MAX_DAILY_LOSS_PCT = 0.03

# Сколько последних свечей использовать
CANDLE_LIMIT = 250

STATE_FILE = "paper_state.json"


# ============================================================
# STATE
# ============================================================

cash = PAPER_BALANCE
positions = {}
trades = []
cooldowns = {}

day_start_balance = PAPER_BALANCE
current_day = datetime.now(timezone.utc).date().isoformat()


# ============================================================
# KRAKEN API
# ============================================================

def api_get(path, params=None):
    try:
        if params:
            query = urllib.parse.urlencode(params)
            url = "https://api.kraken.com" + path + "?" + query
        else:
            url = "https://api.kraken.com" + path

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "paper-trading-bot/2.0"}
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode())

        if data.get("error"):
            print("Kraken API error:", data["error"])
            return None

        return data.get("result")

    except Exception as e:
        print("API error:", e)
        return None


def get_pairs():
    result = api_get("/0/public/AssetPairs")

    if not result:
        return []

    pairs = []

    for key, info in result.items():

        if ".d" in key.lower():
            continue

        wsname = info.get("wsname", "")
        altname = info.get("altname", "")

        if not wsname or not altname:
            continue

        # Только пары с USD
        if not wsname.endswith("/USD"):
            continue

        base, quote = wsname.split("/")

        # Не рассматриваем стейблкоины как отдельные торговые идеи
        stablecoins = {
            "USDT", "USDC", "DAI", "PYUSD", "USDE",
            "TUSD", "USDS", "RLUSD"
        }

        if base in stablecoins:
            continue

        pairs.append({
            "key": key,
            "altname": altname,
            "wsname": wsname
        })

    return pairs


def get_ohlc(pair):
    result = api_get(
        "/0/public/OHLC",
        {
            "pair": pair,
            "interval": INTERVAL
        }
    )

    if not result:
        return []

    keys = [x for x in result.keys() if x != "last"]

    if not keys:
        return []

    candles = result[keys[0]]

    # Последняя свеча ещё формируется — не используем её
    candles = candles[:-1]

    return candles[-CANDLE_LIMIT:]


def get_ticker(pair):
    result = api_get(
        "/0/public/Ticker",
        {"pair": pair}
    )

    if not result:
        return None

    keys = list(result.keys())

    if not keys:
        return None

    data = result[keys[0]]

    try:
        return {
            "price": float(data["c"][0]),
            "volume": float(data["v"][1]),
            "high": float(data["h"][1]),
            "low": float(data["l"][1])
        }
    except Exception:
        return None


# ============================================================
# INDICATORS
# ============================================================

def closes(candles):
    return [float(x[4]) for x in candles]


def highs(candles):
    return [float(x[2]) for x in candles]


def lows(candles):
    return [float(x[3]) for x in candles]


def volumes(candles):
    return [float(x[6]) for x in candles]


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (price - result) * multiplier + result

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(tr)

    return sum(trs[-period:]) / period


def volume_ratio(candles, period=20):
    vols = volumes(candles)

    if len(vols) < period + 1:
        return None

    average = sum(vols[-period - 1:-1]) / period

    if average <= 0:
        return None

    return vols[-1] / average


# ============================================================
# SIGNAL
# ============================================================

def analyze(pair, candles, ticker):
    if len(candles) < 210:
        return None

    prices = closes(candles)

    price = ticker["price"]

    ema20 = ema(prices, 20)
    ema50 = ema(prices, 50)
    ema200 = ema(prices, 200)

    rsi_value = rsi(prices, 14)
    atr_value = atr(candles, 14)
    vol_ratio = volume_ratio(candles, 20)

    if None in (ema20, ema50, ema200, rsi_value, atr_value, vol_ratio):
        return None

    score = 0
    reasons = []

    # Основной тренд
    if price > ema200:
        score += 2
        reasons.append("выше EMA200")

    # Среднесрочный тренд
    if ema20 > ema50:
        score += 2
        reasons.append("EMA20 > EMA50")

    # Цена относительно EMA20
    if price > ema20:
        score += 1
        reasons.append("цена выше EMA20")

    # RSI
    if 50 <= rsi_value <= 68:
        score += 2
        reasons.append("RSI подтверждает импульс")

    # Объём
    if vol_ratio >= 1.20:
        score += 1
        reasons.append("повышенный объём")

    # Не покупаем слишком перекупленный рынок
    if rsi_value > 72:
        score -= 3
        reasons.append("RSI слишком высокий")

    # ATR не должен быть практически нулевым
    atr_pct = atr_value / price

    if atr_pct < 0.002:
        score -= 1
        reasons.append("слишком низкая волатильность")

    return {
        "pair": pair,
        "price": price,
        "score": score,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi_value,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "volume_ratio": vol_ratio,
        "reasons": reasons
    }


# ============================================================
# RISK MANAGEMENT
# ============================================================

def calculate_position(signal):
    global cash

    price = signal["price"]
    atr_value = signal["atr"]

    # ATR-based stop
    stop_distance = atr_value * 1.5

    if stop_distance <= 0:
        return 0, 0, 0

    stop_price = price - stop_distance

    if stop_price <= 0:
        return 0, 0, 0

    # Риск деньгами
    risk_money = cash * RISK_PER_TRADE

    # Количество монет исходя из риска
    quantity_by_risk = risk_money / stop_distance

    # Максимальная позиция
    max_position_money = cash * MAX_POSITION_PCT

    quantity_by_balance = max_position_money / price

    quantity = min(
        quantity_by_risk,
        quantity_by_balance
    )

    position_value = quantity * price

    # Для очень маленького баланса
    if position_value < 1:
        return 0, 0, 0

    # Take Profit = 2R
    take_price = price + stop_distance * 2

    return quantity, stop_price, take_price


# ============================================================
# PAPER TRADING
# ============================================================

def buy(signal):
    global cash

    pair = signal["pair"]

    if pair in positions:
        return

    if len(positions) >= MAX_POSITIONS:
        return

    if cooldown_active(pair):
        return

    quantity, stop_price, take_price = calculate_position(signal)

    if quantity <= 0:
        return

    price = signal["price"]

    cost = quantity * price
    fee = cost * FEE

    total_cost = cost + fee

    if total_cost > cash:
        return

    cash -= total_cost

    positions[pair] = {
        "pair": pair,
        "quantity": quantity,
        "entry": price,
        "stop": stop_price,
        "take": take_price,
        "time": datetime.now(timezone.utc).isoformat(),
        "score": signal["score"]
    }

    print()
    print("🟢 PAPER BUY")
    print("Pair:", pair)
    print("Price:", round(price, 6))
    print("Amount USD:", round(cost, 4))
    print("Score:", signal["score"])
    print("RSI:", round(signal["rsi"], 2))
    print("Stop:", round(stop_price, 6))
    print("Take:", round(take_price, 6))


def sell(pair, price, reason):
    global cash

    position = positions.get(pair)

    if not position:
        return

    quantity = position["quantity"]
    entry = position["entry"]

    gross = quantity * price
    fee = gross * FEE

    received = gross - fee

    invested = quantity * entry
    entry_fee = invested * FEE

    pnl = received - invested - entry_fee

    cash += received

    trades.append({
        "pair": pair,
        "entry": entry,
        "exit": price,
        "pnl": pnl,
        "reason": reason,
        "time": datetime.now(timezone.utc).isoformat()
    })

    del positions[pair]

    cooldowns[pair] = time.time() + COOLDOWN_MINUTES * 60

    print()
    print("🔴 PAPER SELL")
    print("Pair:", pair)
    print("Price:", round(price, 6))
    print("Reason:", reason)
    print("PnL:", round(pnl, 4), "USD")


def check_positions():
    for pair in list(positions.keys()):

        ticker = get_ticker(pair)

        if not ticker:
            continue

        price = ticker["price"]

        position = positions[pair]

        if price <= position["stop"]:
            sell(pair, price, "Stop-Loss")

        elif price >= position["take"]:
            sell(pair, price, "Take-Profit")


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(pair):
    until = cooldowns.get(pair)

    if not until:
        return False

    if time.time() >= until:
        del cooldowns[pair]
        return False

    return True


# ============================================================
# BALANCE / DAILY RISK
# ============================================================

def total_balance():
    total = cash

    for pair, position in positions.items():

        ticker = get_ticker(pair)

        if ticker:
            total += position["quantity"] * ticker["price"]

    return total


def check_new_day():
    global current_day
    global day_start_balance

    today = datetime.now(timezone.utc).date().isoformat()

    if today != current_day:
        current_day = today
        day_start_balance = total_balance()


def daily_loss_limit_reached():
    balance = total_balance()

    if day_start_balance <= 0:
        return False

    loss_pct = (day_start_balance - balance) / day_start_balance

    return loss_pct >= MAX_DAILY_LOSS_PCT


# ============================================================
# STATE
# ============================================================

def save_state():
    data = {
        "cash": cash,
        "positions": positions,
        "trades": trades[-200:],
        "cooldowns": cooldowns,
        "day_start_balance": day_start_balance,
        "current_day": current_day
    }

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    except Exception as e:
        print("State save error:", e)


def load_state():
    global cash
    global positions
    global trades
    global cooldowns
    global day_start_balance
    global current_day

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        cash = float(data.get("cash", PAPER_BALANCE))
        positions = data.get("positions", {})
        trades = data.get("trades", [])
        cooldowns = data.get("cooldowns", {})
        day_start_balance = float(
            data.get("day_start_balance", PAPER_BALANCE)
        )
        current_day = data.get(
            "current_day",
            datetime.now(timezone.utc).date().isoformat()
        )

        print("💾 Состояние восстановлено.")

    except Exception as e:
        print("State load error:", e)


# ============================================================
# STATUS
# ============================================================

def print_status(signals_checked):
    balance = total_balance()

    pnl = balance - PAPER_BALANCE

    print()
    print("=" * 60)
    print("🤖 PAPER TRADING BOT v2")
    print("=" * 60)

    print(
        "Время:",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    print("Баланс:", round(balance, 4), "USD")
    print("Свободные:", round(cash, 4), "USD")
    print("P/L:", round(pnl, 4), "USD")
    print("Проверено пар:", signals_checked)
    print("Открытых позиций:", len(positions))
    print("Всего сделок:", len(trades))

    if trades:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] <= 0)

        print("Прибыльных:", wins)
        print("Убыточных:", losses)

    if positions:
        print()
        print("ОТКРЫТЫЕ ПОЗИЦИИ:")

        for pair, position in positions.items():
            print(
                pair,
                "| entry:",
                round(position["entry"], 6),
                "| stop:",
                round(position["stop"], 6),
                "| take:",
                round(position["take"], 6)
            )

    print("=" * 60)


# ============================================================
# MAIN SCANNER
# ============================================================

def run():

    global day_start_balance

    print()
    print("🚀 BOT v2 ЗАПУЩЕН")
    print("РЕЖИМ: PAPER TRADING")
    print("РЕАЛЬНЫЕ ДЕНЬГИ НЕ ИСПОЛЬЗУЮТСЯ")
    print()

    load_state()

    if not os.path.exists(STATE_FILE):
        day_start_balance = PAPER_BALANCE

    while True:

        try:

            check_new_day()

            # Проверяем существующие позиции
            check_positions()

            # Дневной лимит
            if daily_loss_limit_reached():

                print()
                print("🛑 ДНЕВНОЙ ЛИМИТ УБЫТКА ДОСТИГНУТ.")
                print("Новые сделки временно запрещены.")

                save_state()

                time.sleep(CHECK_EVERY)

                continue

            pairs = get_pairs()

            print()
            print("🔎 Найдено USD-пар:", len(pairs))

            best_signal = None
            checked = 0

            for pair_info in pairs:

                pair = pair_info["altname"]

                try:

                    ticker = get_ticker(pair)

                    if not ticker:
                        continue

                    # Оборот в USD приблизительно
                    volume_usd = ticker["volume"] * ticker["price"]

                    if volume_usd < MIN_24H_VOLUME_USD:
                        continue

                    candles = get_ohlc(pair)

                    if not candles:
                        continue

                    signal = analyze(
                        pair,
                        candles,
                        ticker
                    )

                    checked += 1

                    if not signal:
                        continue

                    if signal["score"] < MIN_SCORE:
                        continue

                    if pair in positions:
                        continue

                    if cooldown_active(pair):
                        continue

                    if (
                        best_signal is None
                        or signal["score"] > best_signal["score"]
                    ):
                        best_signal = signal

                except Exception as e:

                    print(
                        "Ошибка анализа",
                        pair,
                        ":",
                        e
                    )

            if best_signal:

                print()
                print("⭐ ЛУЧШИЙ СИГНАЛ")
                print(
                    best_signal["pair"],
                    "| score:",
                    best_signal["score"],
                    "| RSI:",
                    round(best_signal["rsi"], 2)
                )

                print(
                    "Причины:",
                    ", ".join(best_signal["reasons"])
                )

                buy(best_signal)

            else:

                print()
                print("⏳ Подходящего сигнала нет.")

            print_status(checked)

            save_state()

            print()
            print(
                "Следующая проверка через",
                CHECK_EVERY,
                "секунд."
            )

            time.sleep(CHECK_EVERY)

        except Exception as e:

            print()
            print("⚠️ Ошибка главного цикла:", e)

            time.sleep(CHECK_EVERY)


if __name__ == "__main__":

    try:
        run()

    except KeyboardInterrupt:

        print()
        print("🛑 Бот остановлен.")
        save_state()
