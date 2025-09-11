import os
import time
import pandas as pd
import ccxt
from datetime import datetime
from dotenv import load_dotenv
from delta_rest_client import DeltaRestClient, OrderType

# === Load environment variables ===
load_dotenv()

# === USER CONFIGURATION from .env ===
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")
BASE_URL = os.getenv("DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org")
USD_ASSET_ID = int(os.getenv("USD_ASSET_ID", "3"))
PRODUCT_ID = int(os.getenv("PRODUCT_ID", "1699"))

LOT_SIZE = int(os.getenv("LOT_SIZE", "1"))
SL_PERCENT = float(os.getenv("SL_PERCENT", "0.01"))
TP_MULTIPLIER = float(os.getenv("TP_MULTIPLIER", "2"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "4"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "50"))
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"

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
    df["ema13"] = df["close"].ewm(span=13).mean()
    df["ema23"] = df["close"].ewm(span=23).mean()
    return df

def get_trade_signal(df):
    last = df.iloc[-1]
    second_last = df.iloc[-2]

    print("\n📊 Strategy Check (Latest Candle):")

    if second_last["ema13"] <= second_last["ema23"] and last["ema13"] >= last["ema23"]:
        print("✅ Buy signal detected.")
        return "buy"
    elif second_last["ema13"] >= second_last["ema23"] and last["ema13"] <= last["ema23"]:
        print("✅ Sell signal detected.")
        return "sell"

    print("❌ No trade this candle.")
    return None

# === CANCEL UNFILLED ORDERS ===
def cancel_unfilled_orders(client, product_id):
    try:
        open_orders = client.get_live_orders(query={"product_id": product_id})
        for order in open_orders:
            client.cancel_order(product_id=product_id, order_id=order['id'])
            print(f"❌ Cancelled unfilled order ID: {order['id']}")
    except Exception as e:
        print(f"⚠️ Error cancelling orders: {e}")

# === CHECK OPEN POSITION ===
def has_open_position(client, product_id):
    try:
        pos = client.get_position(product_id=product_id)
        return pos and float(pos.get("size", 0)) > 0
    except Exception as e:
        print(f"⚠️ Error checking position: {e}")
        return False

# === PLACE ORDER + SL/TP ===
def place_order(client, side, product_id):
    try:
        order = client.place_order(
            product_id=product_id,
            size=LOT_SIZE,
            side=side,
            order_type=OrderType.MARKET
        )

        entry_price = float(order.get('limit_price') or order.get('average_fill_price'))
        if entry_price <= 0:
            print("❌ Invalid entry price. Skipping.")
            return

        sl_distance = entry_price * SL_PERCENT
        tp_distance = sl_distance * TP_MULTIPLIER

        sl_price = round(entry_price - sl_distance, 2) if side == "buy" else round(entry_price + sl_distance, 2)
        tp_price = round(entry_price + tp_distance, 2) if side == "buy" else round(entry_price - tp_distance, 2)

        if sl_price <= 0 or tp_price <= 0:
            print("❌ Invalid SL/TP. Skipping.")
            return

        print(f"📌 Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}, Lot: {LOT_SIZE}")

        client.place_order(
            product_id=product_id,
            size=LOT_SIZE,
            side="sell" if side == "buy" else "buy",
            limit_price=tp_price,
            order_type=OrderType.LIMIT
        )
        print(f"🎯 TP placed at {tp_price}")

        client.place_stop_order(
            product_id=product_id,
            size=LOT_SIZE,
            side="sell" if side == "buy" else "buy",
            stop_price=sl_price,
            order_type=OrderType.MARKET
        )
        print(f"🚩 SL placed at {sl_price}")

    except Exception as e:
        print(f"❌ Failed to place order: {e}")

# === WAIT UNTIL NEXT CANDLE ===
def wait_until_next_candle():
    now = datetime.now()
    wait_seconds = 60 - now.second
    print(f"🕒 Waiting {wait_seconds}s until next candle...")
    time.sleep(wait_seconds)

# === MAIN LOOP ===
def main():
    client = authenticate()
    if not client:
        print("⚠️ Auth failed. Exiting.")
        return

    balance = get_usd_balance(client)
    if not balance:
        print("⚠️ Balance fetch failed.")
        return

    print("\n🔁 Starting Strategy Loop...")
    while True:
        try:
            wait_until_next_candle()
            cancel_unfilled_orders(client, PRODUCT_ID)

            if has_open_position(client, PRODUCT_ID):
                print("⏸️ Skipping: already in position.")
                continue

            df = fetch_eth_candles(timeframe=TIMEFRAME)
            if df is None:
                continue

            df = apply_strategy(df)
            signal = get_trade_signal(df)
            if signal:
                place_order(client, signal, PRODUCT_ID)
            else:
                print("❌ No trade this candle.")

        except KeyboardInterrupt:
            print("\n🚪 Bot stopped manually.")
            break
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
