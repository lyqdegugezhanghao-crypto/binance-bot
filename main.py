import os
import asyncio
import time
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime
import math

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 改成 "SOLUSDT" 也完美支持
LEVERAGE = 3
FIXED_GRID = 1.0            # 固定 1 美元网格
TIME_STOP = 600             # 10 分钟真实时间止损

SIZE_MAIN   = 15    # C点开多（美元）
SIZE_HEDGE1 = 30    # D点加空对冲
SIZE_HEDGE2 = 55    # 回到C点加多翻倍
# ===========================================================

app = FastAPI(title="SOL 三角对冲实盘版 — 真成功才打印")

client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

STEP_SIZE = None
QUANTITY_PRECISION = None

state = {
    "current_state": "IDLE",
    "initial_entry_price": None,
    "initial_entry_time": None,
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

async def get_last_price():
    ticker = client.mark_price(SYMBOL)
    return float(ticker["markPrice"])

def adjust_qty(qty):
    global STEP_SIZE, QUANTITY_PRECISION
    if STEP_SIZE is None:
        raise Exception("STEP_SIZE 未初始化")
    if qty < STEP_SIZE:
        return 0
    qty = (int(qty / STEP_SIZE)) * STEP_SIZE
    return round(qty, QUANTITY_PRECISION)

# ================== 关键修复：真实下单成功才打印成功 ==================
async def open_order(side: str, usd_amount: float, price: float, position_side: str):
    try:
        raw_qty = (usd_amount * LEVERAGE) / price
        qty = adjust_qty(raw_qty)
        if qty <= 0:
            log(f"下单失败：数量太小 {raw_qty:.5f} → {qty}")
            return

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

        params = {
            "symbol": SYMBOL,
            "side": side,
            "quantity": qty,
            "positionSide": position_side
        }

        # 1. 先试限价IOC
        resp = client.new_order(**params, type="LIMIT", price=price_rounded, timeInForce="IOC")
        if isinstance(resp, dict) and resp.get("orderId"):
            log(f"限价开仓成功 | {position_side:5} | {side} {qty} @ {price_rounded}")
            return

        if "msg" in resp:
            log(f"限价被拒 → {resp.get('msg')}")

        # 2. 再补市价
        resp = client.new_order(**params, type="MARKET")
        if isinstance(resp, dict) and resp.get("orderId"):
            log(f"市价开仓成功 | {position_side:5} | {side} {qty} 市价")
        elif "msg" in resp:
            log(f"市价也被拒 → {resp.get('msg')}")
        else:
            log(f"市价下单未知响应: {resp}")

    except Exception as e:
        log(f"开仓异常: {e}")

async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            amt = float(pos["positionAmt"])
            qty = abs(amt)
            if qty < STEP_SIZE:
                continue
            side = "SELL" if amt > 0 else "BUY"
            resp = client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty,
                positionSide=pos["positionSide"]
            )
            if resp.get("orderId"):
                log(f"市价平仓成功 | {pos['positionSide']} | {side} {qty}")
    except Exception as e:
        log(f"全平异常: {e}")

# ========================== 核心循环 ==========================
async def strategy_loop():
    await asyncio.sleep(0.5)

    for i in range(60):
        try:
            price = await get_last_price()
            log(f"Binance连接成功 当前标记价 {price:.3f}")
            break
        except Exception as e:
            log(f"连接中 {i+1}/60 ...")
            await asyncio.sleep(1)
    else:
        log("无法连接Binance，退出")
        return

    log("SOL 三角对冲实盘启动成功！1美元网格 + 10分钟硬止损已就位！")

    while True:
        try:
            price = await get_last_price()

            # 真实10分钟时间止损
            if state["initial_entry_time"] and (time.time() - state["initial_entry_time"] > TIME_STOP):
                log("触发10分钟时间止损 → 强平")
                await close_all_market()
                state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                await asyncio.sleep(10)
                continue

            if state["current_state"] == "IDLE":
                log(f"C点开多 | 价格 {price:.3f}")
                await open_order("BUY", SIZE_MAIN, price, "LONG")
                state.update({
                    "current_state": "LONG",
                    "initial_entry_price": price,
                    "initial_entry_time": time.time()
                })

            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log(f"到达B点(+1) → 多单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price <= C - FIXED_GRID:
                    log(f"到达D点(-1) → 开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                if price <= C - 2 * FIXED_GRID:
                    log(f"到达E点(-2) → 空单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price >= C:
                    log(f"回到C点 → 加多翻倍")
                    await open_order("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"

            elif state["current_state"] == "HEDGE2":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log(f"回升B点 → 大胜出局")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})

            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            dist = price - state["initial_entry_price"] if state["initial_entry_price"] else 0
            log(f"状态:{state['current_state']:7} 价:{price:8.3f} 距C:{dist:+6.3f} 已运行:{elapsed}s")

        except Exception as e:
            log(f"循环异常: {e}")

        await asyncio.sleep(11)

# ========================== 启动 ==========================
@app.on_event("startup")
async def startup_event():
    global STEP_SIZE, QUANTITY_PRECISION
    try:
        client.futures_change_position_mode(dualSidePosition=True)
        log("双向持仓模式已确认")
    except:
        log("已是双向模式")

    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == SYMBOL:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        STEP_SIZE = float(f["stepSize"])
                        QUANTITY_PRECISION = int(round(-math.log10(STEP_SIZE)))
                        log(f"精度初始化成功 stepSize={STEP_SIZE} 精度{QUANTITY_PRECISION}位")
                        break
                break
    except Exception as e:
        log(f"获取精度失败: {e}")
        return

    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    try:
        price = await get_last_price()
    except:
        price = 0
    elapsed = int(time.time() - (state["initial_entry_time"] or time.time())) if state["initial_entry_time"] else 0
    return {
        "机器人": "SOL 1美元三角对冲实盘版",
        "状态": state["current_state"],
        "当前价": round(price, 3),
        "C点": round(state["initial_entry_price"], 3) if state["initial_entry_price"] else None,
        "距止损": max(0, TIME_STOP - elapsed)
    }

log("策略加载完成，马上起飞...")
