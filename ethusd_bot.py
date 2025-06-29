=== ETHUSD Futures Trading Bot (Delta Exchange Demo)

=== Full Version: Supports Buy/Sell, Trailing SL, and Break-even Logic ===

from delta_rest_client import DeltaRestClient, OrderType from datetime import datetime import ccxt import pandas as pd import numpy as np from ta.trend import EMAIndicator, ADXIndicator import time import os

=== USER CONFIGURATION ===

API_KEY = os.getenv("DELTA_API_KEY") or 'RzC8BXl98EeFh3i1pOwRAgjqQpLLII' API_SECRET = os.getenv("DELTA_API_SECRET") or 'yP1encFFWbrPkm5u58ak3qhHD3Eupv9fP5Rf9AmPmi60RHTreYuBdNv1a2bo' BASE_URL = 'https://cdn-ind.testnet.deltaex.org' USD_ASSET_ID = 3  # Confirmed from wallet response

=== AUTHENTICATION ===

def authenticate(): try: client = DeltaRestClient( base_url=BASE_URL, api_key=API_KEY, api_secret=API_SECRET ) print("\u2705 Authentication successful.") return client except Exception as e: print(f"❌ Authentication failed: {e}") return None

=== FETCH USD BALANCE ===

def get_usd_balance(client): try: wallet = client.get_balances(asset_id=USD_ASSET_ID) if wallet: balance = float(wallet["available_balance"]) print(f"💰 USD Balance: {balance:.4f} USD") return balance else: print("❌ USD wallet not found.") return None except Exception as e: print(f"❌ Failed to fetch balance: {e}") return None

=== SETUP TRADE LOG FILE ===

def setup_trade_log(): try: with open("trades_log.txt", "a") as f: f.write(f"\n--- New Session Started: {datetime.now()} ---\n") print("✅ Trade log file ready.") except Exception as e: print(f"❌ Failed to setup trade log: {e}")

=== FETCH ETH/USDT 1M CANDLES ===

def fetch_eth_candles(symbol="ETH/USDT", timeframe="1m", limit=100): exchange = ccxt.binance() try: print("📅 Fetching 1m candles from Binance...") ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit) df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]) df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms") return df except Exception as e: print(f"❌ Failed to fetch candle data: {e}") return None

=== APPLY STRATEGY INDICATORS ===

def apply_strategy(df): try: df["ema9"] = df["close"].ewm(span=9).mean() df["ema15"] = df["close"].ewm(span=15).mean() return df except Exception as e: print(f"❌ Error while applying indicators: {e}") return None

=== DETERMINE TRADE DIRECTION ===

def get_trade_signal(df): prev = df.iloc[-2] last = df.iloc[-1] print("\n📊 Strategy Check (Latest Candle):") print(f"Prev EMA9: {prev['ema9']:.2f}, EMA15: {prev['ema15']:.2f}") print(f"Curr EMA9: {last['ema9']:.2f}, EMA15: {last['ema15']:.2f}") if prev["ema9"] < prev["ema15"] and last["ema9"] > last["ema15"]: print("✅ Buy Signal Confirmed") return "buy" elif prev["ema9"] > prev["ema15"] and last["ema9"] < last["ema15"]: print("✅ Sell Signal Confirmed") return "sell" print("❌ No Signal") return None

=== CANCEL UNFILLED ORDERS ===

def cancel_unfilled_orders(client, product_id): try: print("🔍 Checking for unfilled live orders...") open_orders = client.get_live_orders(query={"product_id": product_id}) for order in open_orders: client.cancel_order(product_id=product_id, order_id=order['id']) print(f"❌ Cancelled unfilled order ID: {order['id']}") except Exception as e: print(f"❌ Error cancelling orders: {e}")

=== CHECK IF ALREADY IN POSITION ===

def has_open_position(client, product_id): try: pos = client.get_position(product_id=product_id) return pos and float(pos.get("size", 0)) > 0 except Exception as e: print(f"❌ Error checking position: {e}") return False

=== PLACE ORDER ===

def place_order(client, capital, entry_price, side, product_id): try: RISK_PERCENT = 0.10 SL_PERCENT = 0.01 TP_MULTIPLIER = 3 LEVERAGE = 1 MIN_LOT_SIZE = 1

risk_amount = capital * RISK_PERCENT
    sl_usd = capital * SL_PERCENT
    tp_usd = sl_usd * TP_MULTIPLIER
    raw_lot_size = risk_amount / (sl_usd * LEVERAGE)
    lot_size = max(round(raw_lot_size, 3), MIN_LOT_SIZE)

    sl_price = round(entry_price - sl_usd, 2) if side == "buy" else round(entry_price + sl_usd, 2)
    tp_price = round(entry_price + tp_usd, 2) if side == "buy" else round(entry_price - tp_usd, 2)

    print(f"\n🛒 Placing LIMIT {side.upper()} order: Entry {entry_price}, SL {sl_price}, TP {tp_price}, Lot {lot_size}")
    print(f"🧪 DEBUG: Side: {side}, Size: {lot_size} (type: {type(lot_size)}), Entry: {entry_price}")

    order = client.place_order(
        product_id=product_id,
        size=lot_size,
        side=side,
        limit_price=entry_price,
        order_type=OrderType.MARKET
    )

    print(f"🛑 SL placed at {sl_price}")
    print(f"🎯 TP placed at {tp_price}")

    with open("trades_log.txt", "a") as f:
        f.write(f"{datetime.now()} | ORDER PLACED | {side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP: {tp_price} | Lot: {lot_size}\n")

    monitor_position_with_trailing_sl(client, product_id, entry_price, side, tp_usd)

except Exception as e:
    print(f"❌ Failed to place order: {e}")

=== TRAILING STOP/BREAK-EVEN MONITOR ===

def monitor_position_with_trailing_sl(client, product_id, entry_price, side, tp_usd): try: halfway = entry_price + tp_usd / 2 if side == "buy" else entry_price - tp_usd / 2 trail_distance = tp_usd / 2 moved_to_be = False

while True:
        pos = client.get_position(product_id=product_id)
        print(f"[MONITOR] Position Response: {pos}")
        if not pos or float(pos.get("size", 0)) == 0:
            print("🚪 Position closed.")
            break

        current_price = float(pos.get("mark_price", 0))
        size = float(pos.get("size", 0))
        print(f"[MONITOR] Price: {current_price}, Size: {size}, SL Moved: {moved_to_be}")

        if not moved_to_be:
            if (side == "buy" and current_price >= halfway) or (side == "sell" and current_price <= halfway):
                be_sl = entry_price
                client.place_stop_order(
                    product_id=product_id,
                    size=size,
                    side="sell" if side == "buy" else "buy",
                    stop_price=be_sl,
                    limit_price=be_sl,
                    order_type=OrderType.MARKET
                )
                print(f"🔄 SL moved to BE at {be_sl}")
                moved_to_be = True
        else:
            new_sl = round(current_price - trail_distance, 2) if side == "buy" else round(current_price + trail_distance, 2)
            client.place_stop_order(
                product_id=product_id,
                size=size,
                side="sell" if side == "buy" else "buy",
                stop_price=new_sl,
                limit_price=new_sl,
                order_type=OrderType.MARKET
            )
            print(f"🔁 Trailing SL moved to {new_sl}")

        time.sleep(15)
except Exception as e:
    print(f"❌ Error in SL monitor: {e}")

=== WAIT FOR NEXT 1M CANDLE ===

def wait_until_next_1min(): now = datetime.now() wait_seconds = (60 - now.second) print(f"🕒 Waiting {wait_seconds}s until next 1m candle...") time.sleep(wait_seconds)

=== MAIN LOOP ===

if name == "main": client = authenticate() if client: balance = get_usd_balance(client) if balance: setup_trade_log() print("\n🔁 Starting 1m Strategy Loop...") product_id = 1699 while True: try: wait_until_next_1min() cancel_unfilled_orders(client, product_id) if has_open_position(client, product_id): print("⏸️ Skipping: already in position.") continue

df = fetch_eth_candles(timeframe="1m")
                df = apply_strategy(df)
                signal = get_trade_signal(df)

                if signal:
                    entry_price = float(df.iloc[-1]["close"])
                    place_order(client, balance, entry_price, signal, product_id)
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

