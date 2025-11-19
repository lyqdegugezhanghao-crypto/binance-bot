# -*- coding: utf-8 -*-
from fmz import *
task = VCtx(__name__)

# ==================== 参数区 ====================
grid_step          = 1.0            # 每档间隔 1 美元
base_lots          = 0.32           # 首单手数
martin_ratio       = 1.6            # 马丁倍数
take_profit_usd    = 80             # 总盈利多少美元就全平重启
# ================================================

done_levels = set()                 # 已开过的相对档位：'0','+1','+2','-1','-2'

def get_pos():
    pos = exchange.GetPosition()
    long = short = 0
    for p in pos:
        if p['Type'] in [PD_LONG, PD_LONG_YD]:
            long += p['Amount']
        elif p['Type'] in [PD_SHORT, PD_SHORT_YD]:
            short += p['Amount']
    return long, short

def close_all_and_reset():
    global done_levels
    pos = exchange.GetPosition()
    if not pos:
        return
    Log("【止盈触发】盈利 $", round(exchange.GetAccount().Profit, 2), " 全平并重启网格")
    for p in pos:
        if p['Amount'] > 0:
            if p['Type'] in [PD_LONG, PD_LONG_YD]:
                exchange.SetDirection("closebuy")
                exchange.Sell(-1, p['Amount'])
            else:
                exchange.SetDirection("closesell")
                exchange.Buy(-1, p['Amount'])
    Sleep(5000)
    done_levels.clear()
    Log("网格已完全重置，等待新价格锚点")

def main():
    global done_levels
    LogReset(1)                                      # 启动时自动清空旧日志

    while True:
        Sleep(3000)

        # 1. 止盈检查
        acc = exchange.GetAccount()
        if acc and acc.Profit >= take_profit_usd:
            close_all_and_reset()
            continue

        # 2. 获取持仓和最新价
        long_qty, short_qty = get_pos()
        ticker = exchange.GetTicker()
        if not ticker:
            continue
        price = ticker.Last

        # 3. 手动全平后自动重置
        if long_qty + short_qty == 0 and len(done_levels) > 0:
            done_levels.clear()
            Log("检测到空仓 → 动态网格已重置")

        # 4. 动态计算当前五档（核心！C点永远是当前价）
        C = price
        B = price + grid_step
        A = price + 2 * grid_step
        D = price - grid_step
        E = price - 2 * grid_step

        # 5. 动态手数
        lots_dict = {
            '+2': round(base_lots * martin_ratio**4, 2),  # A
            '+1': round(base_lots * martin_ratio**3, 2),  # B
            '0' : base_lots,                              # C
            '-1': round(base_lots * martin_ratio**1, 2),  # D
            '-2': round(base_lots * martin_ratio**2, 2),  # E
        }

        # 6. 状态栏（干净不刷屏）
        LogStatus(f"{_D()} | 价格(C):{price:.3f} | 多:{long_qty} 空:{short_qty} | 已开{len(done_levels)}档 | 盈利:${acc.Profit:.1f}")

        # 7. 完全空仓 → 立即以当前价开C点多单
        if long_qty + short_qty == 0 and '0' not in done_levels:
            exchange.SetDirection("buy")
            exchange.Buy(-1, lots_dict['0'])
            done_levels.add('0')
            Log(f"动态C点建仓 @ {price:.3f}，手数 {lots_dict['0']}")

        # 8. 只有多仓 → 加多或首次对冲空
        elif long_qty > 0 and short_qty == 0:
            if price >= B and '+1' not in done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots_dict['+1']); done_levels.add('+1')
            if price >= A and '+2' not in done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots_dict['+2']); done_levels.add('+2')
            if price <= D and '-1' not in done_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots_dict['-1']); done_levels.add('-1')

        # 9. 只有空仓 → 加空或对冲多
        elif short_qty > 0 and long_qty == 0:
            if price <= E and '-2' not in done_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots_dict['-2']); done_levels.add('-2')
            if price >= B and '+1' not in done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots_dict['+1']); done_levels.add('+1')

        # 10. 双向持仓 → 只加最远两档
        elif long_qty > 0 and short_qty > 0:
            if price >= A and '+2' not in done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots_dict['+2']); done_levels.add('+2')
            if price <= E and '-2' not in done_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots_dict['-2']); done_levels.add('-2')
