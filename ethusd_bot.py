# === ETHUSD Futures Trading Bot (Delta Exchange Demo)
# === Market Order Entry + Hybrid OCO SL/TP + Trailing SL after Halfway TP ===

from delta_rest_client import DeltaRestClient, OrderType
from datetime import datetime
import ccxt
import pandas as pd
import time
import os

# === USER CONFIGURATION ===
API_KEY = os.getenv("DELTA_API_KEY") or 'RzC8BXl98EeFh3i1pOwRAgjqQpLLII'
API_SECRET = os.getenv("DELTA_API_SECRET") or 'yP1encFFWbrPkm5u58ak3qhHD3Eupv9fP5Rf9AmPmi60RHTreYuBdNv1a2bo'
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
    df["ema6"] = df["close"].ewm(span=6).mean()
    df["ema12"] = df["close"].ewm(span=12).mean()
    return df

# === GET SIGNAL ===
def get_trade_signal(df):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    print("\n📊 Strategy Check (Latest Candle):")
    if prev["ema6"] < prev["ema12"] and last["ema6"] > last["ema12"]:
        return "buy"
    elif prev["ema6"] > prev["ema12"] and last["ema6"] < last["ema12"]:
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
    try:
        RISK_PERCENT = 0.10
        SL_PERCENT = 0.01
        TP_MULTIPLIER = 3
        LEVERAGE = 1
        MIN_LOT_SIZE = 1

        # Calculate SL/TP in USD terms
        risk_amount = capital * RISK_PERCENT
        sl_usd = capital * SL_PERCENT
        tp_usd = sl_usd * TP_MULTIPLIER
        raw_lot_size = risk_amount / (sl_usd * LEVERAGE)
        lot_size = max(round(raw_lot_size, 3), MIN_LOT_SIZE)

        # Market entry
        order = client.place_order(
            product_id=product_id,
            size=lot_size,
            side=side,
            order_type=OrderType.MARKET
        )
        entry_price = float(order.get('limit_price') or order.get('average_fill_price'))

        # SL and TP calculation
        sl_price = round(entry_price - sl_usd, 2) if side == "buy" else round(entry_price + sl_usd, 2)
        tp_price = round(entry_price + tp_usd, 2) if side == "buy" else round(entry_price - tp_usd, 2)

        # ✅ Get mark price and ensure SL is not too close
        ticker = client.get_ticker(str(product_id))
        mark_price = float(ticker.get("mark_price", 0))

        if side == "buy" and sl_price >= mark_price - 0.1:
            sl_price = round(mark_price - 1.0, 2)
        elif side == "sell" and sl_price <= mark_price + 0.1:
            sl_price = round(mark_price + 1.0, 2)

        # ✅ Final SL safety check
        if sl_price <= 0:
            print(f"❌ SL price {sl_price} is invalid. Aborting order.")
            return

        # --- Place TP ---
        client.place_order(
            product_id=product_id,
            size=lot_size,
            side="sell" if side == "buy" else "buy",
            limit_price=tp_price,
            order_type=OrderType.LIMIT
        )
        print(f"🎯 TP placed at {tp_price}")

        # --- Place SL (Stop-Market) ---
        client.place_stop_order(
            product_id=product_id,
            size=lot_size,
            side="sell" if side == "buy" else "buy",
            stop_price=sl_price,
            order_type=OrderType.STOP_MARKET
        )
        print(f"🚩 SL placed at {sl_price}")

        # --- Log trade ---
        with open("trades_log.txt", "a") as f:
            f.write(f"{datetime.now()} | MARKET {side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP: {tp_price} | Lot: {lot_size}\n")

        # --- Optional: Monitor position for trailing SL ---
        monitor_trailing_stop(client, product_id, entry_price, side, tp_usd)

    except Exception as e:
        print(f"❌ Failed to place order: {e}")
 def monitor_trailing_stop(client, product_id, entry_price, side, tp_usd):
    halfway = entry_price + tp_usd / 2 if side == "buy" else entry_price - tp_usd / 2
    trail_distance = tp_usd / 2
    moved_to_be = False

    while True:
        try:
            pos = client.get_position(product_id=product_id)
            if not pos or float(pos.get("size", 0)) == 0:
                print("🚪 Position closed.")
                break

            price = float(pos.get("mark_price", 0))
            size = float(pos.get("size"))

            if not moved_to_be:
                if (side == "buy" and price >= halfway) or (side == "sell" and price <= halfway):
                    be_price = round(entry_price, 2)

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

                if (side == "buy" and new_sl >= price) or (side == "sell" and new_sl <= price):
                    print(f"⚠️ Skipping invalid trailing SL: {new_sl} (Current: {price})")
                    time.sleep(10)
                    continue

                client.place_stop_order(
                    product_id=product_id,
                    size=size,
                    side="sell" if side == "buy" else "buy",
                    stop_price=new_sl,
                    order_type=OrderType.STOP_MARKET
                )
                print(f"🔁 Trailing SL updated to {new_sl}")

            time.sleep(15)

        except Exception as e:
            print(f"❌ Error in trailing SL monitor: {e}")
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
