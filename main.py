# -*- coding: utf-8 -*-
from fmz import *
task = VCtx(__name__)

# ==================== 参数区（你只需要改这几行）===================
grid_step          = 1.0            # 每档间隔（1美元）
base_lots          = 0.32           # 首单手数
martin_ratio       = 1.6            # 马丁倍数
take_profit_usd    = 80             # 总盈利多少美元全平重启
max_levels         = 5              # 最多只开5档（防止狂跌狂涨一直加）
# =================================================================

done_levels = set()   # 记录已经开过的相对档位：'0'(C点), '+1'(B), '+2'(A), '-1'(D), '-2'(E)

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
    if not pos: return
    Log("【止盈触发】盈利 $", exchange.GetAccount().Profit, " 全平并重启")
    for p in pos:
        if p['Amount'] > 0:
            if p['Type'] in [PD_LONG, PD_LONG_YD]:
                exchange.SetDirection("closebuy"); exchange.Sell(-1, p['Amount'])
            else:
                exchange.SetDirection("closesell"); exchange.Buy(-1, p['Amount'])
    Sleep(5000)
    done_levels.clear()
    Log("全平完成，动态网格已重置，等待新价格锚点")

def main():
    global done_levels
    LogReset(1)                                   # 启动时清空旧日志

    while True:
        Sleep(3000)

        # 1. 止盈全平重启
        acc = exchange.GetAccount()
        if acc and acc.Profit >= take_profit_usd:
            close_all_and_reset()
            continue

        long_qty, short_qty = get_pos()
        ticker = exchange.GetTicker()
        if not ticker: continue
        price = ticker.Last

        # 2. 手动全平也自动重置
        if long_qty + short_qty == 0 and len(done_levels) > 0:
            done_levels.clear()
            Log("空仓检测 → 动态网格已重置")

        # 3. 动态计算当前五档价格（核心！）
        C = price                                     # 当前价就是C点！
        B = price + grid_step                         # 上方1美元
        A = price + 2 * grid_step
        D = price - grid_step                         # 下方1美元
        E = price - 2 * grid_step

        # 4. 动态手数
        lots = {
            '+2': round(base_lots * martin_ratio**4, 2),  # A
            '+1': round(base_lots * martin_ratio**3, 2),  # B
            '0' : base_lots,                               # C
            '-1': round(base_lots * martin_ratio**1, 2),  # D
            '-2': round(base_lots * martin_ratio**2, 2),  # E
        }

        # 5. 状态栏显示
        LogStatus(f"{_D()} | 现价(C):{price:.3f} 多:{long_qty} 空:{short_qty} 已开:{len(done_levels)}档 盈利:${acc.Profit:.1f}")

        # 6. 核心开仓逻辑（任意点位立即生效）
        # 首次建仓（C点）
        if long_qty + short_qty == 0 and '0' not in done_levels:
            exchange.SetDirection("buy")
            exchange.Buy(-1, lots['0'])
            done_levels.add('0')
            Log(f"动态C点建仓 @ {price:.3f} 手数 {lots['0']}")

        # 只有多仓 → 可以加多或首次对冲空
        elif long_qty > 0 and short_qty == 0:
            if price >= B and '+1' not in done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots['+1']); done_levels.add('+1')
            if price >= A and '+2' not in done_levels and len(done_levels) < max_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots['+2']); done_levels.add('+2')
            if price <= D and '-1' not in done_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots['-1']); done_levels.add('-1')

        # 只有空仓 → 可以加空或对冲多
        elif short_qty > 0 and long_qty == 0:
            if price <= E and '-2' not in done_levels and len(done_levels) < max_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots['-2']); done_levels.add('-2')
            if price >= B and '+1' not inthe done_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots['+1']); done_levels.add('+1')

        # 双向持仓 → 只加最远两档
        elif long_qty > 0 and short_qty > 0:
            if price >= A and '+2' not in done_levels and len(done_levels) < max_levels:
                exchange.SetDirection("buy"); exchange.Buy(-1, lots['+2']); done_levels.add('+2')
            if price <= E and '-2' not in done_levels and len(done_levels) < max_levels:
                exchange.SetDirection("sell"); exchange.Sell(-1, lots['-2']); done_levels.add('-2')
