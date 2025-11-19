import os
from fastapi import FastAPI

# 官方 3.6.0 导入路径（已验证）
try:
    from binance.um_futures import UMFutures
    print("✅ Binance UM Futures imported successfully")
except ImportError as e:
    print(f"❌ Import error: {e}")
    UMFutures = None  # 备用

app = FastAPI(title="Binance USDC Balance Checker")

@app.get("/")
@app.get("/balance")
async def get_usdc_balance():
    if UMFutures is None:
        return {"error": "Binance 库导入失败，请检查 requirements.txt"}

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")

    if not api_key or not api_secret:
        return {"error": "请在 Render 环境变量中配置 BINANCE_API_KEY 和 BINANCE_SECRET"}

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
                    "可提现余额": item.get("withdrawAvailable", "N/A"),
                    "更新时间": item.get("updateTime", "N/A")
                }
        return {"status": "warning", "message": "账户中未找到 USDC"}
    
    except Exception as e:
        return {"error": f"Binance API 调用失败: {str(e)}"}

@app.get("/health")
async def health():
    return {"status": "running", "message": "Binance checker 正在运行"}
