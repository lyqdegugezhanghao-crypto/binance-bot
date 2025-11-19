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
# 配置区（全部安全读取环境变量）
# ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
SYMBOL = "SOLUSDC"                  # 交易对
LEVERAGE = 3                        # 杠杆
ATR_WINDOW = 14
TIME_STOP = 600                     # 10分钟时间止损
STEP_SIZE = 0.1                     # SOLUSDC 最小交易单位

# 固定美元仓位
SIZE_MAIN   = 15    # C点开多
SIZE_HEDGE1 = 30    # D点加空
SIZE_HEDGE2 = 55    # 回升C点加多

# ==========================
# 初始化
# ==========================
app = FastAPI(title="SOL 三角对冲实盘版 - Render 永久运行")
client = UMFutures(key=API_KEY, secret=API_SECRET)

# 策略状态
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
    df["close"] = df["close"].astype(float)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    return df

def adjust_qty(qty):
    return max(np.floor(qty / STEP_SIZE) * STEP_SIZE, STEP_SIZE)

async def close_all_market():
    try:
        pos_info = client.get_position_risk(symbol=SYMBOL)
        for pos in pos_info:
            qty = abs(float(pos["positionAmt"]))
            if qty < STEP_SIZE:
                continue
            side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            client.create_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)
            log(f"市价平仓成功 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"平仓异常: {e}")

async def open_order(side: str, usd_amount: float, price: float):
    try:
        qty = adjust_qty((usd_amount * LEVERAGE) / price)
        if qty < STEP_SIZE:
            log("开仓失败：数量小于最小单位")
            return

        # 优先限价 IOC，失败则市价补单
        try:
            client.create_order(
                symbol=SYMBOL,
                side=side,
                type="LIMIT",
                quantity=qty,
                price=round(price, 1),
                timeInForce="IOC"
            )
            log(f"限价开仓成功 | {side} | 数量: {qty} | 价格: {price:.2f}")
        except:
            client.create_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty)
            log(f"限价失败 → 市价补单 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"开仓异常: {e}")

# ==========================
# 核心策略循环（Render 完美兼容）
# ==========================
async def strategy_loop():
    await asyncio.sleep(8)  # 启动缓冲
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
                log("触发10分钟时间止损 → 全部平仓")
                await close_all_market()
                state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                await asyncio.sleep(5)
                continue

            # ====================== 状态机 ======================
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
                    log("到达E点 → 空单止盈 + 全平")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})
                elif price >= C:
                    log("回升C点 → 加多")
                    await open_order("BUY", SIZE_HEDGE2, price)
                    state.update({"current_state": "HEDGE2", "entry_time": time.time()})

            elif state["current_state"] == "HEDGE2":
                B = state["entry_price"] + grid
                if price >= B:
                    log("回升B点 → 全部止盈出局")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "entry_price": None, "entry_time": None})

            log(f"状态: {state['current_state']} | 价格: {price:.2f} | 网格: {grid:.2f} | 仓位状态已更新")

        except Exception as e:
            log(f"策略异常: {e}")

        await asyncio.sleep(12)   # 关键！12秒一轮，Render 免费计划永不被杀

# ==========================
# 启动 + 健康检查接口
# ==========================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    price = await get_last_price()
    return {
        "msg": "SOL 三角对冲策略正在实盘运行",
        "symbol": SYMBOL,
        "state": state["current_state"],
        "price": price,
        "grid": round(state["grid"], 2)
    }

log("策略程序加载完成，等待启动...")
