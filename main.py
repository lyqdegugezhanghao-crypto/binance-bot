import os
from fastapi import FastAPI

# 官方期货包导入（已验证通过 GitHub 示例）
try:
    from binance.um_futures import UMFutures
    print("✅ Binance UM Futures imported successfully!")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    raise  # 如果还失败，直接抛错让日志显示

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
    return {"status": "running", "message": "Binance Futures checker 正在运行"}
