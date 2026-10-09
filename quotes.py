# -*- coding: utf-8 -*-
"""
A 股行情底层模块

提供数据源、代码解析、交易时段判断、自选列表持久化。
供 monitor.py（网页看板）与 widget.py（桌面浮窗）共用。
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """自选、窗口位置、日志的存放目录。

    安装版 exe 不能写在程序旁边或临时解压目录里，否则退出或覆盖安装会丢数据。
    源码直接运行时仍放在项目目录，方便调试。
    """
    if getattr(sys, "frozen", False):
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(root, "行情浮窗")
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        return path
    return BASE_DIR


DATA_DIR = data_dir()
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")

DEFAULT_INTERVAL = 2                           # 连续竞价采集间隔（秒）
AUCTION_INTERVAL = 10                          # 早盘集合竞价采集间隔（秒）
MIN_INTERVAL = 1                               # 允许的最快采集间隔
DEFAULT_WATCHLIST = ["sh600403", "sz002457"]   # 首次启动的示例自选

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MARKET_NAMES = {"sh": "沪A", "sz": "深A", "bj": "北交所"}


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# --------------------------------------------------------------------------
# 交易时段
# --------------------------------------------------------------------------

# 上交所 2026 年休市里落在周一至周五的日期。周末一律休市，调休上班的周六日也不开市。
_CLOSED_2026 = {
    date(2026, 1, 1), date(2026, 1, 2),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 23),
    date(2026, 4, 6),
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    date(2026, 6, 19),
    date(2026, 9, 25),
    date(2026, 10, 1), date(2026, 10, 2),
    date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
}


def is_market_day(now=None):
    """当天交易所是否开市（不含具体钟点）。周末和 2026 年节假日为 False。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    if now.year == 2026 and now.date() in _CLOSED_2026:
        return False
    return True


def market_state(now=None):
    """返回 (状态, 描述)。状态取值：auction / open / break / closed"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "closed", "周末休市"
    if now.year == 2026 and now.date() in _CLOSED_2026:
        return "closed", "节假日休市"
    t = now.hour * 60 + now.minute
    # 9:15-9:25 早盘集合竞价；9:25 之后到 9:30 不再拉
    if 555 <= t < 565:
        return "auction", "集合竞价"
    if 570 <= t < 690:
        return "open", "交易中"
    if 690 <= t < 780:
        return "break", "午间休市"
    if 780 <= t <= 900:
        return "open", "交易中"
    if t < 570:
        return "closed", "开盘前"
    return "closed", "已收盘"


def is_trading_now(now=None):
    return market_state(now)[0] == "open"


def is_auction_now(now=None):
    return market_state(now)[0] == "auction"


def is_quoting_now(now=None):
    """该拉行情的时段：早盘集合竞价 + 连续竞价。"""
    return market_state(now)[0] in ("auction", "open")


def quote_interval(now=None):
    """当前时段该用的采集间隔。集合竞价 10 秒，连续竞价用默认 2 秒。"""
    if is_auction_now(now):
        return AUCTION_INTERVAL
    return DEFAULT_INTERVAL


# --------------------------------------------------------------------------
# 代码解析
# --------------------------------------------------------------------------

def code_to_symbol(code):
    """6 位代码 -> 腾讯行情 symbol"""
    if not re.fullmatch(r"\d{6}", code or ""):
        return None
    if code[0] == "6":
        return "sh" + code
    if code[0] in ("0", "3"):
        return "sz" + code
    if code[0] in ("4", "8", "9"):
        return "bj" + code
    return None


def normalize(text):
    """把 '600403' / 'SH600403' / 'sh 600403' 归一为 symbol，识别不了返回 None"""
    q = (text or "").strip()
    if not q:
        return None
    m = re.fullmatch(r"(sh|sz|bj)\s*(\d{6})", q, re.I)
    if m:
        return m.group(1).lower() + m.group(2)
    if re.fullmatch(r"\d{6}", q):
        return code_to_symbol(q)
    return None


def _unescape(s):
    try:
        return json.loads('"' + s.replace('"', '\\"') + '"')
    except Exception:
        return s


def search_symbols(keyword):
    """按名称/拼音/代码搜索 A 股，返回候选列表"""
    url = "https://smartbox.gtimg.cn/s3/?v=2&q=%s&t=all" % urllib.parse.quote(keyword)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
    m = re.search(r'"(.*)"', raw, re.S)
    if not m or not m.group(1).strip():
        return []
    out = []
    for item in m.group(1).split("^"):
        parts = item.split("~")
        if len(parts) < 3:
            continue
        market, code, name = parts[0].lower(), parts[1], parts[2]
        if market not in MARKET_NAMES or not re.fullmatch(r"\d{6}", code):
            continue
        if len(parts) >= 5 and parts[4] and not parts[4].startswith("GP"):
            continue  # 只要股票，过滤基金/债券等
        out.append({
            "symbol": market + code,
            "code": code,
            "name": _unescape(name),
            "market": MARKET_NAMES[market],
        })
    return out


# --------------------------------------------------------------------------
# 数据源
# --------------------------------------------------------------------------

def parse_tencent(line):
    m = re.match(r'v_([a-z]{2}\d{6})="(.*)"\s*$', line.strip())
    if not m:
        return None
    symbol, payload = m.group(1), m.group(2)
    p = payload.split("~")
    if len(p) < 50:
        return None

    def num(i):
        try:
            return float(p[i])
        except (ValueError, IndexError):
            return 0.0

    price = num(3)
    if price <= 0:
        return None
    ts = p[30]
    tstr = "%s:%s" % (ts[8:10], ts[10:12]) if len(ts) == 14 else ""
    return {
        "symbol": symbol,
        "code": p[2],
        "name": p[1],
        "price": price,
        "preclose": num(4),
        "open": num(5),
        "high": num(33),
        "low": num(34),
        "change": num(31),
        "change_pct": num(32),
        "volume": num(36),        # 手
        "amount": num(37),        # 万元
        "turnover": num(38),      # 换手率 %
        "vol_ratio": num(49),     # 量比
        "amplitude": num(43),     # 振幅 %
        "limit_up": num(47),
        "limit_down": num(48),
        "mkt_cap": num(45),       # 总市值（亿）
        "time": tstr,
    }


def fetch_tencent(symbols):
    url = "https://qt.gtimg.cn/q=" + ",".join(symbols)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://gu.qq.com/",
    })
    raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
    out = {}
    for line in raw.split(";"):
        q = parse_tencent(line)
        if q:
            out[q["symbol"]] = q
    # 集合竞价刚开始时，接口会回空价；这不是故障，留给上层接着拉
    if not out and not raw.strip():
        raise RuntimeError("腾讯源未返回有效数据")
    return out


def fetch_sina(symbols):
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://finance.sina.com.cn/",
    })
    raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
    out = {}
    for line in raw.split(";"):
        m = re.match(r'var hq_str_([a-z]{2}\d{6})="(.*)"', line.strip())
        if not m:
            continue
        symbol, p = m.group(1), m.group(2).split(",")
        if len(p) < 32:
            continue
        try:
            price, preclose = float(p[3]), float(p[2])
            open_, high, low = float(p[1]), float(p[4]), float(p[5])
        except ValueError:
            continue
        if price <= 0:
            continue
        chg = price - preclose
        out[symbol] = {
            "symbol": symbol,
            "code": symbol[2:],
            "name": p[0],
            "price": price,
            "preclose": preclose,
            "open": open_,
            "high": high,
            "low": low,
            "change": round(chg, 3),
            "change_pct": round(chg / preclose * 100, 2) if preclose else 0.0,
            "volume": float(p[8]) / 100 if p[8] else 0.0,
            "amount": float(p[9]) / 10000 if p[9] else 0.0,
            "turnover": 0.0,
            "vol_ratio": 0.0,
            "amplitude": round((high - low) / preclose * 100, 2) if preclose else 0.0,
            "limit_up": 0.0,
            "limit_down": 0.0,
            "mkt_cap": 0.0,
            "time": p[31][:5] if len(p) > 31 else "",
        }
    if not out and not raw.strip():
        raise RuntimeError("新浪源未返回有效数据")
    return out


def fetch_quotes(symbols):
    """统一采集入口：腾讯为主、新浪兜底。

    返回 (quotes_dict, source, error)
    """
    if not symbols:
        return {}, None, None
    try:
        return fetch_tencent(symbols), "tencent", None
    except Exception as e:
        first = "腾讯源失败(%s)" % e
    try:
        return fetch_sina(symbols), "sina", None
    except Exception as e2:
        return {}, None, "%s；新浪源失败(%s)" % (first, e2)


# --------------------------------------------------------------------------
# 自选列表持久化
# --------------------------------------------------------------------------

def load_watchlist(path=None):
    path = path or WATCHLIST_FILE
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                cleaned = []
                for item in data:
                    sym = normalize(item.get("symbol") or item.get("code") or "")
                    if not sym:
                        continue
                    cleaned.append({
                        "symbol": sym,
                        "code": sym[2:],
                        "name": item.get("name") or "",
                    })
                # 文件存在就尊重其内容（含空列表）：
                # 用户主动删空自选后，重启不应把默认标的又塞回来
                return cleaned
        except Exception as e:
            log("读取自选失败，改用默认列表: %s" % e)
    return [{"symbol": s, "code": s[2:], "name": ""} for s in DEFAULT_WATCHLIST]


def save_watchlist(items, path=None):
    path = path or WATCHLIST_FILE
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([dict(w) for w in items], f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        log("保存自选失败: %s" % e)
        return False
