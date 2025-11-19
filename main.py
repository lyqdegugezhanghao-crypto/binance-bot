import os, asyncio, time
from fastapi import FastAPI
from binance.um_futures import UMFutures
from datetime import datetime

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

SYMBOL = "SOLUSDC"
LEVERAGE = 3
FIXED_GRID = 1.0
TIME_STOP = 600

SIZE_MAIN, SIZE_HEDGE1, SIZE_HEDGE2 = 15, 30, 55

app = FastAPI()
client = UMFutures(key=API_KEY, secret=API_SECRET, base_url="https://fapi.binance.com")

state = {"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None}

def log(m): print(f"[{datetime.now():%H:%M:%S}] {m}")

async def get_price():
    return float(client.mark_price(SYMBOL)["markPrice"])

async def open(side, usd, price, pos_side):
    raw_qty = usd * LEVERAGE / price
    qty = round(raw_qty // 0.01 * 0.01, 2)  # SOLUSDC stepSize=0.01
    if qty < 0.01: return False
    try:
        r = client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=qty, positionSide=pos_side)
        if r.get("orderId"):
            log(f"开仓成功 → {pos_side} {side} {qty} 手")
            return True
    except Exception as e:
        log(f"开仓失败 → {e}")
    return False

# 终极核弹级强平（双保险）
async def close_all():
    try:
        # 方法1：最稳定的API
        positions = client.get_position_risk(symbol=SYMBOL)
        for p in positions:
            amt = float(p["positionAmt"])
            if abs(amt) < 0.01: continue
            side = "SELL" if amt > 0 else "BUY"
            client.new_order(symbol=SYMBOL, side=side, type="MARKET", quantity=abs(amt), positionSide=p["positionSide"])
            log(f"强平成功 → {p['positionSide']} {abs(amt):.2f} 手（get_position_risk）")

        # 方法2：核弹级一键全平（永不漏平）
        client.futures_account_close_all_positions(symbol=SYMBOL, closeType="LIQUIDATION")
        log("执行核弹级全平（close_all_positions）")
    except Exception as e:
        log(f"强平异常 → {e}")

async def strategy_loop():
    await asyncio.sleep(3)
    try:
        client.change_position_mode(dualSidePosition=True)
        log("已确保双向持仓模式")
    except: pass

    while True:
        try:
            price = await get_price()

            # 倒计时
            if state["initial_entry_time"]:
                remain = TIME_STOP - int(time.time() - state["initial_entry_time"])
                if remain > 0 and remain % 30 < 9:
                    log(f"距离时间止损还剩 {remain} 秒")

            # 时间止损
            if state["initial_entry_time"] and time.time() - state["initial_entry_time"] > TIME_STOP:
                log("10分钟时间止损触发 → 执行核弹级强平")
                await close_all()
                state.update({"current_state":"IDLE", "initial_entry_price":None, "initial_entry_time":None})
                await asyncio.sleep(18)
                continue

            # 只在 IDLE 开第一仓
            if state["current_state"] == "IDLE":
                if await open("BUY", SIZE_MAIN, price, "LONG"):
                    state.update({"current_state":"LONG", "initial_entry_price":price, "initial_entry_time":time.time()})
                    log(f"第一仓建立 C点 {price:.3f}")

            # 其他状态逻辑保持不变
            elif state["current_state"] == "LONG":
                C = state["initial_entry_price"]
                if price >= C + FIXED_GRID:
                    log("涨1美元 止盈")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})
                elif price <= C - FIXED_GRID:
                    await open("SELL", SIZE_HEDGE1, price, "SHORT")
                    state["current_state"] = "HEDGE1"; log("跌1美元 开空对冲")

            elif state["current_state"] == "HEDGE1":
                C = state["initial_entry_price"]
                if price <= C - 2*FIXED_GRID:
                    log("跌2美元 空单止盈")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})
                elif price >= C:
                    await open("BUY", SIZE_HEDGE2, price, "LONG")
                    state["current_state"] = "HEDGE2"; log("回到C点 加多翻倍")

            elif state["current_state"] == "HEDGE2":
                if price >= state["initial_entry_price"] + FIXED_GRID:
                    log("最终回升 大胜出局")
                    await close_all(); state.update({"current_state":"IDLE","initial_entry_price":None,"initial_entry_time":None})

            dist = price - state["initial_entry_price"] if state["initial_entry_price"] else 0
            elapsed = int(time.time() - (state["initial_entry_time"] or time.time()))
            log(f"状态:{state['current_state']:7} 价:{price:8.3f} 距C:{dist:+6.3f} 运行:{elapsed}s")

        except Exception as e:
            log(f"循环异常 {e}")
        await asyncio.sleep(9)

@app.on_event("startup")
async def start():
    asyncio.create_task(strategy_loop())

@app.get("/")
async def root():
    try: p = await get_price()
    except: p = 0
    return {"状态": state["current_state"], "价格": p, "C点": state["initial_entry_price"]}

log("2025 核弹级永不叠仓版已启动")
