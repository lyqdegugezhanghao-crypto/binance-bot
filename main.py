import os
import asyncio
import time
import pandas as pd
import numpy as np
from fastapi import FastAPI
from binance.um_futures import UMFutures
from ta.volatility import AverageTrueRange
from datetime import datetime

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 交易对
LEVERAGE = 3                # 杠杆
ATR_WINDOW = 14
TIME_STOP = 600             # 10分钟时间止损
STEP_SIZE = 0.1             # SOLUSDC最小下单单位

# 美元仓位
SIZE_MAIN   = 15    # C点开多
SIZE_HEDGE1 = 30    # D点加空
SIZE_HEDGE2 = 55    # 回升C点加多
# ===========================================================

app = FastAPI(title="SOL 三角对冲终极实盘版")
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

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
        "t","open","high","low","close","volume","ct","qv","trades","taker_base","taker_quote","ignore"
    ])
    for col in ["open","high","low","close"]:
        df[col] = df[col].astype(float)
    return df

def adjust_qty(qty):
    return max(round(qty / STEP_SIZE) * STEP_SIZE, STEP_SIZE)

async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            qty = abs(float(pos["positionAmt"]))
            if qty < STEP_SIZE:
                continue
            side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            client.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty
            )
            log(f"市价全平 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"全平异常: {e}")

async def open_order(side: str, usd_amount: float, price: float):
    try:
        qty = adjust_qty((usd_amount * LEVERAGE) / price)
        if qty < STEP_SIZE:
            log("开仓失败：数量太小")
            return

        # 优先限价IOC，失败则市价
        try:
            client.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="LIMIT",
                quantity=qty,
                price=round(price, 1),
                timeInForce="IOC"
            )
            log(f"限价开仓成功 | {side} | 数量: {qty} | 价格: {price:.2f}")
        except:
            client.futures_create_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty
            )
            log(f"限价失败→市价补单 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"开仓异常: {e}")

# ========================== 核心循环 ==========================
async def strategy_loop():
    await asyncio.sleep(8)
    log("SOL 三角对冲策略实盘启动成功！开始狩猎...")

    while True:
        try:
            price = await get_last_price()
            df = await get_klines()
            atr = AverageTrueRange(df["high"], df["low"], df["close"], window=ATR_WINDOW).average_true_range().iloc[-1]
            grid = max(2.0, atr / 2)
            state["grid"] = grid

            # 时间止损
            if state["entry_time"] and (time.time() - state["entry_time"] > TIME_STOP):
                log("触发10分钟时间止损 → 全平")
                await close_all_market()
                state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                await asyncio.sleep(5)
                continue

            # ==================== 状态机 ====================
            if state["current_state"] == "IDLE":
                log("C点开多")
                await open_order("BUY", SIZE_MAIN, price)
                state.update({"current_state": "LONG", "entry_price": price, "entry_time": time.time()})

            elif state["current_state"] == "LONG":
                B = state["entry_price"] + grid
                D = state["entry_price"] - grid
                if price >= B:
                    log("到达B点 → 多单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                elif price <= D:
                    log("到达D点 → 开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price)
                    state.update({"current_state": "HEDGE1", "entry_time": time.time()})

            elif state["current_state"] == "HEDGE1":
                C = state["entry_price"]
                E = state["entry_price"] - 2 * grid
                if price <= E:
                    log("到达E点 → 空单止盈+全平")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                elif price >= C:
                    log("回升C点 → 加多")
                    await open_order("BUY", SIZE_HEDGE2, price)
                    state.update({"current_state": "HEDGE2", "entry_time": time.time()})

            elif state["current_state"] == "HEDGE2":
                B = state["entry_price"] + grid
                if price >= B:
                    log("回升B点 → 全部止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})

            log(f"状态: {state['current_state']} | 价格: {price:.2f} | 网格: {grid:.2f}")

        except Exception as e:
            log(f"策略异常: {e}")

        await asyncio.sleep(12)   # Render免费计划永不被杀的节奏

# ========================== 启动 ==========================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    try:
        price = await get_last_price()
    except:
        price = 0
    return {
        "msg": "SOL 三角对冲实盘机器人正在运行",
        "symbol": SYMBOL,
        "state": state["current_state"],
        "current_price": price,
        "grid": round(state["grid"], 2)
    }

log("策略加载完成，等待启动...")
