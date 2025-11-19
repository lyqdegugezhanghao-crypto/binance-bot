import os
from fastapi import FastAPI
from binance.um_futures import UMFutures

app = FastAPI(title="Binance USDC Balance Checker")

@app.get("/")
@app.get("/balance")
async def get_usdc_balance():
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")

    if not api_key or not api_secret:
        return {"error": "请在 Render 环境变量中配置 BINANCE_API_KEY 和 BINANCE_SECRET"}

    client = UMFutures(key=api_key, secret=api_secret)

    try:
        balances = client.balance()
        for item in balances:
            if item["asset"] == "USDC":
                return {
                    "status": "success",
                    "USDC 总余额": item["balance"],
                    "USDC 可用余额": item["availableBalance"],
                    "更新时间": item["updateTime"]
                }
        return {"status": "not_found", "message": "账户中未找到 USDC"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/health")
async def health():
    return {"status": "running", "message": "Binance checker 已就
