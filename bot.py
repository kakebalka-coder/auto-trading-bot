import json
import time
import urllib.request
from datetime import datetime

# =========================
# НАСТРОЙКИ PAPER TRADING
# =========================

START_BALANCE = 20.0       # виртуальный баланс
FEE = 0.0026               # примерная комиссия 0.26%

PAIR = "XBTUSD"
INTERVAL = 15              # свечи по 15 минут

FAST_MA = 20
SLOW_MA = 50

STOP_LOSS = 0.01           # 1%
TAKE_PROFIT = 0.02         # 2%

CHECK_EVERY = 60           # проверять рынок раз в минуту


# =========================
# СОСТОЯНИЕ БОТА
# =========================

cash = START_BALANCE
btc = 0.0

entry_price = 0.0
stop_price = 0.0
take_price = 0.0

trades = 0
wins = 0
losses = 0


# =========================
# ПОЛУЧЕНИЕ ДАННЫХ KRAKEN
# =========================

def get_prices():

    url = (
        "https://api.kraken.com/0/public/OHLC"
        f"?pair={PAIR}&interval={INTERVAL}"
    )

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "paper-trading-bot/1.0"}
        )

        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode())

        if data.get("error"):
            print("Ошибка Kraken:", data["error"])
            return []

        result = data["result"]

        pair_key = [x for x in result.keys() if x != "last"][0]

        candles = result[pair_key]

        # Последняя свеча может быть ещё незакрытой.
        candles = candles[:-1]

        prices = [float(c[4]) for c in candles]

        return prices

    except Exception as e:
        print("Ошибка получения данных:", e)
        return []


# =========================
# СКОЛЬЗЯЩАЯ СРЕДНЯЯ
# =========================

def moving_average(values, period):

    if len(values) < period:
        return None

    return sum(values[-period:]) / period


# =========================
# СТАТУС
# =========================

def show_status(price):

    total = cash + btc * price

    print()
    print("=" * 45)
    print("🤖 PAPER TRADING BOT")
    print("=" * 45)

    print("Время:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("Цена BTC:", round(price, 2), "USD")
    print("Баланс:", round(total, 2), "USD")
    print("Свободные деньги:", round(cash, 2), "USD")
    print("BTC:", round(btc, 8))

    if entry_price > 0:
        print("Цена входа:", round(entry_price, 2))
        print("Stop-Loss:", round(stop_price, 2))
        print("Take-Profit:", round(take_price, 2))
        print("Позиция: ОТКРЫТА")
    else:
        print("Позиция: НЕТ")

    print("Сделок:", trades)
    print("Прибыльных:", wins)
    print("Убыточных:", losses)

    print("=" * 45)


# =========================
# ПОКУПКА
# =========================

def buy(price):

    global cash, btc
    global entry_price, stop_price, take_price
    global trades

    # Используем максимум 25% доступного капитала.
    amount_usd = cash * 0.25

    if amount_usd < 1:
        return

    fee = amount_usd * FEE

    btc_bought = (amount_usd - fee) / price

    cash -= amount_usd
    btc += btc_bought

    entry_price = price

    stop_price = price * (1 - STOP_LOSS)
    take_price = price * (1 + TAKE_PROFIT)

    trades += 1

    print()
    print("🟢 ПОКУПКА")
    print("Цена:", round(price, 2))
    print("Сумма:", round(amount_usd, 2))
    print("Stop-Loss:", round(stop_price, 2))
    print("Take-Profit:", round(take_price, 2))


# =========================
# ПРОДАЖА
# =========================

def sell(price, reason):

    global cash, btc
    global entry_price, stop_price, take_price
    global wins, losses

    if btc <= 0:
        return

    amount_usd = btc * price

    fee = amount_usd * FEE

    received = amount_usd - fee

    invested = btc * entry_price

    profit = received - invested

    cash += received
    btc = 0.0

    print()
    print("🔴 ПРОДАЖА")
    print("Цена:", round(price, 2))
    print("Причина:", reason)
    print("Результат:", round(profit, 4), "USD")

    if profit >= 0:
        wins += 1
        print("✅ Сделка прибыльная")
    else:
        losses += 1
        print("❌ Сделка убыточная")

    entry_price = 0
    stop_price = 0
    take_price = 0


# =========================
# ОСНОВНОЙ ЦИКЛ
# =========================

def run():

    print()
    print("🚀 БОТ ЗАПУЩЕН")
    print("Режим: PAPER TRADING")
    print("Реальные деньги НЕ используются.")
    print()

    while True:

        prices = get_prices()

        if len(prices) < SLOW_MA:
            print("Недостаточно данных...")
            time.sleep(CHECK_EVERY)
            continue

        price = prices[-1]

        fast = moving_average(prices, FAST_MA)
        slow = moving_average(prices, SLOW_MA)

        show_status(price)

        print(
            "MA20:",
            round(fast, 2),
            "| MA50:",
            round(slow, 2)
        )

        # =====================
        # УПРАВЛЕНИЕ ОТКРЫТОЙ ПОЗИЦИЕЙ
        # =====================

        if btc > 0:

            if price <= stop_price:
                sell(price, "Stop-Loss")

            elif price >= take_price:
                sell(price, "Take-Profit")

            elif fast < slow:
                sell(price, "Тренд изменился")


        # =====================
        # ПОИСК ПОКУПКИ
        # =====================

        else:

            if fast > slow:
                buy(price)

            else:
                print("⏳ Сигнала на покупку нет.")


        print()
        print("Следующая проверка через", CHECK_EVERY, "секунд.")

        time.sleep(CHECK_EVERY)


# =========================
# ЗАПУСК
# =========================

if __name__ == "__main__":

    try:
        run()

    except KeyboardInterrupt:

        print()
        print("🛑 Бот остановлен.")

        final_price_data = get_prices()

        if final_price_data:
            final_price = final_price_data[-1]
            total = cash + btc * final_price
            print("Итоговый виртуальный баланс:", round(total, 2), "USD")
