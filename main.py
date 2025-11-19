import os
import asyncio
import time
import pandas as pd
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime
import math

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 交易对
LEVERAGE = 3                # 杠杆
FIXED_GRID = 1.0            # 固定 1 美元网格
TIME_STOP = 600             # 10 分钟时间止损

# 美元仓位
SIZE_MAIN   = 15    # C点开多
SIZE_HEDGE1 = 30    # D点加空对冲
SIZE_HEDGE2 = 55    # 回到C点加多翻倍
# ===========================================================

app = FastAPI(title="SOL 三角对冲实盘版 - 1美元网格 + 真实10分钟硬止损")

client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

# 全局精度
STEP_SIZE = None
QUANTITY_PRECISION = None

# 核心状态
state = {
    "current_state": "IDLE",          # IDLE / LONG / HEDGE1 / HEDGE2
    "initial_entry_price": None,      # 第一次C点价格
    "initial_entry_time": None,       # 第一次开仓时间 → 真实时间止损锚点
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

async def get_last_price():
    ticker = client.mark_price(SYMBOL)
    return float(ticker["markPrice"])

# 安全向下取整数量
def adjust_qty(qty):
    global STEP_SIZE, QUANTITY_PRECISION
    if STEP_SIZE is None:
        raise Exception("STEP_SIZE 未初始化！")
    if qty < STEP_SIZE:
        return 0
    qty = (int(qty / STEP_SIZE)) * STEP_SIZE
    return round(qty, QUANTITY_PRECISION)

# 开仓（必须带 positionSide！）
async def open_order(side: str, usd_amount: float, price: float, position_side: str):
    try:
        raw_qty = (usd_amount * LEVERAGE) / price
        qty = adjust_qty(raw_qty)
        if qty <= 0:
            log(f"开仓失败：数量取整后为0（原始{raw_qty:.5f}）")
            return

        # 获取价格精度 tickSize
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

        common_params = {
            = {
            "symbol": SYMBOL,
            "side": side,
            "quantity": qty,
            "positionSide": position_side           # 关键！双向模式必须加
        }

        # 优先限价IOC
        try:
            client.new_order(
                **common_params,
                type="LIMIT",
                price=price_rounded,
                timeInForce="IOC"
            )
            log(f"限价开仓 | {position_side} | {side} {qty} @ {price_rounded}")
        except Exception:
            client.new_order(**common_params, type="MARKET")
            log(f"市价补单 | {position_side} | {side} {qty} 市价")

    except Exception as e:
        log(f"开仓异常: {e}")

# 市价全平（带 positionSide）
async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            amt = float(pos["positionAmt"])
            qty = abs(amt)
            if qty < STEP_SIZE:
                continue
            side = "SELL" if amt > 0 else "BUY"
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty,
                positionSide=pos["positionSide"]
            )
            log(f"市价平仓 | {pos['positionSide']} | {side} {qty}")
    except Exception as e:
        log(f"全平异常: {e}")

# ========================== 核心策略循环 ==========================
async def strategy_loop():
    await asyncio.sleep(8)
    log("SOL 三角对冲实盘启动成功！1美元固定网格 + 真实10分钟止损已就位！")

    while True:
        try:
            price = await get_last_price()

            # 真实10分钟时间止损（从第一次建仓开始计时）
            if state["initial_entry_time"] and (time.time() - state["initial_entry_time"] > TIME_STOP):
                log("触发10分钟硬止损 → 强平所有仓位！")
                await close_all_market()
                state.update({
                    "current_state": "IDLE",
                    "initial_entry_price": None,
                    "initial_entry_time": None
                })
                await asyncio.sleep(10)
                continue

            # ==================== 状态机 ====================
            if state["current_state"] == "IDLE":
                log(f"C点开多 | 价格 {price:.3f}")
                await open_order("BUY", SIZE_MAIN, price, "LONG")
                state.update({
                    "current_state": "LONG",
                    "initial_entry_price": price,
                    "initial_entry_time": time.time()          # 只记录一次！
                })

            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                B = C + FIXED_GRID      # +1
                D = C - FIXED_GRID      # -1

                if price >= B:
                    log(f"到达B点(+1) → 多单止盈，全平")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price <= D:
                    log(f"到达D点(-1) → 开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                E = C - 2 * FIXED_GRID  # -2

                if price <= E:
                    log(f"到达E点(-2) → 空单止盈，全平结束")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price >= C:
                    log(f"价格回到C点 → 加多翻倍！")
                    await open_order("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"

            elif state["current_state"] == "HEDGE2":
                C = state["initial_entry_price"]
                B = C + FIXED_GRID      # +1

                if price >= B:
                    log(f"回升到B点(+1) → 大胜全平！")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})

            # 运行状态日志
            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            log(f"状态:{state['current_state']:7}  价格:{price:8.3f}  距C:{price-state['initial_entry_price']:+6.3f}  已运行:{elapsed:3d}s")

        except Exception as e:
            log(f"策略异常: {e}")

        await asyncio.sleep(11)

# ========================== 启动时初始化 ==========================
@app.on_event("startup")
async def startup_event():
    global STEP_SIZE, QUANTITY_PRECISION

    # 1. 强制开启对冲模式（保险起见）
    try:
        client.futures_change_position_mode(dualSidePosition=True)
        log("已确认/切换为双向持仓模式")
    except:
        log("双向模式设置已存在")

    # 2. 获取合约精度
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == SYMBOL:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        STEP_SIZE = float(f["stepSize"])
                        QUANTITY_PRECISION = int(round(-math.log10(STEP_SIZE)))
                        log(f"精度初始化成功 → stepSize={STEP_SIZE}，数量保留 {QUANTITY_PRECISION} 位")
                        break
                break
        if STEP_SIZE is None:
            raise Exception("未获取到stepSize")
    except Exception as e:
        log(f"获取精度失败: {e} → 机器人停止")
        return

    asyncio.create_task(strategy_loop())

# ========================== HTTP监控接口 ==========================
@app.get("/")
async def root():
    try:
        price = await get_last_price()
    except:
        price = 0
    elapsed = int(time.time() - (state["initial_entry_time"] or time.time())) if state["initial_entry_time"] else 0
    remain = max(0, TIME_STOP - elapsed)
    return {
        "msg": "SOL 1美元三角对冲实盘机器人运行中",
        "状态": state["current_state"],
        "当前价": round(price, 3),
        "C点价": round(state["initial_entry_price"], 3) if state["initial_entry_price"] else None,
        "距C点": round(price - (state["initial_entry_price"] or price), 3),
        "已运行秒": elapsed,
        "距时间止损": f"{remain}s ({remain//60}分{remain%60}秒)"
    }

log("策略加载完成，等待启动...")
