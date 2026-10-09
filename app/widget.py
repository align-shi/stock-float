# -*- coding: utf-8 -*-
"""
A 股桌面行情浮窗

- 无边框、半透明、可置顶的桌面小挂件，贴在屏幕任意角落
- 每 60 秒自动更新价格与涨跌幅，红涨绿跌
- 鼠标按住可拖动，位置/透明度/主题自动记忆
- 右键菜单：刷新、置顶、不透明度、主题、退出

只依赖 Python 自带的 tkinter，无需安装任何第三方库。

用法：
    python widget.py            # 启动浮窗
    pythonw widget.py           # 启动浮窗（不弹黑色控制台窗口）
"""

import ctypes
import json
import os
from collections import deque
from ctypes import wintypes
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from tkinter import messagebox

from quotes import (
    DATA_DIR,
    DEFAULT_INTERVAL,
    MIN_INTERVAL,
    WATCHLIST_FILE,
    fetch_quotes,
    is_auction_now,
    is_market_day,
    is_quoting_now,
    load_watchlist,
    quote_interval,
    normalize,
    save_watchlist,
    search_symbols,
)

CONF_FILE = os.path.join(DATA_DIR, "widget.json")
LOCK_FILE = os.path.join(DATA_DIR, "widget.lock")

APP_VERSION = "1.2"
UPDATE_API = "https://api.github.com/repos/align-shi/stock-float/releases/latest"

WIDTH_MIN = 140          # 窗口最小宽度；实际宽度按内容算（见 calc_width）
PAD = 9                  # 左右内边距
ROW_GAP = 6              # 名称与右侧数字之间的最小间距
POLL_MS = 200            # 主循环轮询间隔：兼顾采集准时度与快捷键响应

# 全局快捷键：Alt+V 显示/隐藏。退出只走右键菜单，不再注册 Alt+Q。
HOTKEY_ID = 0xA1         # 本进程内唯一的注册号，随便取
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000    # 按住不放时只触发一次（Win7+）
VK_V = 0x56
WM_HOTKEY = 0x0312

# 涨跌异动提醒（盯的是实时价本身，与昨收无关）
ALERT_WINDOW = 5.0        # 观察窗口：只看最近这么多秒的价格
ALERT_PCT = 1.0           # 触发阈值：窗口内价格变动幅度（%）
ALERT_MAX_GAP = 10.0      # 两次采样间隔超过这个秒数就当成断档，重新攒窗口
ALERT_COOLDOWN = 10.0     # 同一只股票同方向触发的静默期（秒）
ALERT_MAX_BUBBLES = 3     # 同屏最多几个气泡
BUBBLE_GAP = 6            # 多个气泡之间的垂直间距
BUBBLE_LIFE = 5200        # 气泡停留时长（毫秒），之后开始淡出
BUBBLE_FADE = 14          # 淡出步数
BUBBLE_STEP_MS = 45       # 每步毫秒
BUBBLE_ALPHA = 0.97       # 气泡自身不透明度（比浮窗更醒目）

# 收盘自动退出
AUTO_CLOSE_AT = (15, 0, 5)   # 每天这个时刻自动关掉（收盘后 5 秒，让最后一笔数据落完）
AUTO_CLOSE_BYE_MS = 1400     # 退出前把"收盘了"的气泡留一会儿，让人看清为什么消失
QUIT_SETTLE_MS = 120         # 退出时先隐身、隔这么一会儿再销毁窗口（见 quit 的注释）
MENU_TEARDOWN_MS = 400       # 菜单收摊的延后毫秒：立刻拆会吞掉刚点的那个回调

THEMES = {
    "dark": {
        "border": "#3a4150",
        "bg": "#1b1e24",
        "text": "#e9ecf1",
        "muted": "#7b8290",
        "up": "#ff5a5c",
        "down": "#2fd07a",
        "flat": "#9aa1ad",
        "flash_up": "#4a2a2c",
        "flash_down": "#1d3d2e",
        "bubble_bg": "#242832",
        "bubble_sub": "#c3c9d4",
    },
    "light": {
        "border": "#d5d9e0",
        "bg": "#ffffff",
        "text": "#1f2329",
        "muted": "#8b909a",
        "up": "#d9342b",
        "down": "#17a35c",
        "flat": "#8b909a",
        "flash_up": "#fbdcdb",
        "flash_down": "#d4f2e3",
        "bubble_bg": "#ffffff",
        "bubble_sub": "#4a5058",
    },
}

DEFAULT_CONF = {
    "x": None,           # None = 首次启动自动贴到右上角
    "y": None,
    "alpha": 0.93,
    "theme": "dark",
    "topmost": True,
    "interval": DEFAULT_INTERVAL,
    "alert_enabled": True,
    "alert_pct": ALERT_PCT,
    "alert_hidden_too": True,   # 浮窗被 Alt+V 藏起来时，照样弹气泡
    "auto_close": True,         # 每天 15:00:05 自动退出
}


def hide_console():
    """脱离 python.exe 自带的控制台。

    只调用 ShowWindow 隐藏的话，窗口还在，用户一点关闭，进程就跟着退出。
    FreeConsole 之后这个黑窗口不再属于浮窗，关掉它也不会结束程序。
    """
    try:
        kernel = ctypes.windll.kernel32
        hwnd = kernel.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
        kernel.FreeConsole()
    except Exception:
        pass


_SINGLETON = None


def acquire_single_instance():
    """同一时间只允许一个浮窗实例，避免重复双击开出多个窗口"""
    global _SINGLETON
    try:
        import msvcrt
        fh = open(LOCK_FILE, "w")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        fh.write(str(os.getpid()))
        fh.flush()
        _SINGLETON = fh
        return True
    except Exception:
        return False


def log_error(msg, exc=None):
    """把异常写进 widget-error.log。

    浮窗的控制台是隐藏的，print 到 stderr 的东西谁也看不见 —— Tk 回调里
    抛出的异常（菜单项、定时器）默认就是这么被吞掉的，于是表现为
    "点了没反应、但程序还好好的"，极难排查。这里统一落文件。
    """
    try:
        with open(os.path.join(DATA_DIR, "widget-error.log"), "a", encoding="utf-8") as f:
            f.write("\n[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


def install_excepthook():
    """崩溃时把堆栈写进 widget-error.log，方便排查"""
    def hook(exc_type, exc, tb):
        try:
            with open(os.path.join(DATA_DIR, "widget-error.log"), "a", encoding="utf-8") as f:
                f.write("\n[%s]\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
    sys.excepthook = hook


def enable_dpi_awareness():
    """在高分屏上让文字保持清晰"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class StockWidget:
    def __init__(self):
        self.conf = self.load_conf()
        self.theme = THEMES.get(self.conf.get("theme"), THEMES["dark"])
        self.interval = max(MIN_INTERVAL, int(self.conf.get("interval") or DEFAULT_INTERVAL))

        self.watchlist = load_watchlist()
        self._watch_lock = threading.Lock()   # 采集线程读列表时，界面可能正在增删
        self.quotes = {}
        self.prev_price = {}
        self.rows = {}
        self.last_fetch = 0.0
        self._fetch_started = 0.0
        self.force = True
        self.err = None
        self.busy = False
        self.q = queue.Queue()
        self.hot_q = queue.Queue()   # 全局快捷键线程 → 主线程的投递口
        self.hidden = False
        self.hotkey_ok = None        # None=还没注册完，True=可用，False=被占用
        self.win_w = None        # 当前窗口宽度（按内容动态计算）
        self._dragging = False
        self._closing = False    # 正在退出：主循环与菜单据此不再碰 Tk
        self._loop_after = None  # 主循环的 after 句柄，退出时要先摘掉
        self._menu = None        # 当前挂着的右键菜单，退出前必须先收掉
        self._menu_loop = False  # tk_popup 的原生模态菜单循环是否还开着（见 quit）
        self._destroyed = False  # 主窗口是否已销毁（兜底线程据此判断要不要强杀）
        self._quit_pending = False  # 在菜单里点了退出，等菜单循环退干净再销毁
        self._quit_after = None     # 退出兜底计时器的句柄，销毁时要摘掉
        self._adding = False        # 添加股票的输入框是否已打开
        self._add_pending = False   # 菜单里点了添加，等菜单循环退出再弹框
        self._alpha_pending = False  # 菜单里点了自定义不透明度，等菜单收干净再弹框
        self._theme_pending = None  # 菜单里点了主题，等菜单收干净再重建界面

        # 涨跌异动提醒
        self.alert_on = bool(self.conf.get("alert_enabled", True))
        self.alert_pct = float(self.conf.get("alert_pct") or ALERT_PCT)
        self.price_hist = {}     # symbol -> deque[(ts, price)]，算窗口用
        self.alert_cd = {}       # symbol -> (静默到期时间, 上次方向)
        self.bubbles = []        # 正在显示的气泡窗口，按从上到下排列

        # 收盘自动退出
        self.auto_close = bool(self.conf.get("auto_close", True))
        self._ac_date = None     # 上次判定过的日期
        self._ac_at = None       # 那一天的触发时刻
        self._ac_armed = False   # 今天要不要等这个点
        self._ac_booted = False  # 是否已完成首次校准（区分"启动"与"跨天"）

        self.root = tk.Tk()
        self.root.title("行情浮窗")
        # Tk 回调（菜单项、定时器）里抛的异常默认只写 stderr，而我们的控制台
        # 是隐藏的 —— 等于凭空消失，表现为"点了没反应"。改写到日志文件。
        self.root.report_callback_exception = self._on_tk_error
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.conf.get("topmost", True)))
        self.root.attributes("-alpha", float(self.conf.get("alpha", 0.93)))
        self.root.configure(bg=self.theme["border"])

        self.var_top = tk.BooleanVar(value=bool(self.conf.get("topmost", True)))
        self.var_alert = tk.BooleanVar(value=self.alert_on)
        self.var_alert_hidden = tk.BooleanVar(value=bool(self.conf.get("alert_hidden_too", True)))
        self.var_auto_close = tk.BooleanVar(value=self.auto_close)

        self.init_fonts()
        self.build()

        self.root.after(120, self.start)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def load_conf(self):
        conf = dict(DEFAULT_CONF)
        if os.path.exists(CONF_FILE):
            try:
                with open(CONF_FILE, encoding="utf-8") as f:
                    conf.update(json.load(f) or {})
            except Exception:
                pass
        return conf

    def save_conf(self):
        try:
            with open(CONF_FILE, "w", encoding="utf-8") as f:
                json.dump(self.conf, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # 只吞 OSError 是不够的：退出时这里抛的异常会打断整个收尾流程
            log_error("保存配置失败", e)

    def _on_tk_error(self, exc, val, tb):
        """Tk 回调里的异常：落盘，别让它在隐藏控制台里无声消失"""
        log_error("Tk 回调异常: %s" % getattr(exc, "__name__", exc), val)

    def safe_after(self, ms, fn):
        """排一个定时器；排不上就直接执行，绝不把异常抛回调用方"""
        try:
            self.root.after(ms, fn)
            return True
        except Exception as e:
            log_error("after 排不上，改为立即执行", e)
            try:
                fn()
            except Exception as e2:
                log_error("立即执行也失败", e2)
            return False

    # ------------------------------------------------------------------
    # 字体与布局
    # ------------------------------------------------------------------
    def init_fonts(self):
        self.f_name = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")
        self.f_num = tkfont.Font(family="Consolas", size=10, weight="bold")
        self.f_bub = tkfont.Font(family="Microsoft YaHei UI", size=11, weight="bold")
        self.f_bub_sub = tkfont.Font(family="Microsoft YaHei UI", size=9)

    def build(self):
        t = self.theme
        for c in list(self.root.winfo_children()):
            # 右键菜单和气泡都挂在主窗口上。换主题时拆掉菜单，Windows 会留下
            # 一块空白框，要点一下才消失。
            if isinstance(c, (tk.Menu, tk.Toplevel)):
                continue
            c.destroy()
        self.rows = {}

        self.inner = tk.Frame(self.root, bg=t["bg"])
        self.inner.pack(fill="both", expand=True, padx=1, pady=1)

        # 去掉了顶部的状态行（市场状态 / 刷新倒计时 / 分隔线），只留行情本身
        self.body = tk.Frame(self.inner, bg=t["bg"])
        self.body.pack(fill="x", padx=PAD, pady=(7, 7))

        # 数据源异常时才浮现的一条提示；正常情况下 pack_forget，不占任何高度
        self.status_lbl = tk.Label(self.inner, text="", font=self.f_name,
                                   fg=t["up"], bg=t["bg"], anchor="w")
        self.status_on = False

        self.rebuild_rows()

    def calc_width(self, h=None):
        """按当前自选的实际字宽算窗口宽度，避免多余留白"""
        if not self.watchlist:
            return max(WIDTH_MIN, PAD * 2 + self.f_name.measure("未添加股票") + 4)
        need = 0
        for w in self.watchlist:
            name = w.get("name") or w["code"]
            q = self.quotes.get(w["symbol"]) or {}
            p = q.get("price")
            # 还没数据时按占位串量；拉到数据后 render() 会自动重算宽度
            txt = ("%.2f(%+.2f%%)" % (p, q.get("change_pct") or 0.0)) if p else "000.00(-00.00%)"
            need = max(need, self.f_name.measure(name) + ROW_GAP + self.f_num.measure(txt))
        return max(WIDTH_MIN, PAD * 2 + need + 4)

    def rebuild_rows(self):
        t = self.theme
        for c in self.body.winfo_children():
            c.destroy()
        self.rows = {}

        if not self.watchlist:
            tk.Label(self.body, text="未添加股票", font=self.f_name,
                     fg=t["muted"], bg=t["bg"], anchor="w").pack(fill="x")
            self.fit_and_bind()
            return

        for i, w in enumerate(self.watchlist):
            sym = w["symbol"]
            box = tk.Frame(self.body, bg=t["bg"])
            box.pack(fill="x", pady=(0 if i == 0 else 5, 0))
            box.columnconfigure(0, weight=1)

            name = tk.Label(box, text=w.get("name") or w["code"], font=self.f_name,
                            fg=t["text"], bg=t["bg"], anchor="w", bd=0)
            name.grid(row=0, column=0, sticky="w")

            # 右侧「价格(涨跌幅)」：单标签一个字体，保证 7.25(-4.61%) 紧贴无缝隙
            val = tk.Label(box, text="--", font=self.f_num, fg=t["muted"],
                           bg=t["bg"], anchor="e", bd=0)
            val.grid(row=0, column=1, sticky="e", padx=(ROW_GAP, 0))

            self.rows[sym] = {"name": name, "val": val}

        self.fit_and_bind()

    def fit_and_bind(self):
        """按内容算好宽高，摆到记忆位置，并重新绑定拖动/右键"""
        self.root.update_idletasks()
        w = self.calc_width()
        h = self.inner.winfo_reqheight()

        vx, vy, vw, vh = self.virtual_screen()
        old_right = (self.root.winfo_x() + self.win_w) if self.win_w else None
        x, y = self.resolve_pos(w, h)
        # 原本贴着虚拟桌面右缘时，改宽度后保持右边缘不动
        if old_right is not None and old_right > vx + vw - 60:
            x = vx + vw - w - 22
            self.conf["x"] = x
            self.save_conf()

        self.win_w = w
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.bind_tree(self.root)

    def virtual_screen(self):
        """整块虚拟桌面，含副屏。Tk 的 screenwidth 只有主屏。"""
        try:
            u = ctypes.windll.user32
            x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
            w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            if w > 0 and h > 0:
                return int(x), int(y), int(w), int(h)
        except Exception:
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def resolve_pos(self, w, h):
        vx, vy, vw, vh = self.virtual_screen()
        x, y = self.conf.get("x"), self.conf.get("y")
        if x is None:
            x = vx + vw - w - 22     # 首次启动：虚拟桌面右上角
        if y is None:
            y = vy + 76
        x = max(vx, min(int(x), vx + vw - w))
        y = max(vy, min(int(y), vy + vh - h))
        return x, y

    def bind_tree(self, w):
        w.bind("<ButtonPress-1>", self.on_press)
        w.bind("<B1-Motion>", self.on_drag)
        w.bind("<ButtonRelease-1>", self.on_release)
        w.bind("<Button-3>", self.popup)
        for c in w.winfo_children():
            # 添加股票等弹窗也是主窗口的子窗口，绑上拖动会让按钮点不了
            if isinstance(c, tk.Toplevel):
                continue
            self.bind_tree(c)

    # ------------------------------------------------------------------
    # 拖动
    # ------------------------------------------------------------------
    def on_press(self, e):
        self._dragging = True
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def on_drag(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))
        if self.bubbles:
            self.relayout_bubbles()     # 气泡跟着浮窗走

    def on_release(self, e=None):
        self._dragging = False
        vx, vy, vw, vh = self.virtual_screen()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        m = 16
        if x < vx + m:
            x = vx + 4
        elif x + w > vx + vw - m:
            x = vx + vw - w - 4
        if y < vy + m:
            y = vy + 4
        elif y + h > vy + vh - m:
            y = vy + vh - h - 4
        self.root.geometry("+%d+%d" % (x, y))
        self.conf["x"], self.conf["y"] = x, y
        self.save_conf()

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def popup(self, e):
        # 构造菜单和弹出都要碰 Tk：退出瞬间被右键会炸出一串
        # `can't invoke "menu"/"tk" command: application has been destroyed`
        if self._closing:
            return
        try:
            self.root.winfo_exists()
        except tk.TclError:
            return

        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="立即刷新", command=self.force_refresh)
        m.add_separator()
        m.add_checkbutton(label="窗口置顶", variable=self.var_top, command=self.toggle_top)

        sub = tk.Menu(m, tearoff=0)
        for a in (1.0, 0.93, 0.85, 0.75, 0.6, 0.5):
            sub.add_command(label="%d%%" % round(a * 100),
                            command=lambda v=a: self.set_alpha(v))
        sub.add_separator()
        sub.add_command(label="自定义…", command=self.custom_alpha)
        m.add_cascade(label="不透明度", menu=sub)

        subm = tk.Menu(m, tearoff=0)
        subm.add_command(label="深色", command=lambda: self.set_theme("dark"))
        subm.add_command(label="浅色", command=lambda: self.set_theme("light"))
        m.add_cascade(label="主题", menu=subm)

        suba = tk.Menu(m, tearoff=0)
        suba.add_checkbutton(label="开启涨跌提醒", variable=self.var_alert,
                             command=self.toggle_alert)
        subt = tk.Menu(suba, tearoff=0)
        for v in (0.5, 1.0, 2.0, 3.0):
            lbl = "%.1f%%%s" % (v, "  ✓" if abs(v - self.alert_pct) < 1e-9 else "")
            subt.add_command(label=lbl, command=lambda x=v: self.set_alert_pct(x))
        suba.add_cascade(label="阈值（%d秒内）" % int(ALERT_WINDOW), menu=subt)
        suba.add_checkbutton(label="隐藏浮窗时也提醒", variable=self.var_alert_hidden,
                             command=self.toggle_alert_hidden)
        suba.add_separator()
        suba.add_command(label="预览气泡效果", command=self.test_bubble)
        m.add_cascade(label="涨跌提醒", menu=suba)

        m.add_separator()
        subw = tk.Menu(m, tearoff=0)
        subw.add_command(label="添加股票…", command=self.add_stock)
        subw.add_separator()
        self.fill_remove_menu(subw)
        subw.add_separator()
        subw.add_command(label="重新载入自选", command=self.reload_watchlist)
        subw.add_command(label="编辑自选文件", command=self.edit_watchlist)
        m.add_cascade(label="自选管理", menu=subw)

        m.add_separator()
        if self.hotkey_ok is False:
            # 快捷键被别人占了，隐藏后就没有唤回的入口了，必须提前说清楚
            m.add_command(label="隐藏浮窗（Alt+V 被占用，慎用）",
                          command=self.hide_from_menu)
        else:
            m.add_command(label="隐藏浮窗  Alt+V", command=self.hide_widget)
        m.add_checkbutton(label="%02d:%02d:%02d 收盘自动退出" % AUTO_CLOSE_AT,
                          variable=self.var_auto_close, command=self.toggle_auto_close)
        m.add_separator()
        m.add_command(label="退出", command=self.quit)

        self._menu = m
        self._menu_loop = True
        try:
            m.tk_popup(e.x_root, e.y_root)
        except Exception as ex:
            # 弹出瞬间窗口被销毁（比如刚好点了"退出"、或收盘自动退出到点）
            log_error("tk_popup 异常", ex)
        finally:
            self._menu_loop = False
            try:
                m.grab_release()
            except tk.TclError:
                pass
            # 用完就拆：一是别留孤儿窗口（见 close_menu 的注释），
            # 二是每次右键都会新建一整套菜单，不拆就是持续泄漏。
            # 但**必须延后拆** —— 立刻拆会把用户刚点的那一项的回调吞掉，
            # 详见 destroy_menu_later 的注释（"删除自选点了没反应"就是它）。
            if self._menu is m:
                self._menu = None
            # 添加股票不能在菜单循环里弹框：循环没退时菜单还抓着鼠标，
            # 输入框的确定/取消点不到，菜单自己也关不掉。
            if self._add_pending:
                self.destroy_menu(m)
                self.root.after_idle(self._begin_add_prompt)
            elif self._alpha_pending:
                self.destroy_menu(m)
                self.root.after_idle(self._begin_alpha_prompt)
            elif self._theme_pending:
                name = self._theme_pending
                self._theme_pending = None
                self.destroy_menu(m)
                self.root.after_idle(lambda n=name: self._apply_theme(n))
            else:
                self.destroy_menu_later(m)

            # 用户是在菜单里点的"退出"时，在这里销毁最安全。
            # 注：实测这条分支其实走不到 —— 命令回调是在 tk_popup 返回**之后**
            # 才派发的，那时 _menu_loop 已被上面置回 False，quit() 会直接
            # 调 destroy_root()。留着它只是个不花钱的保险。
            if self._quit_pending:
                self._quit_pending = False
                self.destroy_root()

    def toggle_top(self):
        v = bool(self.var_top.get())
        self.root.attributes("-topmost", v)
        self.conf["topmost"] = v
        self.save_conf()

    def set_alpha(self, v):
        self.root.attributes("-alpha", float(v))
        self.conf["alpha"] = float(v)
        self.save_conf()

    def custom_alpha(self):
        """记下「要改不透明度」，等菜单循环退出再弹输入框。"""
        if self._adding or self._alpha_pending or self._closing:
            return
        if self._menu_loop:
            self._alpha_pending = True
            try:
                ctypes.windll.user32.EndMenu()
            except Exception as e:
                log_error("结束菜单失败", e)
            return
        self._begin_alpha_prompt()

    def _begin_alpha_prompt(self):
        if self._adding or self._closing or self._destroyed:
            self._alpha_pending = False
            return
        self._alpha_pending = False
        self._adding = True
        try:
            ctypes.windll.user32.EndMenu()
        except Exception as e:
            log_error("结束菜单失败", e)
        m = self._menu
        self._menu = None
        if m is not None:
            self.destroy_menu(m)
        try:
            self.root.after(0, self._prompt_alpha)
        except Exception as e:
            self._adding = False
            log_error("排不透明度对话框失败", e)

    def _prompt_alpha(self):
        try:
            if self._closing or self._destroyed:
                return
            cur = int(round(float(self.conf.get("alpha", 0.93)) * 100))
            q = self.ask_text("不透明度", "输入 10 到 100 的整数：", str(cur))
            if q is None or not str(q).strip():
                return
            alpha = parse_opacity(q)
            if alpha is None:
                messagebox.showwarning(
                    "不透明度", "请输入 10 到 100 之间的整数", parent=self.root)
                return
            self.set_alpha(alpha)
        finally:
            self._adding = False

    def set_theme(self, name):
        if name not in THEMES or self._closing:
            return
        if self._menu_loop:
            # 菜单还开着就重建界面，会把菜单拆掉，屏幕上留下一块空白框
            self._theme_pending = name
            try:
                ctypes.windll.user32.EndMenu()
            except Exception as e:
                log_error("结束菜单失败", e)
            return
        self._apply_theme(name)

    def _apply_theme(self, name):
        if name not in THEMES or self._closing or self._destroyed:
            return
        self.conf["theme"] = name
        self.save_conf()
        self.theme = THEMES[name]
        self.root.configure(bg=self.theme["border"])
        self.build()
        self.force_refresh()

    def edit_watchlist(self):
        if not os.path.exists(WATCHLIST_FILE):
            save_watchlist(self.watchlist, WATCHLIST_FILE)
        try:
            os.startfile(WATCHLIST_FILE)
        except Exception:
            messagebox.showinfo("自选列表位置", WATCHLIST_FILE)

    def fill_remove_menu(self, menu):
        if not self.watchlist:
            menu.add_command(label="（列表为空）", state="disabled")
            return
        for w in self.watchlist:
            label = "删除  %s %s" % (w.get("name") or "未命名", w["code"])
            menu.add_command(label=label,
                             command=lambda s=w["symbol"]: self.remove_stock(s))

    def remove_stock(self, symbol):
        with self._watch_lock:
            before = len(self.watchlist)
            self.watchlist = [w for w in self.watchlist if w["symbol"] != symbol]
        if len(self.watchlist) == before:
            return
        self.quotes.pop(symbol, None)
        self.prev_price.pop(symbol, None)
        self.price_hist.pop(symbol, None)
        self.alert_cd.pop(symbol, None)
        save_watchlist(self.watchlist)
        self.rebuild_rows()

    def add_stock(self):
        """只记下「要添加」，立刻结束原生菜单。

        定时器会在 tk_popup 的菜单循环里触发。若那时就 wait_window，
        菜单窗口不消失，而且它占着鼠标，确定/取消都点不到。
        """
        if self._adding or self._add_pending or self._closing:
            return
        if self._menu_loop:
            self._add_pending = True
            try:
                ctypes.windll.user32.EndMenu()
            except Exception as e:
                log_error("结束菜单失败", e)
            return
        self._begin_add_prompt()

    def _begin_add_prompt(self):
        if self._adding or self._closing or self._destroyed:
            self._add_pending = False
            return
        self._add_pending = False
        self._adding = True
        try:
            ctypes.windll.user32.EndMenu()
        except Exception as e:
            log_error("结束菜单失败", e)
        m = self._menu
        self._menu = None
        if m is not None:
            self.destroy_menu(m)
        try:
            self.root.after(0, self._prompt_add_stock)
        except Exception as e:
            self._adding = False
            log_error("排添加股票对话框失败", e)

    def _prompt_add_stock(self):
        try:
            if self._closing or self._destroyed:
                return
            q = self.ask_text("添加股票", "输入股票代码或名称：")
            if not q or not q.strip():
                return
            self._commit_add_stock(q.strip())
        finally:
            self._adding = False

    def ask_text(self, title, prompt, initial=""):
        """自绘输入框。不要用 simpledialog，也不要 transient 到浮窗。"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg="#ffffff")

        tk.Label(win, text=prompt, bg="#ffffff", fg="#1f2329",
                 font=self.f_name, anchor="w").pack(fill="x", padx=16, pady=(14, 8))

        var = tk.StringVar(value=initial)
        ent = tk.Entry(win, textvariable=var, font=self.f_name, width=28,
                       relief="solid", bd=1)
        ent.pack(fill="x", padx=16)

        result = {"v": None}

        def ok(event=None):
            result["v"] = var.get()
            win.destroy()

        def cancel(event=None):
            win.destroy()

        box = tk.Frame(win, bg="#ffffff")
        box.pack(fill="x", padx=16, pady=(12, 14))
        tk.Button(box, text="取消", font=self.f_name, width=8,
                  command=cancel).pack(side="right")
        tk.Button(box, text="确定", font=self.f_name, width=8,
                  command=ok).pack(side="right", padx=(0, 8))

        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.update_idletasks()
        w_, h_ = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % ((win.winfo_screenwidth() - w_) // 2,
                                 (win.winfo_screenheight() - h_) // 3))
        win.lift()
        win.update()
        self._focus_dialog(win)
        try:
            win.grab_set()
        except tk.TclError as e:
            log_error("对话框未能独占输入", e)
        ent.focus_set()
        if initial:
            ent.selection_range(0, "end")
        self.root.wait_window(win)
        return result["v"]

    def _focus_dialog(self, win):
        """把输入框拉到前台。无边框浮窗当父窗口时，不抢前台按钮收不到点击。"""
        win.focus_force()
        try:
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            log_error("输入框置前失败", e)

    def _commit_add_stock(self, q):
        symbol = normalize(q)
        name = ""
        if not symbol:
            try:
                cands = search_symbols(q)
            except Exception as e:
                messagebox.showerror("搜索失败", str(e), parent=self.root)
                return
            if not cands:
                messagebox.showwarning("未找到", "没找到「%s」，换个代码或名称再试" % q,
                                       parent=self.root)
                return
            if len(cands) > 1:
                picked = self.pick_candidate(cands)
                if not picked:
                    return
                symbol, name = picked["symbol"], picked["name"]
            else:
                symbol, name = cands[0]["symbol"], cands[0]["name"]

        if any(w["symbol"] == symbol for w in self.watchlist):
            messagebox.showinfo("提示", "%s 已经在列表里了" % symbol, parent=self.root)
            return

        with self._watch_lock:
            self.watchlist.append({"symbol": symbol, "code": symbol[2:], "name": name})
        save_watchlist(self.watchlist)
        self.rebuild_rows()
        self.force = True

    def pick_candidate(self, cands):
        """名称命中多只时，让用户显式选择，不做自动猜测"""
        win = tk.Toplevel(self.root)
        win.title("选择股票")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg="#ffffff")

        tk.Label(win, text="找到多只股票，请选择：", bg="#ffffff", fg="#1f2329",
                 font=self.f_name, anchor="w").pack(fill="x", padx=16, pady=(14, 8))

        picked = {"v": None}

        def choose(c, w=win):
            picked["v"] = c
            w.destroy()

        for c in cands[:12]:
            tk.Button(win, text="%s    %s · %s" % (c["name"], c["code"], c["market"]),
                      font=self.f_name, relief="flat", anchor="w", cursor="hand2",
                      bg="#f4f5f7", activebackground="#e2edff", padx=14, pady=7,
                      command=lambda cc=c: choose(cc)).pack(fill="x", padx=16, pady=2)

        tk.Frame(win, bg="#ffffff", height=10).pack()
        win.update_idletasks()
        w_, h_ = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % ((win.winfo_screenwidth() - w_) // 2,
                                 (win.winfo_screenheight() - h_) // 3))
        win.grab_set()
        win.focus_force()
        self.root.wait_window(win)
        return picked["v"]

    def reload_watchlist(self):
        loaded = load_watchlist()
        with self._watch_lock:
            self.watchlist = loaded
        self.rebuild_rows()
        self.force = True

    def force_refresh(self):
        self.force = True

    # ------------------------------------------------------------------
    # 显示 / 隐藏（全局快捷键 Alt+V）
    # ------------------------------------------------------------------
    def hotkey_loop(self):
        """在工作线程里注册全局快捷键并用消息循环等它。

        hWnd 传 NULL = 把快捷键挂到本线程上：这样不管浮窗有没有焦点、
        甚至已经被 withdraw 掉了，按键都能收到。WM_HOTKEY 会投递到
        本线程的消息队列，GetMessageW 取出后丢给主线程处理。
        """
        u = ctypes.windll.user32
        ok_v = u.RegisterHotKey(None, HOTKEY_ID, MOD_ALT | MOD_NOREPEAT, VK_V)
        self.hotkey_ok = bool(ok_v)
        if not ok_v:
            return              # 没注册上，线程没消息可等
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.hot_q.put("toggle")

    def drain_hotkey(self):
        try:
            while True:
                self.hot_q.get_nowait()
                self.toggle_visible()
        except queue.Empty:
            pass

    def toggle_visible(self):
        if self.hidden:
            self.show_widget()
        else:
            self.hide_widget()

    def hide_from_menu(self):
        if not messagebox.askokcancel(
                "行情浮窗",
                "Alt+V 已被别的程序占用，隐藏后只能重启浮窗才能再看到。\n仍要隐藏吗？"):
            return
        self.hide_widget()

    def hide_widget(self):
        # 先记下位置：浮窗是 overrideredirect 的，某些平台 withdraw→deiconify
        # 一轮会丢掉无边框标志和位置，恢复时按这里重新摆
        self.conf["x"] = self.root.winfo_x()
        self.conf["y"] = self.root.winfo_y()
        self.save_conf()
        self.root.withdraw()
        self.hidden = True

    def show_widget(self):
        self.hidden = False
        self.root.deiconify()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.conf.get("topmost", True)))
        self.root.attributes("-alpha", float(self.conf.get("alpha", 0.93)))
        self.fit_and_bind()

    def destroy_menu_later(self, m):
        """延后拆菜单。**立刻拆会把用户刚点的那一项的回调整个吞掉。**

        `tk_popup` 返回 ≠ 命令已经派发完。Windows 上 TrackPopupMenu 一结束，
        Tk 才把用户选中的项当作一次**排队事件**回调出去；此刻紧接着
        destroy() 掉菜单对象，那次派发就找不到菜单了 —— 回调不执行、不抛异常、
        widget-error.log 里一个字都没有，表现就是"点了完全没反应"。

        实测（真实 widget.py + 真实鼠标点原生菜单）：

            立即 unpost+destroy   → 回调未触发   ← 曾经的现状
            什么都不做             → 回调触发
            只 unpost              → 回调触发
            只 destroy             → 回调未触发
            延后 1ms 再 unpost+destroy → 回调触发
            延后 400ms 再 unpost+destroy → 回调触发

        所以元凶是 destroy() 的时机，与 unpost() 无关。受影响的**不只是删除自选**，
        菜单里每一项都哑掉了，包括"退出"（这也是"点退出没反应"的真因之一，
        另一部分原因是当时跑的是旧进程）。

        延后拆不会让菜单窗口残留：点击后 #32768 本身就已经消失（实测 0.4s
        与 2.2s 时残留数都是 0），这里只是回收 Tcl 菜单对象、避免每次右键泄漏
        一整套菜单。
        """
        def run():
            if self._closing or self._destroyed:
                return              # 正在退出 / 已销毁，别再碰 Tk
            self.destroy_menu(m)

        try:
            self.root.after(MENU_TEARDOWN_MS, run)
        except Exception as e:
            # 排不上就算了：宁可漏回收一套菜单，也别让收尾流程在这里断掉
            log_error("菜单延后收摊排不上", e)

    def destroy_menu(self, m):
        """把菜单收起来再拆掉，两步都不能少

        - unpost() 才是让菜单从屏幕上消失的那一步（销毁 widget 不一定来得及
          在窗口活着的时候把它撤下去）
        - destroy() 释放整套菜单对象，连同它的级联子菜单
        """
        for fn in (m.unpost, m.destroy):
            try:
                fn()
            except Exception as e:
                log_error("收菜单失败: %s" % getattr(fn, "__name__", fn), e)

    def close_menu(self):
        """让还挂着的右键菜单收摊。

        EndMenu() 是这里唯一靠得住的一步：Windows 上 tk_popup 走原生模态
        循环（TrackPopupMenuEx），#32768 菜单窗口由 user32 托管，Tk 自己的
        unpost() 管不了它。循环没结束就销毁解释器，菜单窗口会变成孤儿，
        一块 SystemButtonFace 灰的空白矩形杵在屏幕上（实测 186x250 纯
        #f0f0f0，一个字的像素都没有），直到用户点到别处才消失。

        **在菜单回调里（_menu_loop=True）绝不销毁菜单自己** —— 那正是
        "点退出没反应"的元凶：销毁一个正在被 track 的菜单会打断 Tk，
        收尾流程从此走不下去，_closing 卡在 True，之后每次点退出都石沉大海。
        菜单对象留给 popup() 的 finally 去拆，那时循环已经退干净了。
        """
        try:
            ctypes.windll.user32.EndMenu()
        except Exception as e:
            log_error("EndMenu 失败", e)

        if self._menu_loop:
            return                      # 正在菜单回调里，别动菜单对象

        m = self._menu
        self._menu = None
        if m is not None:
            self.destroy_menu(m)

    def dismiss_bubbles(self):
        """气泡也是无边框 Toplevel，退出前先隐身，别留下同样的孤儿"""
        for win in list(self.bubbles):
            try:
                win.withdraw()
            except Exception as e:
                log_error("收气泡失败", e)
        self.bubbles = []

    def quit(self):
        """干净退出 —— 但**无论如何都要退出去**。

        这条路径踩过一次实打实的故障：收尾阶段某一步抛异常，_closing 就永远
        留在 True，窗口不销毁也不隐身，而后面的每次"退出"都被开头的守卫
        直接挡掉 —— 用户看到的就是"点退出完全没反应"，程序却活得好好的
        （进程在、窗口响应正常、CPU 0%，一点崩溃的迹象都没有）。

        所以这里的写法是：收尾的任何一步都各管各的、绝不外抛，真正的销毁
        放在 finally 里 —— 哪怕前面全崩了，窗口也必须消失。

        顺序仍然是讲究的：

        1. EndMenu()：结束原生菜单模态循环（详见 close_menu 的注释）。
        2. 摘掉主循环的 after：否则 destroy() 之后那个已排队的回调还会
           触发一次，终端里刷 `invalid command name "...loop"`。
        3. 收气泡、存位置。
        4. 销毁。若此刻还在菜单回调里（点了"退出"菜单项），不要当场销毁
           —— 交给 popup() 的 finally，那时模态循环已经退干净；同时挂一个
           1.5 秒的定时器兜底，万一 finally 没来也不会一直挂着。
        """
        if self._closing:
            return                  # 重复调用（比如退出瞬间又点了菜单）直接忽略
        self._closing = True

        try:
            self.close_menu()

            if self._loop_after is not None:
                try:
                    self.root.after_cancel(self._loop_after)
                except Exception:
                    pass
                self._loop_after = None

            self.dismiss_bubbles()

            try:
                self.conf["x"] = self.root.winfo_x()
                self.conf["y"] = self.root.winfo_y()
            except Exception:
                pass
            self.save_conf()
        except Exception as e:
            # 记下来就行，绝不能让它挡住下面的销毁
            log_error("退出收尾阶段异常（已忽略，继续销毁）", e)
        finally:
            self.finish_quit()

    def finish_quit(self):
        """销毁主窗口。在菜单循环里就先记下，等 popup 的 finally 来收。"""
        if self._destroyed:
            return

        # 兜底：万一销毁没生效，也不能留一个看不见的进程在后台空转
        try:
            threading.Thread(target=self.force_exit_later, daemon=True).start()
        except Exception as e:
            log_error("兜底线程起不来", e)

        if self._menu_loop:
            # 还在 tk_popup 的模态循环里：EndMenu 已请求它退出，popup() 的
            # finally 马上会执行，那时销毁最干净。这里只留个兜底计时器。
            self._quit_pending = True
            try:
                self._quit_after = self.root.after(1500, self.destroy_root)
            except Exception as e:
                log_error("兜底计时器排不上", e)
                self.destroy_root()
            return

        self.destroy_root()

    def destroy_root(self):
        if self._destroyed:
            return
        # 摘掉自己挂的兜底计时器：否则销毁后它还会触发一次，
        # 只在 stderr 里留一行 `invalid command name "...destroy_root"`
        if self._quit_after is not None:
            try:
                self.root.after_cancel(self._quit_after)
            except Exception:
                pass
            self._quit_after = None
        try:
            self.root.destroy()
        except Exception as e:
            log_error("销毁主窗口失败", e)
        finally:
            self._destroyed = True

    def force_exit_later(self, secs=6.0):
        """退出兜底：到点还没真正销毁，就强杀自己"""
        time.sleep(secs)
        if not self._destroyed:
            os._exit(0)

    # ------------------------------------------------------------------
    # 收盘自动退出
    # ------------------------------------------------------------------
    def maybe_auto_close(self, now=None):
        """到点收工：每天 15:00:05 自动退出。

        判定用的是"跨越时刻"而不是"当前时间晚于该时刻"，两种情况要分开：

        - **启动校准**：启动时若已经过了今天的点（比如晚上复盘才开），
          今天就不再等了，否则一启动就被自己杀掉。
        - **跨天重新武装**：过了零点就是新的一天，无条件等当天的点。
          这里不能沿用启动那套判断，否则休眠醒来正好卡在 15:00:05，
          会被当成"启动时已过点"而白白漏掉一整天。

        两种情况下，"过期才醒"（比如 14:50 休眠、15:30 醒）都会补一次退出。
        """
        if not self.auto_close:
            return False
        now = now or datetime.now()

        # 周末和节假日没有收盘这一说，不要在 15:00 把浮窗关掉
        if not is_market_day(now):
            if self._ac_date != now.date():
                self._ac_booted = True
                self._ac_date = now.date()
                self._ac_armed = False
            return False

        if self._ac_date != now.date():
            calibrating = not self._ac_booted
            self._ac_booted = True
            self._ac_date = now.date()
            self._ac_at = now.replace(hour=AUTO_CLOSE_AT[0],
                                      minute=AUTO_CLOSE_AT[1],
                                      second=AUTO_CLOSE_AT[2],
                                      microsecond=0)
            self._ac_armed = (now < self._ac_at) if calibrating else True

        if not self._ac_armed or now < self._ac_at:
            return False

        # 只触发一次：退出前还有几百毫秒，别反复进来
        self._ac_armed = False
        self.close_for_day()
        return True

    def close_for_day(self):
        """收尾：留个气泡说明一声，然后干净退出"""
        if self.hidden:
            # 藏起来的浮窗没有好告别的位置，直接走
            self.root.after(60, self.quit)
            return
        self.make_bubble("收盘 %02d:%02d:%02d" % AUTO_CLOSE_AT,
                         "浮窗自动退出，明天见", self.theme["flat"])
        self.root.after(AUTO_CLOSE_BYE_MS, self.quit)

    def toggle_auto_close(self):
        self.auto_close = bool(self.var_auto_close.get())
        self.conf["auto_close"] = self.auto_close
        self.save_conf()
        if self.auto_close:
            # 重新打开时按"现在"重新校准：已经过了今天的点就不补退，等明天
            self._ac_date = None
            self._ac_booted = False

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        self.root.after(300, self.loop)
        self.root.after(2000, self.schedule_update_check)

    def schedule_update_check(self):
        """安装版启动后看一眼 GitHub 最新 Release，不打断开窗。"""
        if not getattr(sys, "frozen", False) or self._closing:
            return
        threading.Thread(target=self.fetch_update, daemon=True).start()

    def fetch_update(self):
        try:
            req = urllib.request.Request(UPDATE_API, headers={
                "User-Agent": "stock-float",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            remote_tag = data.get("tag_name") or ""
            remote = version_tuple(remote_tag)
            local = version_tuple(APP_VERSION)
            if not remote or not local or remote <= local:
                return
            if str(self.conf.get("update_seen") or "") == remote_tag:
                return
            url = ""
            for asset in data.get("assets") or []:
                if (asset.get("name") or "").lower().endswith(".exe"):
                    url = asset.get("browser_download_url") or ""
                    break
            if not url:
                url = data.get("html_url") or ""
            title = data.get("name") or remote_tag
        except Exception as e:
            log_error("检查更新失败", e)
            return
        try:
            self.root.after(0, lambda: self.prompt_update(remote_tag, title, url))
        except Exception as e:
            log_error("安排更新提示失败", e)

    def prompt_update(self, remote_tag, title, url):
        if self._closing or self._destroyed:
            return
        go = messagebox.askyesno(
            "行情浮窗",
            "发现新版本 %s。\n\n点“是”后会自动下载并安装，浮窗会先退出，装好后自动重新打开。" % title,
            parent=self.root,
        )
        if not go:
            self.conf["update_seen"] = remote_tag
            self.save_conf()
            return
        if not url.lower().split("?", 1)[0].endswith(".exe"):
            webbrowser.open(url)
            return
        self._show_update_status("正在下载更新…")
        threading.Thread(target=self._download_and_apply, args=(url,), daemon=True).start()

    def _show_update_status(self, text):
        win = tk.Toplevel(self.root)
        win.title("行情浮窗")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg="#ffffff")
        lbl = tk.Label(win, text=text, bg="#ffffff", fg="#1f2329",
                       font=self.f_name, padx=24, pady=18)
        lbl.pack()
        win.update_idletasks()
        w_, h_ = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % ((win.winfo_screenwidth() - w_) // 2,
                                 (win.winfo_screenheight() - h_) // 3))
        win.lift()
        self._update_win = win
        self._update_lbl = lbl

    def _set_update_status(self, text):
        lbl = getattr(self, "_update_lbl", None)
        if lbl is not None:
            try:
                lbl.config(text=text)
            except tk.TclError:
                pass

    def _download_and_apply(self, url):
        try:
            path = download_installer(url)
        except Exception as e:
            log_error("下载更新失败", e)
            try:
                self.root.after(0, lambda: self._update_failed("下载失败，请稍后再试。"))
            except Exception:
                pass
            return
        try:
            self.root.after(0, lambda: self._apply_downloaded(path))
        except Exception as e:
            log_error("安排安装失败", e)

    def _update_failed(self, text):
        win = getattr(self, "_update_win", None)
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        self._update_win = None
        if not self._closing and not self._destroyed:
            messagebox.showwarning("行情浮窗", text, parent=self.root)

    def _apply_downloaded(self, path):
        if self._closing or self._destroyed:
            return
        self._set_update_status("正在安装，浮窗会自动重新打开…")
        try:
            launch_installer(path)
        except Exception as e:
            log_error("启动安装包失败", e)
            self._update_failed("无法启动安装包。")
            return
        self.quit()

    def eff_interval(self):
        """实际使用的间隔：集合竞价 10 秒；连续竞价按配置。失败时放慢。"""
        base = quote_interval() if is_auction_now() else self.interval
        return base if not self.err else max(15, base * 5)

    def loop(self):
        if self._closing:
            return                  # 已经在退出了，别再碰 Tk
        self.drain()
        self.drain_hotkey()
        now = time.time()
        if not self.busy and self.watchlist:
            if self.force or (is_quoting_now() and now - self.last_fetch >= self.eff_interval()):
                self.force = False
                self.busy = True
                self._fetch_started = now   # 以发起时刻计周期，网络耗时不算进去
                threading.Thread(target=self.worker, daemon=True).start()
        self.update_header()
        if self.maybe_auto_close():
            return                  # 已经在关闭流程里了，别再排下一轮
        self._loop_after = self.root.after(POLL_MS, self.loop)

    def worker(self):
        try:
            with self._watch_lock:
                syms = [w["symbol"] for w in self.watchlist]
            quotes, source, err = fetch_quotes(syms)
        except Exception as e:
            quotes, err = {}, str(e)
        self.q.put((quotes, err))

    def drain(self):
        try:
            while True:
                quotes, err = self.q.get_nowait()
                self.busy = False
                self.last_fetch = self._fetch_started or time.time()
                self.err = err
                if quotes:
                    self.quotes.update(quotes)
                self.render()
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render(self):
        t = self.theme
        for w in self.watchlist:
            sym = w["symbol"]
            row = self.rows.get(sym)
            q = self.quotes.get(sym)
            if not row or not q:
                continue

            if not w.get("name") and q.get("name"):
                # 名称是从行情里学到的，顺手存下来：否则下次启动时
                # 名称栏会先退化成股票代码（与"隐藏代码"的诉求冲突）
                w["name"] = q["name"]
                row["name"].config(text=w["name"])
                save_watchlist(self.watchlist)

            pct = q.get("change_pct", 0.0) or 0.0
            col = t["up"] if pct > 0.001 else t["down"] if pct < -0.001 else t["flat"]
            row["val"].config(text="%.2f(%+.2f%%)" % (q["price"], pct), fg=col)

            prev = self.prev_price.get(sym)
            if prev is not None and abs(prev - q["price"]) > 1e-9:
                self.flash((row["val"],), q["price"] > prev)
            self.prev_price[sym] = q["price"]

            self.check_alert(sym, q, w)

        # 数字位数变化时同步窗口宽度（拖动中不动，避免打架）
        if not self._dragging:
            want = self.calc_width()
            if self.win_w and want != self.win_w:
                self.fit_and_bind()

        self.update_header()

    def flash(self, labels, up):
        hot = self.theme["flash_up"] if up else self.theme["flash_down"]
        base = self.theme["bg"]
        for lbl in labels:
            lbl.config(bg=hot)

        def restore():
            for lbl in labels:
                try:
                    lbl.config(bg=base)
                except tk.TclError:
                    pass

        self.root.after(500, restore)

    # ------------------------------------------------------------------
    # 涨跌异动提醒（气泡）
    # ------------------------------------------------------------------
    def check_alert(self, sym, q, w):
        """盯着实时价：最近几秒内价格跳动超过阈值就弹气泡。

        比的是价格自己，不是相对昨收的涨跌幅——2 秒内从 2.00 跳到 3.00
        这种，昨收口径可能只几个点，价格口径才是真实的 50%。
        """
        now = time.time()
        price = q.get("price") or 0.0
        if price <= 0:
            return

        hist = self.price_hist.get(sym)
        if hist is None:
            hist = self.price_hist[sym] = deque()
        # 断档（午间休市、断网、电脑休眠）里的价差不属于"几秒内"，
        # 不清掉就会把半小时的走势当成 5 秒的异动
        if hist and now - hist[-1][0] > ALERT_MAX_GAP:
            hist.clear()
        hist.append((now, price))
        cutoff = now - ALERT_WINDOW
        while len(hist) > 1 and hist[0][0] < cutoff:
            hist.popleft()              # 窗口外的点丢掉

        if not self.alert_on:
            return
        if self.hidden and not self.conf.get("alert_hidden_too", True):
            return
        if len(hist) < 2:
            return                      # 只有一笔，谈不上变动

        hi_t, hi = max(hist, key=lambda x: x[1])
        lo_t, lo = min(hist, key=lambda x: x[1])
        if lo <= 0:
            return

        # 方向按现价在窗口高低区间里的位置定：
        # 偏上就是拉升（从低点到现价），偏下就是跳水（从高点到现价）
        if price >= (hi + lo) / 2.0:
            base, base_t = lo, lo_t
            delta = (price - lo) / lo * 100.0
        else:
            base, base_t = hi, hi_t
            delta = (price - hi) / hi * 100.0        # 负值
        if abs(delta) < self.alert_pct:
            return

        # 同一方向短时间内只提醒一次，否则一路单边会弹个没完；反向立刻放行
        last = self.alert_cd.get(sym)
        if last and now < last[0] and (last[1] > 0) == (delta > 0):
            return
        self.alert_cd[sym] = (now + ALERT_COOLDOWN, delta)

        self.pop_bubble(w.get("name") or q.get("name") or sym,
                        delta, base, price, now - base_t)

    def pop_bubble(self, name, delta, p_from, p_to, span):
        """异动气泡：色带 + 异动摘要 + 起止价，几秒后自动淡出"""
        up = delta > 0
        self.make_bubble(
            "%s %d秒%s %.2f%%" % ("▲" if up else "▼", max(1, int(round(span))),
                                  "拉升" if up else "跳水", abs(delta)),
            "%s  %.2f → %.2f" % (name, p_from, p_to),
            self.theme["up"] if up else self.theme["down"],
        )

    def make_bubble(self, head_text, sub_text, accent):
        """气泡底座：左侧 accent 色带 + 主标题 + 副标题，5 秒后自动淡出"""
        t = self.theme

        # 同时来好几个也只留最新的几个，别把屏幕糊满
        while len(self.bubbles) >= ALERT_MAX_BUBBLES:
            self.close_bubble(self.bubbles[0])

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", BUBBLE_ALPHA)
        win.configure(bg=accent)        # 外框露出的 3px 就是左侧色带

        inner = tk.Frame(win, bg=t["bubble_bg"])
        inner.pack(fill="both", expand=True, padx=(3, 1), pady=1)

        head = tk.Label(inner, text=head_text, font=self.f_bub,
                        fg=accent, bg=t["bubble_bg"], anchor="w")
        head.pack(fill="x", padx=10, pady=(7, 0))

        tk.Label(inner, text=sub_text, font=self.f_bub_sub, fg=t["bubble_sub"],
                 bg=t["bubble_bg"], anchor="w").pack(fill="x", padx=10, pady=(2, 8))

        self.bubbles.append(win)
        win.update_idletasks()
        bw = max(168, win.winfo_reqwidth())
        bh = win.winfo_reqheight()
        x, y = self.bubble_pos(bw, bh, len(self.bubbles) - 1)
        win.geometry("%dx%d+%d+%d" % (bw, bh, x, y))

        def click_close(_e=None, w=win):
            self.close_bubble(w)

        def bind_click(widget):
            widget.bind("<Button-1>", click_close)
            for c in widget.winfo_children():
                bind_click(c)
        bind_click(win)

        self.root.after(BUBBLE_LIFE, lambda w=win: self.fade_bubble(w))

    def bubble_pos(self, bw, bh, slot):
        """气泡贴着浮窗摆：优先浮窗正下方，下方不够就往上叠，左右不出屏"""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if self.hidden:
            # 浮窗被藏起来了，只能按配置里记的位置算
            wx = int(self.conf.get("x") or (sw - bw - 22))
            wy = int(self.conf.get("y") or 76)
            ww, wh = self.win_w or WIDTH_MIN, 60
        else:
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            ww = self.root.winfo_width() or (self.win_w or WIDTH_MIN)
            wh = self.root.winfo_height() or 60

        x = wx + ww - bw                # 先跟浮窗右对齐
        if x + bw > sw - 8:
            x = sw - bw - 8
        if x < 8:
            x = 8

        offset = slot * (bh + BUBBLE_GAP)
        y = wy + wh + 6 + offset
        if y + bh > sh - 40:
            y = wy - bh - 6 - offset
        if y < 8:
            y = 8
        return x, y

    def fade_bubble(self, win, step=0):
        if win not in self.bubbles:
            return
        if step >= BUBBLE_FADE:
            self.close_bubble(win)
            return
        try:
            win.attributes("-alpha", max(0.0, BUBBLE_ALPHA * (1 - (step + 1) / float(BUBBLE_FADE))))
        except tk.TclError:
            return
        self.root.after(BUBBLE_STEP_MS, lambda: self.fade_bubble(win, step + 1))

    def close_bubble(self, win):
        if win in self.bubbles:
            self.bubbles.remove(win)
        try:
            win.destroy()
        except tk.TclError:
            pass
        self.relayout_bubbles()

    def relayout_bubbles(self):
        """关掉一个或拖动浮窗后，把剩下的气泡重新码好"""
        for i, w in enumerate(self.bubbles):
            try:
                x, y = self.bubble_pos(w.winfo_width(), w.winfo_height(), i)
                w.geometry("+%d+%d" % (x, y))
            except tk.TclError:
                pass

    def toggle_alert(self):
        self.alert_on = bool(self.var_alert.get())
        self.conf["alert_enabled"] = self.alert_on
        self.save_conf()

    def set_alert_pct(self, v):
        self.alert_pct = float(v)
        self.conf["alert_pct"] = float(v)
        self.save_conf()

    def toggle_alert_hidden(self):
        v = bool(self.var_alert_hidden.get())
        self.conf["alert_hidden_too"] = v
        self.save_conf()

    def test_bubble(self):
        """菜单里的预览：一涨一跌两个气泡，顺便验证堆叠与配色"""
        self.pop_bubble("大有能源", 1.35, 7.10, 7.25, 5.0)
        self.pop_bubble("测试股票", -1.85, 16.90, 16.60, 4.0)

    def update_header(self):
        """顶部状态行已移除，这里只剩一件事：数据源异常时浮出一条提示。

        价格是钱的事，静默失败比多一行提示危险得多，所以异常态仍然保留。
        """
        t = self.theme
        want = bool(self.err)
        if want:
            self.status_lbl.config(text="数据源异常，价格可能已过期", fg=t["up"])
        if want != self.status_on:
            self.status_on = want
            if want:
                self.status_lbl.pack(fill="x", padx=PAD, pady=(0, 7))
            else:
                self.status_lbl.pack_forget()
            self.fit_and_bind()


def download_installer(url):
    """把 GitHub Release 里的安装包下到临时目录，并确认是 Windows 可执行文件。"""
    folder = os.path.join(tempfile.gettempdir(), "stock-float-update")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "setup.exe")
    req = urllib.request.Request(url, headers={"User-Agent": "stock-float"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) < 64 or not data.startswith(b"MZ"):
        raise ValueError("下载到的文件不是安装包")
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def launch_installer(path):
    """静默覆盖安装。安装结束会重新打开浮窗，当前进程随后退出。"""
    subprocess.Popen(
        [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
         "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS"],
        close_fds=False,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


def parse_opacity(text):
    """把用户输入换成 0.10–1.00。只接受 10 到 100 的整数。"""
    s = (text or "").strip().rstrip("%").strip()
    if not s or not s.isdigit():
        return None
    n = int(s)
    if n < 10 or n > 100:
        return None
    return n / 100.0


def version_tuple(text):
    """把 'v1.2.3' / '1.1' 变成可比较的数字元组。认不出就返回 None。"""
    nums = []
    for part in (text or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return None
        nums.append(int(digits))
    return tuple(nums) if nums else None


def seed_bundled_watchlist():
    """安装版第一次启动时，把打包进去的自选拷到用户目录。"""
    if not getattr(sys, "frozen", False) or os.path.exists(WATCHLIST_FILE):
        return
    bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "watchlist.json")
    if not os.path.isfile(bundled):
        return
    try:
        with open(bundled, encoding="utf-8") as src:
            text = src.read()
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as dst:
            dst.write(text)
    except OSError as e:
        log_error("初始化自选失败", e)


def main():
    seed_bundled_watchlist()
    hide_console()
    install_excepthook()
    enable_dpi_awareness()
    if not acquire_single_instance():
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("行情浮窗",
                                "浮窗已经在运行了（可能被 Alt+V 隐藏着）。\n\n"
                                "按 Alt+V 切换显示 / 隐藏；\n"
                                "要退出，在浮窗上点右键 → 退出。")
            r.destroy()
        except Exception:
            pass
        return
    StockWidget().root.mainloop()


if __name__ == "__main__":
    main()
