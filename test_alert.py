# -*- coding: utf-8 -*-
"""异动提醒的离线验证：伪造时间轴喂一串价格，看什么时候该弹、什么时候不该弹。

比的是实时价（窗口内低点→现价 / 高点→现价），与昨收无关。
不联网、不启动采集线程。用完可以删。
"""

import os
import sys
import tempfile
import time as _t

import quotes
import widget as W

# ---- 0) 配置/自选全部改到临时目录 ----------------------------------------
# 测试实例不能碰正式配置，否则会把浮窗的位置、透明度覆盖掉
TMP = tempfile.mkdtemp(prefix="widgettest-")
W.CONF_FILE = os.path.join(TMP, "widget.json")
quotes.WATCHLIST_FILE = os.path.join(TMP, "watchlist.json")
quotes.save_watchlist([{"symbol": "sh600403", "code": "600403", "name": "大有能源"},
                       {"symbol": "sh603716", "code": "603716", "name": "塞力医疗"}])

# ---- 1) 接管 time.time，让"5 秒"由我们说了算 -------------------------------
CLOCK = {"now": 1000.0}
_t.time = lambda: CLOCK["now"]


class TestWidget(W.StockWidget):
    def start(self):
        pass                    # 不启动采集线程与快捷键


app = TestWidget()
fired = []
app.pop_bubble = lambda name, delta, p_from, p_to, span: fired.append(
    (round(CLOCK["now"], 1), name, round(delta, 2), p_from, p_to, round(span, 1)))

SYM = "sh600403"
ITEM = {"symbol": SYM, "code": "600403", "name": "大有能源"}


def tick(price):
    app.check_alert(SYM, {"symbol": SYM, "name": "大有能源",
                          "price": price, "change_pct": 0.0}, ITEM)


print("=" * 68)
print("场景 A：每 2 秒一笔，7.25 缓慢抬到 7.35（+1.38%）才该弹")
print("=" * 68)
A = [
    (0.0, 7.25, "第一笔，不足两点", False),
    (2.0, 7.25, "两点同价，窗口变动 0", False),
    (2.0, 7.25, "仍是 0", False),
    (2.0, 7.30, "窗口内 +0.69%，不到 1%", False),
    (2.0, 7.35, "窗口内 +1.38% → 该弹", True),
    (2.0, 7.40, "又 +1.37%，但同向且冷却中 → 不弹", False),
    (2.0, 7.40, "同向，仍不弹", False),
    (2.0, 7.45, "窗口已滑动，只剩 +0.68% → 不弹", False),
]
ok = True
for dt, price, desc, want in A:
    CLOCK["now"] += dt
    before = len(fired)
    tick(price)
    got = len(fired) > before
    ok = ok and got == want
    tail = "  →  %+.2f%%  %.2f→%.2f  %.0f秒" % (fired[-1][2], fired[-1][3],
                                                fired[-1][4], fired[-1][5]) if got else ""
    print("%s t=%7.1f 价 %.2f  %-34s 弹=%s%s"
          % ("OK " if got == want else "!! ", CLOCK["now"], price, desc,
             "是" if got else "否", tail))

print()
print("=" * 68)
print("场景 B：反向急跌 → 冷却不挡（方向变了必须马上报）")
print("=" * 68)
for dt, price, desc, want in [
    (2.0, 7.42, "小幅回落，窗口内仅 -0.4%", False),
    (2.0, 7.20, "2 秒 -2.96% → 该弹（跳水）", True),
    (2.0, 7.18, "继续跌但同向冷却中 → 不弹", False),
]:
    CLOCK["now"] += dt
    before = len(fired)
    tick(price)
    got = len(fired) > before
    ok = ok and got == want
    tail = "  →  %+.2f%%  %.2f→%.2f  %.0f秒" % (fired[-1][2], fired[-1][3],
                                                fired[-1][4], fired[-1][5]) if got else ""
    print("%s t=%7.1f 价 %.2f  %-34s 弹=%s%s"
          % ("OK " if got == want else "!! ", CLOCK["now"], price, desc,
             "是" if got else "否", tail))

print()
print("=" * 68)
print("场景 C：你举的例子 —— 2 元到 3 元")
print("=" * 68)
app.price_hist.clear()
app.alert_cd.clear()
fired.clear()
tick(2.00)
CLOCK["now"] += 2.0
tick(3.00)
hit = len(fired) == 1
ok = ok and hit
print("%s 2.00 → 3.00（2 秒）：弹=%s  %s"
      % ("OK " if hit else "!! ", "是" if hit else "否",
         ("▲ %.0f秒拉升 %.2f%%  %.2f→%.2f" % (fired[0][5], fired[0][2],
                                              fired[0][3], fired[0][4])) if hit else ""))

print()
print("=" * 68)
print("场景 D：断档保护（午休 / 断网 / 休眠）")
print("=" * 68)
CLOCK["now"] += 90.0
before = len(fired)
tick(2.10)          # 相对 90 秒前的 3.00 是 -30%，但那是断档，不算
got = len(fired) > before
ok = ok and not got
print("%s 隔了 90 秒后价格 3.00→2.10：窗口被清空重攒，不弹（弹=%s）"
      % ("OK " if not got else "!! ", "是" if got else "否"))

print()
print("=" * 68)
print("场景 E：阈值可调 + 开关")
print("=" * 68)
app.price_hist.clear()
app.alert_cd.clear()
fired.clear()
app.alert_pct = 2.0
for i, p in enumerate([10.00, 10.00, 10.15, 10.18]):
    CLOCK["now"] += 2.0 if i else 0.0
    tick(p)
got = len(fired) > 0
ok = ok and not got
print("%s 阈值 2.0%% 时，+1.8%% 不弹（弹=%s）" % ("OK " if not got else "!! ", "是" if got else "否"))

app.alert_pct = 1.0
app.alert_on = False
CLOCK["now"] += 2.0
tick(10.60)         # +4%，但开关关了
got = len(fired) > 0
ok = ok and not got
print("%s 关掉提醒后，+4%% 也不弹（弹=%s）" % ("OK " if not got else "!! ", "是" if got else "否"))
app.alert_on = True

# ---- 真实气泡渲染 + 自动淡出 ----------------------------------------------
print()
print("=" * 68)
print("渲染验证")
print("=" * 68)
# 前面为纯逻辑校验把 pop_bubble 换成了记录器，这里换回真实实现
import types
app.pop_bubble = types.MethodType(W.StockWidget.pop_bubble, app)
app.pop_bubble("大有能源", 1.35, 7.10, 7.25, 5.0)
app.pop_bubble("塞力医疗", -1.85, 16.90, 16.60, 4.0)
app.root.update()
print("真弹两个气泡，位置:", [b.geometry() for b in app.bubbles])
print("置顶 / alpha   :", [b.attributes("-topmost") for b in app.bubbles],
      [round(b.attributes("-alpha"), 2) for b in app.bubbles])

for _ in range(200):
    app.root.update()
    _t.sleep(0.05)
    if not app.bubbles:
        break
print("停留后自动淡出，剩余气泡:", len(app.bubbles))

app.hide_widget()
app.root.update()
app.pop_bubble("大有能源", -1.10, 7.30, 7.10, 5.0)
app.root.update()
n = len(app.bubbles)
ok = ok and n == 1
print("%s 浮窗隐藏（Alt+V）后仍能弹气泡：%s"
      % ("OK " if n == 1 else "!! ", app.bubbles[0].geometry() if n else "无"))

print()
print("=" * 68)
print("全部用例通过" if ok else "有用例不符合预期，见上面的 !! 行")
for b in list(app.bubbles):
    app.close_bubble(b)
app.root.destroy()
sys.exit(0 if ok else 1)
