import os
import asyncio
import time
import pandas as pd
import numpy as np
from fastapi import FastAPI
from binance.um_futures import UMFutures
from ta.volatility import AverageTrueRange
from datetime import datetime

# ==========================
# 配置区（全部从环境变量读取，更安全！）
# ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
SYMBOL = "SOLUSDC"                  # 交易对
LEVERAGE = 3                        # 杠杆
TOTAL_USD = 100                     # 总资金（仅用于日志参考）
ATR_WINDOW = 14
TIME_STOP = 600                     # 时间止损 10 分钟
STEP_SIZE = 0.1                     # SOLUSDC 最小下单单位 0.1

# 固定仓位（美元）
SIZE_MAIN = 15      # C点开多
SIZE_HEDGE1 = 30    # D点加空
SIZE_HEDGE2 = 55    # 回升C点加多

# ==========================
# 初始化
# ==========================
app = FastAPI(title="SOL 三角对冲策略 - Render 实盘版")
client = UMFutures(key=API_KEY, secret=API_SECRET)

# 策略状态（全局）
state = {
    "current_state": "IDLE",
    "entry_price": None,
    "entry_time": None,
    "grid": 2.0
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

async def get_last_price():
    ticker = client.mark_price(SYMBOL)
    return float(ticker["markPrice"])

async def get_klines(limit=50):
    klines = client.klines(symbol=SYMBOL, interval="1m", limit=limit)
    df = pd.DataFrame(klines, columns=[
        "t","open","high","low","close","volume","close_time","quote_volume",
        "trades","taker_base","taker_quote","ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df

def adjust_qty(qty):
    return max(np.floor(qty / STEP_SIZE) * STEP_SIZE, STEP_SIZE)

async def close_all_market():
    try:
        pos = client.get_position_risk(symbol=SYMBOL)[0]
        qty = abs(float(pos["positionAmt"]))
        if qty < STEP_SIZE:
            return
        side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
        client.futures_create_order(
            symbol=SYMBOL, side=side, type="MARKET", quantity=qty
        )
        log(f"平仓成功 | 方向: {side} | 数量: {qty}")
    except Exception as e:
        log(f"平仓失败: {e}")

async def open_order(side: str, usd_amount: float, price: float):
    try:
        qty = adjust_qty((usd_amount * LEVERAGE) / price)
        if qty < STEP_SIZE:
            log("开仓失败：数量过小")
            return
        # 先尝试限价 IOC，失败就市价
        try:
            client.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="LIMIT",
                quantity=qty,
                price=round(price, 1),
                timeInForce="IOC"
            )
            log(f"开仓成功 | {side} | 数量: {qty} | 价格: {price:.2f} | 杠杆: {LEVERAGE}x")
        except:
            client.futures_create_order(
                symbol=SYMBOL, side=side, type="MARKET", quantity=qty
            )
            log(f"限价失败，市价补单 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"开仓失败: {e}")

# ==========================
# 核心策略循环（Render 完美兼容）
# ==========================
async def strategy_loop():
    await asyncio.sleep(5)  # 启动延时
    log("SOL 三角对冲策略已启动！实盘运行中...")
    
    while True:
        try:
            price = await get_last_price()
            df = await get_klines()
            atr = AverageTrueRange(df["high"], df["low"], df["close"], window=ATR_WINDOW).average_true_range().iloc[-1]
            grid = max(2.0, atr / 2)
            state["grid"] = grid

            # 时间止损
            if state["entry_time"] and (time.time() - state["entry_time"] > TIME_STOP):
                log("触发时间止损，全部平仓")
                await close_all_market()
                state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                await asyncio.sleep(3)
                continue

            # 状态机
            if state["current_state"] == "IDLE":
                log("C点开多")
                await open_order("BUY", SIZE_MAIN, price)
                state.update({"current_state": "LONG", "entry_price": price, "entry_time": time.time()})

            elif state["current_state"] == "LONG":
                B = state["entry_price"] + grid
                D = state["entry_price"] - grid
                if price >= B:
                    log("到B点，多单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None})
                elif price <= D:
                    log("到D点，开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price)
                    state.update({"current_state": "HEDGE1", "entry_time": time.time()})

            elif state["current_state"] == "HEDGE1":
                C = state["entry_price"]
                E = state["entry_price"] - 2 * grid
                if price <= E:
                    log("到E点，空单止盈 + 平多，全部出局")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None})
                elif price >= C:
                    log("回升C点，加多")
                    await open_order("BUY", SIZE_HEDGE2, price)
                    state.update({"current_state": "HEDGE2", "entry_time": time.time()})

            elif state["current_state"] == "HEDGE2":
                B = state["entry_price"] + grid
                if price >= B:
                    log("回升B点，全部止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None})

            log(f"当前状态: {state['current_state']} | 价格: {price:.2f} | 网格: {grid:.2f}")

        except Exception as e:
            log(f"策略异常: {e}")

        await asyncio.sleep(3)  # 主循环频率

# ==========================
# 启动策略
# ==========================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    return {
        "strategy": "SOL 三角对冲实盘运行中",
        "symbol": SYMBOL,
        "state": state["current_state"],
        "price": await get_last_price(),
        "url": "https://binance-bot-1-u6ze.onrender.com"
    }
