# -*- coding: utf-8 -*-
"""收盘自动退出的离线验证：用伪造的当前时间喂 maybe_auto_close，看该关的时候关不关。

不联网、不启动采集线程、不真的退出。用完可以删。

两个关键区分（都踩过坑）：
- reboot() = 像一个新进程那样启动（重新校准今天还要不要等）
- rearm()  = 进程一直活着、日期翻篇（无条件等当天那个点）
"""

import os
import sys
import tempfile
import types
from datetime import datetime

import quotes
import widget as W

# 测试实例不能碰正式配置（否则会覆盖浮窗的位置、透明度、开关）
TMP = tempfile.mkdtemp(prefix="autoclosetest-")
W.CONF_FILE = os.path.join(TMP, "widget.json")
quotes.WATCHLIST_FILE = os.path.join(TMP, "watchlist.json")
quotes.save_watchlist([{"symbol": "sh600403", "code": "600403", "name": "大有能源"}])


class TestWidget(W.StockWidget):
    def start(self):
        pass                    # 不启动采集线程与快捷键


app = TestWidget()
closed = []
app.close_for_day = lambda: closed.append(now)

now = None


def at(h, m, s, day=datetime(2026, 9, 24)):     # 2026-09-24 是周四
    return day.replace(hour=h, minute=m, second=s, microsecond=0)


def reboot():
    """模拟一个新进程刚启动"""
    app._ac_date = None
    app._ac_booted = False


def rearm():
    """进程一直活着，只是日期翻篇了"""
    app._ac_date = None


def check(desc, t, want_fire):
    """喂一个时刻，看这次调用有没有触发退出"""
    global now
    now = t
    before = len(closed)
    fired = app.maybe_auto_close(now)
    got = len(closed) > before
    ok = (fired == want_fire) and (got == want_fire)
    print("%s %s  返回[%s] 退出[%s] 期望[%s]   %s"
          % ("OK " if ok else "!! ", t.strftime("%m-%d %H:%M:%S"),
             "真" if fired else "假", "是" if got else "否",
             "是" if want_fire else "否", desc))
    return ok


allok = True
print("=" * 74)
print("场景 A：早盘 10:00 启动 → 15:00:05 该关，且只关一次")
print("=" * 74)
reboot()
allok &= check("刚启动，还没到点", at(10, 0, 1), False)
allok &= check("午休期间", at(12, 0, 0), False)
allok &= check("收盘前 1 秒", at(15, 0, 4), False)
allok &= check("到点，触发退出", at(15, 0, 5), True)
allok &= check("退出流程中重复调用，不再触发", at(15, 0, 5), False)

print()
print("=" * 74)
print("场景 B：晚上复盘才启动 → 当天不该被秒杀，次日到点才关")
print("=" * 74)
reboot()
closed.clear()
# 9/23、9/24 都是交易日；9/25 起中秋休市，不能拿来当“次日该关”
allok &= check("20:00 启动，已过今天这个点", at(20, 0, 0, datetime(2026, 9, 23)), False)
allok &= check("当天 23:59 仍不动", at(23, 59, 59, datetime(2026, 9, 23)), False)
rearm()
allok &= check("跨到次日零点，重新武装", at(0, 0, 1, datetime(2026, 9, 24)), False)
allok &= check("次日 15:00:04 还没到", at(15, 0, 4, datetime(2026, 9, 24)), False)
allok &= check("次日 15:00:05 该关", at(15, 0, 5, datetime(2026, 9, 24)), True)

print()
print("=" * 74)
print("场景 C：电脑休眠")
print("=" * 74)
reboot()
closed.clear()
allok &= check("14:50 睡下，还没到点", at(14, 50, 0), False)
allok &= check("15:30 醒来，已过点 → 补一次退出", at(15, 30, 0), True)

reboot()
closed.clear()
allok &= check("23:00 开机（当天已过点）", at(23, 0, 0, datetime(2026, 9, 23)), False)
rearm()
allok &= check("一觉睡到次日 15:00:05，跨天第一眼就在点上 → 仍要关",
               at(15, 0, 5, datetime(2026, 9, 24)), True)

print()
print("=" * 74)
print("场景 D：启动/重开的那一刻正好卡在 15:00:05 → 按'校准'处理，不补退")
print("=" * 74)
reboot()
closed.clear()
allok &= check("启动时刻恰好在点上，今天不等", at(15, 0, 5, datetime(2026, 9, 28)), False)
rearm()
allok &= check("次日正常触发", at(15, 0, 5, datetime(2026, 9, 29)), True)

print()
print("=" * 74)
print("场景 D2：周末和节假日 15:00 不退出")
print("=" * 74)
reboot()
closed.clear()
allok &= check("周六下午不退", at(15, 0, 5, datetime(2026, 9, 26)), False)
allok &= check("中秋节周五不退", at(15, 0, 5, datetime(2026, 9, 25)), False)

print()
print("=" * 74)
print("场景 E：菜单开关")
print("=" * 74)
app.var_auto_close.set(False)
app.toggle_auto_close()
reboot()
closed.clear()
allok &= check("关掉开关后到点也不退", at(15, 0, 5, datetime(2026, 9, 28)), False)

app.var_auto_close.set(True)
app.toggle_auto_close()
allok &= check("当天已过点，重新打开不补退", at(16, 0, 0, datetime(2026, 9, 28)), False)
rearm()
allok &= check("次日照常触发", at(15, 0, 5, datetime(2026, 9, 29)), True)

print()
print("=" * 74)
print("场景 F：与主循环的衔接")
print("=" * 74)
hits = []
app.close_for_day = lambda: hits.append(now)
rearm()
now = at(15, 0, 5, datetime(2026, 9, 30))
r1 = app.maybe_auto_close(now)
r2 = app.maybe_auto_close(now)
hit = (r1 is True and r2 is False and len(hits) == 1)
allok &= hit
print("%s 触发当轮返回 True（主循环据此不再排下一轮），随后立刻返回 False，只收尾一次"
      % ("OK " if hit else "!! "))

print()
print("=" * 74)
print("场景 G：真实收尾 —— 可见时弹告别气泡，隐藏时直接走")
print("=" * 74)
# 前面为了计数把 close_for_day 换成了记录器，这里换回真实实现
app.close_for_day = types.MethodType(W.StockWidget.close_for_day, app)
quits = []
app.quit = lambda: quits.append(1)
app.hidden = False
app.close_for_day()
app.root.update()
n = len(app.bubbles)
allok &= (n == 1)
print("%s 可见时：弹 1 个气泡 %s"
      % ("OK " if n == 1 else "!! ", app.bubbles[0].geometry() if n else "无"))
if n:
    texts = [c.cget("text") for c in app.bubbles[0].winfo_children()[0].winfo_children()]
    print("   内容:", texts)

import time as _tt
for _ in range(80):
    app.root.update()
    _tt.sleep(0.05)
    if quits:
        break
allok &= len(quits) == 1
print("%s 气泡留 %d ms 后触发退出：%s"
      % ("OK " if quits else "!! ", W.AUTO_CLOSE_BYE_MS, "已退出" if quits else "没退出"))

for b in list(app.bubbles):
    app.close_bubble(b)
quits.clear()
app.hidden = True
app.close_for_day()
app.root.update()
allok &= (not app.bubbles)
print("%s 隐藏时：不弹气泡（没位置可告别）" % ("OK " if not app.bubbles else "!! "))

print()
print("=" * 74)
print("全部用例通过" if allok else "有用例不符合预期，见上面的 !! 行")
app.root.destroy()
sys.exit(0 if allok else 1)
