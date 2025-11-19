# ========================== 一键终极修复版（直接覆盖全部） ==========================
import os, asyncio, time, math
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 想改 SOLUSDT 直接改这一行就行
LEVERAGE = 3
FIXED_GRID = 1.0
TIME_STOP = 600             # 10分钟

SIZE_MAIN   = 15
SIZE_HEDGE1 = 30
SIZE_HEDGE2 = 55

app = FastAPI()
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

STEP_SIZE = None
state = {
    "current_state": "IDLE",
    "initial_entry_price": None,
    "initial_entry_time": None,
}

def log(msg): print(f"[{datetime.now():%H:%M:%S}] {msg}")

async def get_last_price():
    return float(client.mark_price(SYMBOL)["markPrice"])

def adjust_qty(qty):
    global STEP_SIZE
    if qty < STEP_SIZE: return 0
    return round((int(qty / STEP_SIZE)) * STEP_SIZE, 8)

# 真实下单成功才打印成功
async def open_order(side, usd, price, pos_side):
    qty = adjust_qty(usd * LEVERAGE / price)
    if qty == 0: 
        log(f"数量太小，无法下单")
        return False

    params = {"symbol":SYMBOL, "side":side, "quantity":qty, "positionSide":pos_side}
    # 限价IOC
    resp = client.new_order(**params, type="LIMIT", price=round(price//0.01*0.01, 8), timeInForce="IOC")
    if resp.get("orderId"):
        log(f"成功开仓 {pos_side:5} | {side} {qty} @ 市价/限价")
        return True
    # 市价补单
    resp = client.new_order(**params, type="MARKET")
    if resp.get("orderId"):
        log(f"成功开仓 {pos_side:5} | {side} {qty} 市价补单")
        return True
    log(f"下单被拒 {resp.get('msg','')}")
    return False

async def close_all_market():
    for p in client.futures_position_information(symbol=SYMBOL):
        qty = abs(float(p["positionAmt"]))
        if qty < 0.01: continue
        side = "SELL" if float(p["positionAmt"])>0 else "BUY"
        client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty, positionSide=p["positionSide"])
        log(f"强平 {p['positionSide']} {qty}")

async def strategy_loop():
    await asyncio.sleep(1)
    # 精度
    global STEP_SIZE
    for s in client.exchange_info()["symbols"]:
        if s["symbol"] == SYMBOL:
            STEP_SIZE = float([f["stepSize"] for f in s["filters"] if f["filterType"]=="LOT_SIZE"][0])
            break
    log(f"启动完成，步长 {STEP_SIZE}")

    while True:
        try:
            price = await get_last_price()

            # 10分钟真·时间止损
            if state["initial_entry_time"] and time.time() - state["initial_entry_time"] > TIME_STOP:
                log("10分钟时间止损触发，强平所有仓位")
                await close_all_market()
                state.update({"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None})
                await asyncio.sleep(15)
                continue

            # IDLE → 只能从这里开第一仓
            if state["current_state"] == "IDLE":
                if await open_order("BUY", SIZE_MAIN, price, "LONG"):
                    state.update({
                        "current_state": "LONG",
                        "initial_entry_price": price,
                        "initial_entry_time": time.time()
                    })
                    log(f"第一仓建立成功，C点 {price:.3f}")

            # LONG 状态
            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log("涨1刀止盈")
                    await close_all_market()
                    state.update({"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None})
                elif price <= C - FIXED_GRID:
                    await open_order("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"
                    log("跌1刀，开空对冲 → HEDGE1")

            # HEDGE1 状态
            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                if price <= C - 2*FIXED_GRID:
                    log("跌2刀，空单止盈")
                    await close_all_market()
                    state.update({"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None})
                elif price >= C:
                    await open_order("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"
                    log("回到C点，加多翻倍 → HEDGE2")

            # HEDGE2 状态
            elif state["current_state"] == "HEDGE2":
                if price >= state["initial_entry_price"] + FIXED_GRID:
                    log("最终涨1刀，大胜出局！")
                    await close_all_market()
                    state.update({"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None})

            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            dist = price - state["initial_entry_price"] if state["initial_entry_price"] else 0
            log(f"状态:{state['current_state']:7}  价:{price:8.3f}  距C:{dist:+6.3f}  运行:{elapsed}s")

        except Exception as e:
            log(f"异常: {e}")

        await asyncio.sleep(9)

@app.on_event("startup")
async def start():
    client.futures_change_position_mode(dualSidePosition=True)  # 确保双向
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    try: p = await get_last_price()
    except: p = 0
    return {"状态": state["current_state"], "价格": p, "距C": round(p - (state["initial_entry_price"] or p),3)}

log("终极版加载完成，3秒后起飞…")
