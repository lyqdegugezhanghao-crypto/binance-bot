import os, asyncio, time, math
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime

# ========================== 配置区 ==========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"          # 改 SOLUSDT 也行
LEVERAGE = 3
FIXED_GRID = 1.0
TIME_STOP = 600             # 10分钟

SIZE_MAIN   = 15
SIZE_HEDGE1 = 30
SIZE_HEDGE2 = 55
# ===========================================================

app = FastAPI()
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

STEP_SIZE = None
state = {"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None}

def log(m): print(f"[{datetime.now():%H:%M:%S}] {m}")

async def get_price():
    return float(client.mark_price(SYMBOL)["markPrice"])

def qty_ok(q):
    global STEP_SIZE
    if q < STEP_SIZE: return 0
    return round((int(q / STEP_SIZE)) * STEP_SIZE, 8)

async def open(side, usd, price, pos_side):
    q = qty_ok(usd * LEVERAGE / price)
    if q == 0:
        log("数量太小，放弃下单")
        return False
    params = {"symbol":SYMBOL, "side":side, "quantity":q, "positionSide":pos_side}
    try:
        r = client.new_order(**params, type="MARKET")
        if r.get("orderId"):
            log(f"开仓成功 → {pos_side} | {side} {q} 手")
            return True
    except Exception as e:
        log(f"开仓失败 → {e}")
    return False

# ==================== 彻底修复版强平函数 ====================
async def close_all():
    try:
        positions = client.position_information(symbol=SYMBOL)   # 正确函数名
        for p in positions:
            amt = float(p["positionAmt"])
            if abs(amt) < 0.01: continue
            side = "SELL" if amt > 0 else "BUY"
            client.new_order(
                symbol=SYMBOL,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                positionSide=p["positionSide"]
            )
            log(f"强平成功 → {p['positionSide']} {abs(amt):.2f} 手")
    except Exception as e:
        log(f"强平失败 → {e}")

# ========================== 主循环 ==========================
async def strategy_loop():
    await asyncio.sleep(2)
    global STEP_SIZE
    try:
        for s in client.exchange_info()["symbols"]:
            if s["symbol"] == SYMBOL:
                STEP_SIZE = float([f["stepSize"] for f in s["filters"] if f["filterType"]=="LOT_SIZE"][0])
        client.change_position_mode(dualSidePosition=True)
        log("初始化完成，步长 {STEP_SIZE}，已双向模式")
    except Exception as e: log(f"初始化异常 {e}")

    while True:
        try:
            price = await get_price()

            # 倒计时播报（每30秒一次）
            if state["initial_entry_time"]:
                remain = TIME_STOP - int(time.time() - state["initial_entry_time"])
                if remain > 0 and remain % 30 < 9:
                    log(f"距离10分钟时间止损还剩 {remain} 秒（{remain//60}分{remain%60}秒）")

            # 真实时间止损
            if state["initial_entry_time"] and time.time() - state["initial_entry_time"] > TIME_STOP:
                log("10分钟时间止损触发 → 强平所有仓位")
                await close_all()
                state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})
                await asyncio.sleep(15)
                continue

            # 状态机
            if state["current_state"] == "IDLE":
                if await open("BUY", SIZE_MAIN, price, "LONG"):
                    state.update({"current_state":"LONG","initial_entry_price":price,"initial_entry_time":time.time()})
                    log(f"第一仓建立！C点 {price:.3f}")

            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log("涨1美元，止盈出局")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})
                elif price <= C - FIXED_GRID:
                    await open("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"; log("跌1美元，开空对冲")

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                if price <= C - 2*FIXED_GRID:
                    log("跌2美元，空单止盈")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})
                elif price >= C:
                    await open("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"; log("回到C点，加多翻倍")

            elif state["current_state"] == "HEDGE2":
                if price >= state["initial_entry_price"] + FIXED_GRID:
                    log("最终回升1美元，大胜出局！")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})

            dist = price - state["initial_entry_price"] if state["initial_entry_price"] else 0
            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            log(f"状态:{state['current_state']:7} 价:{price:8.3f} 距C:{dist:+6.3f} 运行:{elapsed}s")

        except Exception as e:
            log(f"异常 {e}")
        await asyncio.sleep(9)

@app.on_event("startup")
async def go():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def home():
    try: p = await get_price()
    except: p = 0
    remain = max(0, TIME_STOP - int(time.time() - (state["initial_entry_time"] or time.time()))) if state["initial_entry_time"] else 0
    return {"状态":state["current_state"], "价格":round(p,3), "C点":state["initial_entry_price"], "距时间止损":remain}

log("2025 终极无敌版已就位，马上起飞……")
