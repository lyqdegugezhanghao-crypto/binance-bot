import os
import asyncio
import time
import math
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 改成 SOLUSDT 也完美支持
LEVERAGE = 3
FIXED_GRID = 1.0            # 固定 1 美元网格
TIME_STOP = 600             # 10 分钟真实时间止损

SIZE_MAIN   = 15    # C点开多（美元）
SIZE_HEDGE1 = 30    # D点开空对冲
SIZE_HEDGE2 = 55    # 回到C点加多翻倍
# ===========================================================

app = FastAPI()
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

STEP_SIZE = None
state = {
    "current_state": "IDLE",
    "initial_entry_price": None,
    "initial_entry_time": None,
}

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")

async def get_last_price():
    return float(client.mark_price(SYMBOL)["markPrice"])

def adjust_qty(qty):
    global STEP_SIZE
    if STEP_SIZE is None or qty < STEP_SIZE:
        return 0
    return round((int(qty / STEP_SIZE)) * STEP_SIZE, 8)

async def open_order(side: str, usd_amount: float, price: float, position_side: str) -> bool:
    qty = adjust_qty(usd_amount * LEVERAGE / price)
    if qty <= 0:
        log("数量太小，无法下单")
        return False

    params = {
        "symbol": SYMBOL,
        "side": side,
        "quantity": qty,
        "positionSide": position_side
    }

    try:
        # 优先限价IOC
        resp = client.new_order(
            **params,
            type="LIMIT",
            price=round(price // 0.01 * 0.01, 8),
            timeInForce="IOC"
        )
        if resp.get("orderId"):
            log(f"开仓成功 → {position_side} | {side} {qty} @ 限价")
            return True
    except:
        pass

    # 市价补单
    try:
        resp = client.new_order(**params, type="MARKET")
        if resp.get("orderId"):
            log(f"开仓成功 → {position_side} | {side} {qty} @ 市价")
            return True
        else:
            log(f"下单被拒 → {resp.get('msg', '未知错误')}")
    except Exception as e:
        log(f"下单异常 → {e}")

    return False

async def close_all_market():
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for p in positions:
            amt = float(p["positionAmt"])
            qty = abs(amt)
            if qty < 0.009:
                continue
            side = "SELL" if amt > 0 else "BUY"
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=qty,
                positionSide=p["positionSide"]
            )
            log(f"强平 → {p['positionSide']} {qty}")
    except Exception as e:
        log(f"强平异常 → {e}")

# ========================== 核心循环（已彻底解决重复开仓 + 时间止损） ==========================
async def strategy_loop():
    await asyncio.sleep(1)

    # 初始化精度 + 双向模式
    global STEP_SIZE
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == SYMBOL:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        STEP_SIZE = float(f["stepSize"])
                break
        log(f"精度初始化成功 stepSize={STEP_SIZE}")

        # 正确切换双向模式
        client.change_position_mode(dualSidePosition=True)
        log("已确认双向持仓模式")
    except Exception as e:
        log(f"初始化异常 → {e}")

    log("SOL 三角对冲实盘版启动！1美元网格 + 真10分钟止损")

    while True:
        try:
            price = await get_last_price()

            # 真实时间止损（从第一仓开始计时，绝不重置）
            if state["initial_entry_time"] and time.time() - state["initial_entry_time"] > TIME_STOP:
                log("触发10分钟时间止损 → 强平所有仓位")
                await close_all_market()
                state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                await asyncio.sleep(15)
                continue

            # 只在 IDLE 状态开第一仓，彻底杜绝重复开仓
            if state["current_state"] == "IDLE":
                if await open_order("BUY", SIZE_MAIN, price, "LONG"):
                    state.update({
                        "current_state": "LONG",
                        "initial_entry_price": price,
                        "initial_entry_time": time.time()
                    })
                    log(f"第一仓建立成功！C点 {price:.3f}")

            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log("涨1美元 → 多单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price <= C - FIXED_GRID:
                    log("跌1美元 → 开空对冲")
                    await open_order("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                if price <= C - 2 * FIXED_GRID:
                    log("跌2美元 → 空单止盈")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})
                elif price >= C:
                    log("回到C点 → 加多翻倍")
                    await open_order("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"

            elif state["current_state"] == "HEDGE2":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log("最终回升1美元 → 大胜出局！")
                    await close_all_market()
                    state.update({"current_state": "IDLE", "initial_entry_price": None, "initial_entry_time": None})

            # 心跳日志
            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            dist = price - state["initial_entry_price"] if state["initial_entry_price"] else 0
            log(f"状态:{state['current_state']:7} 价:{price:8.3f} 距C:{dist:+6.3f} 已运行:{elapsed}s")

        except Exception as e:
            log(f"循环异常 → {e}")

        await asyncio.sleep(9)

@app.on_event("startup")
async def startup():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    try:
        price = await get_last_price()
    except:
        price = 0
    elapsed = int(time.time() - (state["initial_entry_time"] or time.time())) if state["initial_entry_time"] else 0
    return {
        "机器人": "SOL 1美元三角对冲",
        "状态": state["current_state"],
        "价格": round(price, 3),
        "C点": round(state["initial_entry_price"], 3) if state["initial_entry_price"] else None,
        "距C": round(price - (state["initial_entry_price"] or price), 3),
        "距时间止损": max(0, TIME_STOP - elapsed)
    }

log("终极稳定版加载完成，3秒后起飞…")
