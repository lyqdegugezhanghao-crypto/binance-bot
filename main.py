import os
import asyncio
from fastapi import FastAPI
from binance.um_futures import UMFutures

# ========= 配置区 =========
CHECK_INTERVAL = 30  # 每隔多少秒检查一次（实盘建议 15~60）
# =========================

app = FastAPI(title="Binance 实盘环境测试器 - 纯日志版")

# 全局 client（启动时创建一次）
client = None

@app.on_event("startup")
async def startup_event():
    global client
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")

    if not api_key or not api_secret:
        print("ERROR: 未检测到 BINANCE_API_KEY 或 BINANCE_SECRET 环境变量！")
        return

    client = UMFutures(key=api_key, secret=api_secret)
    print("Binance 实盘客户端初始化成功！开始每 {} 秒巡检...".format(CHECK_INTERVAL))

    # 启动后台巡检任务
    asyncio.create_task(real_disk_health_check())

async def real_disk_health_check():
    """纯日志版实盘健康检查 - 绝不下单"""
    while True:
        try:
            # 1. 查询账户余额（重点看 USDC）
            print("\n=== 开始新一轮实盘巡检 ===")
            balances = client.balance()
            usdc_info = None
            for b in balances:
                if b["asset"] == "USDC":
                    usdc_info = b
                    break

            if usdc_info:
                print(f"USDC 总余额: {usdc_info['balance']}")
                print(f"USDC 可用余额: {usdc_info['availableBalance']}")
                print(f"USDC 可提现: {usdc_info.get('withdrawAvailable', 'N/A')}")
            else:
                print("Warning: 账户中未找到 USDC")

            # 2. 查询 BTCUSDT 最新标记价格
            ticker = client.mark_price("BTCUSDT")
            price = float(ticker["markPrice"])
            print(f"BTCUSDT 当前标记价格: {price:,.2f} USDT")

            print("=== 本轮巡检完成，实盘连接正常 ===\n")

        except Exception as e:
            print(f"ERROR: 实盘巡检出错: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

@app.get("/")
@app.get("/health")
async def health():
    return {
        "status": "实盘环境测试器正在运行",
        "检查间隔秒": CHECK_INTERVAL,
        "提示": "打开 Logs 标签页即可看到实时余额和价格"
    }

print("实盘测试器启动中...")
