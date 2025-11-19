import os
import asyncio
import time
import pandas as pd
import numpy as np
from fastapi import FastAPI
from binance.um_futures import UMFutures
from ta.volatility import AverageTrueRange
from datetime import datetime
import math  # ← 新增：用来算精度

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
SYMBOL = "SOLUSDC"          # 交易对
LEVERAGE = 3                # 杠杆
ATR_WINDOW = 14
TIME_STOP = 600             # 10分钟时间止损

# 美元仓位
SIZE_MAIN = 15              # C点开多
SIZE_HEDGE1 = 30            # D点加空
SIZE_HEDGE2 = 55            # 回升C点加多
# ===========================================================

app = FastAPI(title="SOL 三角对冲终极实盘版")
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

# ← 修复点1：动态获取真实精度，不再硬编码
STEP_SIZE = None            # 启动时自动获取
QUANTITY_PRECISION = None   # 小数位数

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

# ← 修复点2：全新防炸弹 adjust_qty（向下取整，最安全）
def adjust_qty(qty):
    global STEP_SIZE, QUANTITY_PRECISION
    if STEP_SIZE is None:
        raise Exception("STEP_SIZE 未初始化！")
    # 至少要能下最小单
    if qty < STEP_SIZE:
        return 0
    # 严格向下取整到 stepSize（绝不会超精度）
    qty = (int(qty / STEP_SIZE)) * STEP_SIZE
    return round(qty, QUANTITY_PRECISION)

async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            qty = abs(float(pos["positionAmt"]))
            if qty < STEP_SIZE:
                continue
            side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            params = {
                "symbol": SYMBOL,
                "side": side,
                "type": "MARKET",
                "quantity": qty
            }
            client.new_order(**params)
            log(f"市价全平 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"全平异常: {e}")

async def open_order(side: str, usd_amount: float, price: float):
    try:
        raw_qty = (usd_amount * LEVERAGE) / price
        qty = adjust_qty(raw_qty)
        if qty <= 0:
            log(f"开仓失败：数量取整后为0（原始{raw_qty:.5f}）")
            return

        # ← 修复点3：价格也按 tickSize 圆整，防止价格精度超
        # 先获取价格精度（顺便缓存）
        tick_size = 0.01  # SOLUSDC 当前是 0.01
        try:
            info = client.exchange_info()["symbols"]
            for s in info:
                if s["symbol"] == SYMBOL:
                    for f in s["filters"]:
                        if f["filterType"] == "PRICE_FILTER":
                            tick_size = float(f["tickSize"])
                    break
        except:
            pass
        price_rounded = round(price // tick_size * tick_size, 8)

        # 优先限价IOC
        try:
            params = {
                "symbol": SYMBOL,
                "side": side,
                "type": "LIMIT",
                "quantity": qty,
                "price": price_rounded,
                "timeInForce": "IOC"
            }
            client.new_order(**params)
            log(f"限价开仓成功 | {side} | 数量: {qty} | 价格: {price_rounded}")
        except Exception as e:
            # 市价补单
            params = {
                "symbol": SYMBOL,
                "side": side,
                "type": "MARKET",
                "quantity": qty
            }
            client.new_order(**params)
            log(f"限价失败→市价补单 | {side} | 数量: {qty} | 价格: 市价")
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
        await asyncio.sleep(12)

# ========================== 启动 ==========================
@app.on_event("startup")
async def startup_event():
    global STEP_SIZE, QUANTITY_PRECISION
    # ← 修复点4：启动时自动获取真实 stepSize（核心！）
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == SYMBOL:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        STEP_SIZE = float(f["stepSize"])
                        QUANTITY_PRECISION = int(round(-math.log10(STEP_SIZE)))
                        log(f"【精度初始化成功】{SYMBOL} stepSize={STEP_SIZE}，精度保留 {QUANTITY_PRECISION} 位小数")
                        break
                break
        if STEP_SIZE is None:
            raise Exception("未获取到合约精度")
    except Exception as e:
        log(f"获取合约精度失败: {e}，机器人停止运行")
        return

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
