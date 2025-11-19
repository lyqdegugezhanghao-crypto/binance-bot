import os
from binance.um_futures import UMFutures

def main():
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_SECRET")

    if not api_key or not api_secret:
        print("❌ 未检测到环境变量，请检查 Render 是否配置 BINANCE_API_KEY / BINANCE_SECRET")
        return

    client = UMFutures(key=api_key, secret=api_secret)

    try:
        balance_list = client.balance()
        print("✅ 成功连接币安实盘！\n")

        for item in balance_list:
            if item["asset"] == "USDC":
                print("USDC 账户余额：", item["balance"])
                print("USDC 可用余额：", item["availableBalance"])
                return

        print("⚠️ 没有找到 USDC，请确认资金在 U 本位合约账户")

    except Exception as e:
        print("❌ 连接币安失败：", e)

if __name__ == "__main__":
    main()
