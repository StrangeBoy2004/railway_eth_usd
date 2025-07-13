# === ETHUSD Futures Trading Bot (Delta Exchange Demo)
# === Market Order Entry + Hybrid OCO SL/TP + Trailing SL after Halfway TP ===

from delta_rest_client import DeltaRestClient, OrderType
from datetime import datetime, timedelta
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
import requests

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
        # Attempt to fetch public IP to help user debug IP whitelist issues
        try:
            ip = requests.get("https://api.ipify.org").text
            print(f"🌐 Your current IP: {ip}")
            print("🔐 Make sure this IP is whitelisted in your Delta Exchange API settings.")
        except:
            print("⚠️ Unable to fetch public IP for whitelist suggestion.")
        
        print(f"❌ Failed to fetch balance: {e}")
        return None


# === SETUP TRADE LOG ===
def setup_trade_log():
    with open("trades_log.txt", "a") as f:
        f.write(f"\n--- New Session Started: {datetime.now()} ---\n")
    print("✅ Trade log file ready.")

# === FETCH CANDLE DATA ===
def fetch_eth_candles(symbol="ETH/USDT", timeframe="15m", limit=100):
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
    last = df.iloc[-1]
    second_last = df.iloc[-2]

    print("\n📊 Strategy Check (Latest Candle):")
    
    # Buy signal: EMA6 just crossed above EMA12 or is equal (touching)
    if second_last["ema6"] <= second_last["ema12"] and last["ema6"] >= last["ema12"]:
        print("✅ Buy signal detected.")
        return "buy"
    
    # Sell signal: EMA6 just crossed below EMA12 or is equal
    elif second_last["ema6"] >= second_last["ema12"] and last["ema6"] <= last["ema12"]:
        print("✅ Sell signal detected.")
        return "sell"
    print(f"🧮 EMA6: prev={second_last['ema6']:.2f}, last={last['ema6']:.2f}")
    print(f"🧮 EMA12: prev={second_last['ema12']:.2f}, last={last['ema12']:.2f}")
    return None
# === CANCEL UNFILLED ORDERS ===
def cancel_unfilled_orders(client, product_id):
    try:
        open_orders = client.get_live_orders(query={"product_id": product_id})
        for order in open_orders:
            order_id = order.get("id")
            if not order_id:
                continue  # Skip if order ID is missing

            try:
                client.cancel_order(product_id=product_id, order_id=order_id)
                print(f"❌ Cancelled unfilled order ID: {order_id}")
            except Exception as e:
                if "open_order_not_found" in str(e):
                    print(f"⚠️ Order {order_id} already filled or not found.")
                else:
                    print(f"⚠️ Failed to cancel order {order_id}: {e}")
    except Exception as e:
        print(f"❌ Failed to fetch live orders: {e}")


# === CHECK OPEN POSITION ===
def has_open_position(client, product_id):
    pos = client.get_position(product_id=product_id)
    return pos and float(pos.get("size", 0)) > 0

# === CLOSE ALL POSITIONS + ORDERS SAFELY ===
def safe_cancel_open_position_and_orders(client, product_id):
    try:
        print("⚠️ Attempting cleanup of open orders and positions...")

        # ✅ Cancel all open orders safely
        open_orders = client.get_live_orders(query={"product_id": product_id})
        for order in open_orders:
            order_id = order.get("id")
            if order_id:
                try:
                    client.cancel_order(product_id=product_id, order_id=order_id)
                    print(f"❌ Cancelled unfilled order ID: {order_id}")
                except Exception as cancel_err:
                    print(f"⚠️ Failed to cancel order ID {order_id}: {cancel_err}")
            else:
                print(f"⚠️ Skipping malformed order (missing 'id'): {order}")

        # ✅ Close position safely if any
        pos = client.get_position(product_id=product_id)
        if pos and float(pos.get("size", 0)) > 0:
            side = pos.get("side")
            size = float(pos.get("size", 0))
            if side and size > 0:
                exit_side = "sell" if side == "buy" else "buy"
                try:
                    client.place_order(
                        product_id=product_id,
                        size=size,
                        side=exit_side,
                        order_type=OrderType.MARKET
                    )
                    print(f"✅ Position closed using market order (size={size}, side={exit_side})")
                except Exception as close_err:
                    print(f"❌ Failed to close position: {close_err}")
            else:
                print(f"⚠️ Position exists but missing side or size: {pos}")
        else:
            print("✅ No open position to close.")

    except Exception as e:
        print(f"🚨 Failed during emergency cleanup: {e}")

# === PLACE ORDER FUNCTION ===
def place_order(client, capital, side, product_id):
    try:
        LOT_SIZE = 1
        SL_PERCENT = 0.01         # 1% SL
        TP_MULTIPLIER = 2         # 1:2 RR
        LEVERAGE = 5

        # === Set leverage ===
        client.set_leverage(product_id=product_id, leverage=LEVERAGE)

        # === Market Entry ===
        order = client.place_order(
            product_id=product_id,
            size=LOT_SIZE,
            side=side,
            order_type=OrderType.MARKET
        )

        entry_price = float(order.get('average_fill_price') or order.get('limit_price'))
        if not entry_price or entry_price < 100:
            print(f"❌ Invalid entry price: {entry_price}. Skipping.")
            return

        sl_distance = entry_price * SL_PERCENT
        tp_distance = sl_distance * TP_MULTIPLIER

        sl_price = round(entry_price - sl_distance, 2) if side == "buy" else round(entry_price + sl_distance, 2)
        tp_price = round(entry_price + tp_distance, 2) if side == "buy" else round(entry_price - tp_distance, 2)

        if sl_price <= 0 or tp_price <= 0:
            print(f"❌ Invalid SL ({sl_price}) or TP ({tp_price}). Skipping.")
            return

        print(f"📌 Entry: {entry_price}, SL: {sl_price}, TP: {tp_price}, Lot: {LOT_SIZE}")

        # === TP (Limit) ===
        client.place_order(
            product_id=product_id,
            size=LOT_SIZE,
            side="sell" if side == "buy" else "buy",
            limit_price=tp_price,
            order_type=OrderType.LIMIT
        )
        print(f"🎯 TP placed at {tp_price}")

        # === SL (Stop-Market) ===
        client.place_stop_order(
            product_id=product_id,
            size=LOT_SIZE,
            side="sell" if side == "buy" else "buy",
            stop_price=sl_price,
            order_type=OrderType.MARKET
        )
        print(f"🚩 SL placed at {sl_price} (STOP_MARKET)")

        # === Log ===
        with open("trades_log.txt", "a") as f:
            f.write(f"{datetime.now()} | {side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP: {tp_price} | Lot: {LOT_SIZE} | Leverage: {LEVERAGE}\n")

        # === Monitor SL ===
        monitor_trailing_stop(client, product_id, entry_price, side, tp_distance)

    except Exception as e:
        print(f"❌ Failed to place order: {e}")


# === MONITOR TRAILING SL ===
def monitor_trailing_stop(client, product_id, entry_price, side, tp_usd):
    halfway = entry_price + tp_usd / 2 if side == "buy" else entry_price - tp_usd / 2
    trail_distance = tp_usd / 2
    moved_to_be = False
    last_sl_price = None

    while True:
        try:
            pos = client.get_position(product_id=product_id)

            # 🛡️ Fallback if data missing or malformed
            if not pos or "mark_price" not in pos or "size" not in pos:
                print("⚠️ Position data missing or malformed. Retrying...")
                time.sleep(10)
                continue

            price = float(pos.get("mark_price", 0))
            size = float(pos.get("size", 0))

            # 🛡️ Guard against invalid data (very important)
            if price <= 1 or size <= 0 or size == -1.0:
                print(f"⚠️ Invalid price ({price}) or size ({size}). Skipping SL update.")
                time.sleep(10)
                continue

            # 🧮 Break-Even logic
            if not moved_to_be:
                if (side == "buy" and price >= halfway) or (side == "sell" and price <= halfway):
                    be_price = round(entry_price, 2)
                    client.place_stop_order(
                        product_id=product_id,
                        size=size,
                        side="sell" if side == "buy" else "buy",
                        stop_price=be_price,
                        order_type=OrderType.MARKET
                    )
                    print(f"🔄 SL moved to Break-Even at {be_price}")
                    moved_to_be = True
                    last_sl_price = be_price

            # 🔁 Trailing SL logic
            elif moved_to_be:
                new_sl = round(price - trail_distance, 2) if side == "buy" else round(price + trail_distance, 2)

                if new_sl <= 1 or new_sl == last_sl_price:
                    print(f"⚠️ Skipping duplicate or invalid SL update: {new_sl}")
                    time.sleep(10)
                    continue

                if (side == "buy" and new_sl >= price) or (side == "sell" and new_sl <= price):
                    print(f"⚠️ Skipping logically invalid SL: {new_sl} vs price: {price}")
                    time.sleep(10)
                    continue

                client.place_stop_order(
                    product_id=product_id,
                    size=size,
                    side="sell" if side == "buy" else "buy",
                    stop_price=new_sl,
                    order_type=OrderType.MARKET
                )
                print(f"🔁 Trailing SL updated to {new_sl}")
                last_sl_price = new_sl

            time.sleep(15)

        except Exception as e:
            print(f"❌ Error in trailing SL monitor: {e}")
            if "unavailable" in str(e) or "502" in str(e):
                print("🔁 Retrying after temporary API error...")
            time.sleep(15)



# === WAIT FOR NEXT CANDLE ===
def wait_until_next_15min():
    now = datetime.utcnow()
    next_15min = (now + timedelta(minutes=15 - now.minute % 15)).replace(second=15, microsecond=0)
    wait_seconds = (next_15min - now).total_seconds()
    print(f"🕒 Waiting {int(wait_seconds)}s until next 15m candle closes...")
    time.sleep(wait_seconds)

# === MAIN LOOP ===
if __name__ == "__main__":
    client = authenticate()
    if client:
        balance = get_usd_balance(client)
        if balance:
            setup_trade_log()
            print("\n🔁 Starting 5m Strategy Loop...")
            while True:
                try:
                    wait_until_next_15min()
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
                    if "502" in str(e) or "Bad Gateway" in str(e) or "ConnectionError" in str(e):
                        safe_cancel_open_position_and_orders(client, PRODUCT_ID)
                    time.sleep(30)
        else:
            print("⚠️ USD balance fetch failed.")
    else:
        print("⚠️ Auth failed. Exiting.")
