import os
from fastapi import FastAPI

# 官方 binance-connector 3.6.0 正确导入（GitHub 示例确认）
try:
    from binance.um_futures import UMFutures
    print("✅ Binance UM Futures imported successfully!")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    # 备用：如果还是错，用动态导入
    import importlib
    binance_um = importlib.import_module('binance.um_futures')
    UMFutures = binance_um.UMFutures
    print("✅ Fallback import success!")

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
