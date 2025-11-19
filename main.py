import os
from fastapi import FastAPI
from binance.um_futures import UMFutures

app = FastAPI(title="Binance USDC Balance Checker - connector 3.6.0")

@app.get("/")
@app.get("/balance")
async def get_usdc_balance():
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")

    if not api_key or not api_secret:
        return {"error": "未检测到 API_KEY / SECRET，请在 Render 环境变量中配置"}

    client = UMFutures(key=api_key, secret=api_secret)

    try:
        balance_list = client.balance()
        for item in balance_list:
            if item.get("asset") == "USDC":
                return {
                    "status": "success",
                    "asset": "USDC",
                    "总余额": item["balance"],
                    "可用余额": item["availableBalance"],
                    "可提现余额": item["withdrawAvailable"],
                    "更新时间": item["updateTime"]
                }
        return {"status": "warning", "message": "账户中未找到 USDC"}
    
    except Exception as e:
        return {"error": f"请求失败: {str(e)}"}

@app.get("/health")
async def health():
    return {"status": "running", "message": "Binance checker 正在运行"}
