# === ETHUSD Futures Trading Bot (Delta Exchange Demo)
# === Market Order Entry + Hybrid OCO SL/TP + Trailing SL after Halfway TP ===

from delta_rest_client import DeltaRestClient, OrderType
from datetime import datetime
import ccxt
import pandas as pd
import time
import os

# === USER CONFIGURATION ===
API_KEY = os.getenv("DELTA_API_KEY") or 'your_api_key_here'
API_SECRET = os.getenv("DELTA_API_SECRET") or 'your_api_secret_here'
BASE_URL = 'https://cdn-ind.testnet.deltaex.org'
USD_ASSET_ID = 3
PRODUCT_ID = 1699  # ETHUSD Futures Demo Product ID

# === GLOBAL LOT TRACKING ===
INITIAL_CAPITAL = None
LOT_MULTIPLIER = 1.0

# === AUTHENTICATION ===
def authenticate():
    try:
        client = DeltaRestClient(base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET)
        print("\u2705 Authentication successful.")
        return client
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return None

# === FETCH USD BALANCE ===
def get_usd_balance(client):
    try:
        wallet = client.get_balances(asset_id=USD_ASSET_ID)
        if wallet:
            balance = float(wallet["available_balance"])
            print(f"💰 USD Balance: {balance:.4f} USD")
            return balance
        else:
            print("❌ USD wallet not found.")
            return None
    except Exception as e:
        print(f"❌ Failed to fetch balance: {e}")
        return None

# === SETUP TRADE LOG ===
def setup_trade_log():
    with open("trades_log.txt", "a") as f:
        f.write(f"\n--- New Session Started: {datetime.now()} ---\n")
    print("✅ Trade log file ready.")

# === FETCH CANDLE DATA ===
def fetch_eth_candles(symbol="ETH/USDT", timeframe="1m", limit=100):
    exchange = ccxt.binance()
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"❌ Failed to fetch candle data: {e}")
        return None

# === APPLY STRATEGY ===
def apply_strategy(df):
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema15"] = df["close"].ewm(span=15).mean()
    return df

# === GET SIGNAL ===
def get_trade_signal(df):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    print("\n📊 Strategy Check (Latest Candle):")
    if prev["ema9"] < prev["ema15"] and last["ema9"] > last["ema15"]:
        return "buy"
    elif prev["ema9"] > prev["ema15"] and last["ema9"] < last["ema15"]:
        return "sell"
    return None

# === CANCEL UNFILLED ORDERS ===
def cancel_unfilled_orders(client, product_id):
    open_orders = client.get_live_orders(query={"product_id": product_id})
    for order in open_orders:
        client.cancel_order(product_id=product_id, order_id=order['id'])
        print(f"❌ Cancelled unfilled order ID: {order['id']}")

# === CHECK OPEN POSITION ===
def has_open_position(client, product_id):
    pos = client.get_position(product_id=product_id)
    return pos and float(pos.get("size", 0)) > 0

# === PLACE ORDER + SL/TP ===
def place_order(client, capital, side, product_id):
    global INITIAL_CAPITAL, LOT_MULTIPLIER
    try:
        if INITIAL_CAPITAL is None:
            INITIAL_CAPITAL = capital
            print(f"📌 Initial Capital Set: ${INITIAL_CAPITAL:.2f}")

        # Increase lot size if capital grows
        if capital >= INITIAL_CAPITAL * 1.2:
            LOT_MULTIPLIER *= 1.05
            INITIAL_CAPITAL = capital
            print(f"📈 Lot multiplier increased to {LOT_MULTIPLIER:.2f}")

        BASE_LOT = 1.0
        lot_size = max(round(BASE_LOT * LOT_MULTIPLIER, 2), 1)

        SL_USD = 1.0
        TP_USD = 3.0

        order = client.place_order(
            product_id=product_id,
            size=lot_size,
            side=side,
            order_type=OrderType.MARKET
        )

        entry_price = order.get("average_fill_price") or order.get("limit_price")
        if not entry_price:
            print("❌ Could not determine entry price.")
            return
        entry_price = float(entry_price)

        sl_price = round(entry_price - SL_USD, 2) if side == "buy" else round(entry_price + SL_USD, 2)
        tp_price = round(entry_price + TP_USD, 2) if side == "buy" else round(entry_price - TP_USD, 2)

        # Fetch mark price to validate SL
        ticker = client.get_ticker(str(product_id))
        mark_price = float(ticker["mark_price"])

        if side == "buy" and sl_price >= mark_price:
            sl_price = round(mark_price - 0.5, 2)
        elif side == "sell" and sl_price <= mark_price:
            sl_price = round(mark_price + 0.5, 2)

        client.place_order(
            product_id=product_id,
            size=lot_size,
            side="sell" if side == "buy" else "buy",
            limit_price=tp_price,
            order_type=OrderType.LIMIT
        )
        print(f"🎯 TP placed at {tp_price}")

        client.place_stop_order(
            product_id=product_id,
            size=lot_size,
            side="sell" if side == "buy" else "buy",
            stop_price=sl_price,
            order_type=OrderType.STOP_MARKET
        )
        print(f"🚩 SL placed at {sl_price}")

        with open("trades_log.txt", "a") as f:
            f.write(f"{datetime.now()} | MARKET {side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP: {tp_price} | Lot: {lot_size}\n")

        monitor_trailing_stop(client, product_id, entry_price, side, TP_USD)

    except Exception as e:
        print(f"❌ Failed to place order: {e}")

# === TRAILING SL MONITOR ===
def monitor_trailing_stop(client, product_id, entry_price, side, tp_usd):
    halfway = entry_price + tp_usd / 2 if side == "buy" else entry_price - tp_usd / 2
    trail_distance = tp_usd / 2
    moved_to_be = False

    while True:
        pos = client.get_position(product_id=product_id)
        if not pos or float(pos.get("size", 0)) == 0:
            print("🚪 Position closed.")
            break

        price = float(pos.get("mark_price", 0))
        size = float(pos.get("size"))

        if not moved_to_be:
            if (side == "buy" and price >= halfway) or (side == "sell" and price <= halfway):
                be_price = entry_price
                client.place_stop_order(
                    product_id=product_id,
                    size=size,
                    side="sell" if side == "buy" else "buy",
                    stop_price=be_price,
                    order_type=OrderType.STOP_MARKET
                )
                print(f"🔄 SL moved to BE at {be_price}")
                moved_to_be = True
        else:
            new_sl = round(price - trail_distance, 2) if side == "buy" else round(price + trail_distance, 2)
            client.place_stop_order(
                product_id=product_id,
                size=size,
                side="sell" if side == "buy" else "buy",
                stop_price=new_sl,
                order_type=OrderType.STOP_MARKET
            )
        time.sleep(15)

# === WAIT FOR NEXT CANDLE ===
def wait_until_next_1min():
    now = datetime.now()
    wait_seconds = 60 - now.second
    print(f"🕒 Waiting {wait_seconds}s until next 1m candle...")
    time.sleep(wait_seconds)

# === MAIN LOOP ===
if __name__ == "__main__":
    client = authenticate()
    if client:
        balance = get_usd_balance(client)
        if balance:
            setup_trade_log()
            print("\n🔁 Starting 1m Strategy Loop...")
            while True:
                try:
                    wait_until_next_1min()
                    cancel_unfilled_orders(client, PRODUCT_ID)
                    if has_open_position(client, PRODUCT_ID):
                        print("⏸️ Skipping: already in position.")
                        continue

                    df = fetch_eth_candles()
                    df = apply_strategy(df)
                    signal = get_trade_signal(df)

                    if signal:
                        place_order(client, balance, signal, PRODUCT_ID)
                    else:
                        print("❌ No trade this candle.")
                except KeyboardInterrupt:
                    print("\n🚪 Bot stopped manually.")
                    break
                except Exception as e:
                    print(f"❌ Error: {e}")
                    time.sleep(30)
        else:
            print("⚠️ USD balance fetch failed.")
    else:
        print("⚠️ Auth failed. Exiting.")
