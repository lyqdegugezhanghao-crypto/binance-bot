import os
import asyncio
import time
import pandas as pd
import numpy as np
from fastapi import FastAPI
from binance.um_futures import UMFutures
from ta.volatility import AverageTrueRange
from datetime import datetime
import math

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
SYMBOL = "SOLUSDC"          # 交易对
LEVERAGE = 3                # 杠杆
FIXED_GRID = 1.0            # 关键：A-B-C-D-E 每个点固定相差 1 美元
TIME_STOP = 600             # 10分钟时间止损（从第一次开仓开始计时）

# 美元仓位大小
SIZE_MAIN   = 15   # C点开多
SIZE_HEDGE1 = 30   # D点加空（对冲）
SIZE_HEDGE2 = 55   # 回到C点加多（终极翻倍）

# ===========================================================
app = FastAPI(title="SOL 三角对冲终极实盘版 - 固定1美元网格 + 真实时间止损")

client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

# 全局精度
STEP_SIZE = None
QUANTITY_PRECISION = None

# 核心状态（修复：用 initial 开头表示第一次建仓时间和价格，绝不中途重置）
state = {
    "current_state": "IDLE",          # IDLE / LONG / HEDGE1 / HEDGE2
    "initial_entry_price": None,      # 第一次C点价格（所有点位基于此计算）
    "initial_entry_time": None,       # 第一次开仓时间 → 时间止损专用
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

# 安全向下取整数量
def adjust_qty(qty):
    global STEP_SIZE, QUANTITY_PRECISION
    if STEP_SIZE is None:
        raise Exception("STEP_SIZE 未初始化！")
    if qty < STEP_SIZE:
        return 0
    qty = (int(qty / STEP_SIZE)) * STEP_SIZE
    return round(qty, QUANTITY_PRECISION)

# 市价全平
async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            qty = abs(float(pos["positionAmt"]))
            if qty < STEP_SIZE:
                continue
            side = "SELL" if float(pos["positionAmt"]) > 0 else "BUY"
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty
            )
            log(f"市价全平 | {side} | 数量: {qty}")
    except Exception as e:
        log(f"全平异常: {e}")

# 开仓（限价IOC → 市价补单，带价格/数量精度防护）
async def open_order(side: str, usd_amount: float, price: float):
    try:
        raw_qty = (usd_amount * LEVERAGE) / price
        qty = adjust_qty(raw_qty)
        if qty <= 0:
            log(f"开仓失败：数量取整后为0（原始{raw_qty:.5f}）")
            return

        # 获取tickSize（价格精度）
        tick_size = 0.01
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
        price_rounded = round((price // tick_size) * tick_size, 8)

        # 优先限价IOC
        try:
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="LIMIT",
                quantity=qty,
                price=price_rounded,
                timeInForce="IOC"
            )
            log(f"限价开仓成功 | {side} | 数量: {qty} | 价格: {price_rounded}")
        except Exception as e:
            # 市价补单
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty
            )
            log(f"限价失败→市价补单 | {side} | 数量: {qty} | 市价")
    except Exception as e:
        log(f"开仓异常: {e}")

# ========================== 核心策略循环 ==========================
async def strategy_loop():
    await asyncio.sleep(8)
    log("SOL 三角对冲策略实盘启动成功！固定1美元网格 + 真实10分钟时间止损已就位！")

    while True:
        try:
            price = await get_last_price()

            # ==================== 真实时间止损（从第一次建仓开始计时） ====================
            if state["initial_entry_time"] and (time.time() - state["initial_entry_time"] > TIME_STOP):
                log("【警告】触发10分钟时间止损 → 强平所有仓位！")
                await close_all_market()
                state.update({
                    "current_state": "IDLE",
                    "initial_entry_price": None,
                    "initial_entry_time": None
                })
                await asyncio.sleep(10)
                continue

            # ==================== 状态机（固定1美元网格） ====================
            if state["current_state"] == "IDLE":
                # C点：开多（记录初始价格和时间）
                log(f"C点开多 | 价格 {price:.2f}")
                await open_order("BUY", SIZE_MAIN, price)
                state.update({
                    "current_state": "LONG",
                    "initial_entry_price": price,
                    "initial_entry_time": time.time()   # 只记录一次！
                })

            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                B = C + FIXED_GRID    # C + 1
                D = C - FIXED_GRID    # C - 1

                if price >= B:
                    log(f"到达B点(+1) → 多单止盈，全平")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price <= D:
                    log(f"到达D点(-1) → 开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price)
                    state["current_state"] = "HEDGE1"

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                E = C - 2 * FIXED_GRID   # C - 2

                if price <= E:
                    log(f"到达E点(-2) → 空单止盈，全平结束周期")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price >= C:
                    log(f"价格回到C点 → 加多翻倍！")
                    await open_order("BUY", SIZE_HEDGE2, price)
                    state["current_state"] = "HEDGE2"

            elif state["current_state"] == "HEDGE2":
                C = state["initial_entry_price"]
                B = C + FIXED_GRID       # C + 1

                if price >= B:
                    log(f"回升到B点(+1) → 全部大胜止盈！")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})

            log(f"状态: {state['current_state']} | 价格: {price:.2f} | 距C点: {price - state['initial_entry_price']:.2f} | 已运行: {int(time.time() - (state['initial_entry_time'] or time.time()))}秒")

        except Exception as e:
            log(f"策略异常: {e}")

        await asyncio.sleep(12)

# ========================== 启动精度初始化 ==========================
@app.on_event("startup")
async def startup_event():
    global STEP_SIZE, QUANTITY_PRECISION
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == SYMBOL:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        STEP_SIZE = float(f["stepSize"])
                        QUANTITY_PRECISION = int(round(-math.log10(STEP_SIZE), 0))
                        log(f"【精度初始化成功】{SYMBOL} stepSize={STEP_SIZE}，数量精度 {QUANTITY_PRECISION} 位")
                        break
                break
        if STEP_SIZE is None:
            raise Exception("未能获取合约数量精度")
    except Exception as e:
        log(f"获取合约精度失败: {e}，机器人已停止")
        return

    asyncio.create_task(strategy_loop())

# ========================== HTTP状态接口 ==========================
@app.get("/")
async def root():
    try:
        price = await get_last_price()
    except:
        price = 0
    elapsed = int(time.time() - (state["initial_entry_time"] or time.time())) if state["initial_entry_time"] else 0
    return {
        "msg": "SOL 三角对冲实盘机器人正在运行（固定1美元网格 + 真实10分钟止损）",
        "symbol": SYMBOL,
        "state": state["current_state"],
        "current_price": round(price, 2),
        "C点价格": round(state["initial_entry_price"], 2) if state["initial_entry_price"] else None,
        "已运行秒数": elapsed,
        "距离时间止损还剩": max(0, TIME_STOP - elapsed)
    }

log("策略加载完成，等待FastAPI启动...")
