# -*- coding: utf-8 -*-
"""
A 股实时行情监测看板（网页版）

- 每 60 秒采集一次最新价格与涨跌幅
- 仅 A 股交易时段自动采集（9:30-11:30 / 13:00-15:00），其余时段休眠
- 提供本地网页看板，红涨绿跌，自动刷新

数据源与代码解析见同目录 quotes.py。

用法：
    python monitor.py                  # 启动看板并自动打开浏览器
    python monitor.py --port 9000      # 指定端口
    python monitor.py --interval 30    # 指定采集间隔
    python monitor.py --no-browser     # 不自动打开浏览器
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quotes import (
    BASE_DIR,
    AUCTION_INTERVAL,
    DEFAULT_INTERVAL,
    MIN_INTERVAL,
    fetch_quotes,
    is_quoting_now,
    quote_interval,
    load_watchlist,
    log,
    market_state,
    normalize,
    save_watchlist,
    search_symbols,
)

DASHBOARD_FILE = os.path.join(BASE_DIR, "dashboard.html")
DEFAULT_PORT = 8899
MAX_HISTORY = 900         # 每只股票在内存中保留的历史点数（2s 一采 ≈ 30 分钟走势）

LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()   # 手动刷新和采集线程不要同时写行情
COLLECTOR = None

STATE = {
    "watchlist": [],       # [{"symbol": "sh600403", "code": "600403", "name": "大有能源"}]
    "quotes": {},          # symbol -> quote dict
    "history": {},         # symbol -> [{"t": "13:43", "p": 7.25}]
    "last_update": None,
    "last_error": None,
    "source": None,
}


# --------------------------------------------------------------------------
# 采集
# --------------------------------------------------------------------------

def refresh():
    """采集一次并写入 STATE。同一时间只允许一次，避免两路刷新互相覆盖。"""
    with REFRESH_LOCK:
        with LOCK:
            wl = list(STATE["watchlist"])
            symbols = [w["symbol"] for w in wl]
        if not symbols:
            with LOCK:
                STATE["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                STATE["last_error"] = None
            return

        quotes, source, err = fetch_quotes(symbols)
        now = datetime.now()
        learned = False

        with LOCK:
            for w in wl:
                q = quotes.get(w["symbol"])
                if not q:
                    continue
                if not w.get("name") and q.get("name"):
                    w["name"] = q["name"]
                    learned = True
                q["name"] = q.get("name") or w.get("name") or ""
                STATE["quotes"][w["symbol"]] = q
                hist = STATE["history"].setdefault(w["symbol"], [])
                hist.append({"t": q.get("time") or now.strftime("%H:%M"), "p": q["price"]})
                if len(hist) > MAX_HISTORY:
                    del hist[:-MAX_HISTORY]
            STATE["last_update"] = now.strftime("%Y-%m-%d %H:%M:%S")
            STATE["last_error"] = err
            if source:
                STATE["source"] = source
            snapshot = [dict(item) for item in STATE["watchlist"]] if learned else None

        if snapshot:
            save_watchlist(snapshot)

        if quotes:
            log("已更新 %d 只 | %s" % (len(quotes), STATE["last_update"]))
        if err:
            log("采集警告: %s" % err)


class Collector(threading.Thread):
    """采集线程：交易时段内每 REFRESH_SECONDS 秒拉一次"""

    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._force = threading.Event()
        self.next_at = time.time()

    def trigger(self):
        self._force.set()

    def next_in(self):
        if not is_quoting_now():
            return -1
        return max(0, int(round(self.next_at - time.time())))

    def run(self):
        while not self._stop.is_set():
            try:
                if self._force.is_set() or (is_quoting_now() and time.time() >= self.next_at):
                    self._force.clear()
                    t0 = time.time()
                    refresh()
                    # 以"开始采集"的时刻为基准排下一次，否则网络耗时会被累加进周期
                    self.next_at = t0 + quote_interval()
            except Exception as e:
                log("采集异常: %s" % e)
                self.next_at = time.time() + REFRESH_SECONDS
            # 细粒度等待，避免 1 秒轮询把间隔拖成 2~3 秒
            self._stop.wait(0.2)


REFRESH_SECONDS = DEFAULT_INTERVAL


# --------------------------------------------------------------------------
# 状态组装
# --------------------------------------------------------------------------

def build_state():
    with LOCK:
        rows = []
        for w in STATE["watchlist"]:
            q = STATE["quotes"].get(w["symbol"], {})
            rows.append({
                "symbol": w["symbol"],
                "code": w.get("code") or q.get("code", ""),
                "name": w.get("name") or q.get("name", "") or w["symbol"],
                "quote": q,
                "history": list(STATE["history"].get(w["symbol"], [])),
            })
        last_update = STATE["last_update"]
        error = STATE["last_error"]
        source = STATE["source"]
    state, desc = market_state()
    return {
        "rows": rows,
        "market": {"state": state, "desc": desc},
        "last_update": last_update,
        "error": error,
        "source": source,
        "interval": REFRESH_SECONDS,
        "next_in": COLLECTOR.next_in() if COLLECTOR else -1,
        "server_ts": time.time(),
    }


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "StockBoard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            try:
                with open(DASHBOARD_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, "dashboard.html 缺失，请确认与 monitor.py 在同一目录",
                           "text/plain; charset=utf-8")
        elif path == "/api/state":
            self._json(build_state())
        elif path == "/api/search":
            kw = (qs.get("q") or [""])[0].strip()
            if not kw:
                self._json({"ok": True, "candidates": []})
                return
            try:
                self._json({"ok": True, "candidates": search_symbols(kw)})
            except Exception as e:
                self._json({"ok": False, "message": "搜索失败：%s" % e})
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/add":
            self._handle_add(self._read_json())
        elif path == "/api/remove":
            self._handle_remove(self._read_json())
        elif path == "/api/refresh":
            if COLLECTOR:
                COLLECTOR.trigger()
            self._json({"ok": True})
        else:
            self._send(404, "not found", "text/plain; charset=utf-8")

    def _handle_add(self, payload):
        query = (payload.get("query") or "").strip()
        symbol = (payload.get("symbol") or "").strip()
        name = (payload.get("name") or "").strip()
        if not query and not symbol:
            self._json({"ok": False, "message": "请输入股票代码或名称"})
            return

        target = normalize(symbol or query)
        if not target:
            try:
                cands = search_symbols(query or symbol)
            except Exception as e:
                self._json({"ok": False, "message": "搜索失败：%s" % e})
                return
            if not cands:
                self._json({"ok": False, "message": "没找到「%s」，换个代码或名称再试" % (query or symbol)})
                return
            if len(cands) > 1:
                self._json({"ok": False, "need_selection": True, "candidates": cands})
                return
            target, name = cands[0]["symbol"], cands[0]["name"]

        with LOCK:
            if any(w["symbol"] == target for w in STATE["watchlist"]):
                self._json({"ok": False, "message": "%s 已在监测列表中" % target})
                return
            STATE["watchlist"].append({"symbol": target, "code": target[2:], "name": name})

        refresh()
        save_watchlist(STATE["watchlist"])
        with LOCK:
            added = next((dict(w) for w in STATE["watchlist"] if w["symbol"] == target), {})
        self._json({"ok": True, "message": "已添加 %s" % (added.get("name") or target), "row": added})

    def _handle_remove(self, payload):
        symbol = normalize(payload.get("symbol") or "")
        if not symbol:
            self._json({"ok": False, "message": "参数有误"})
            return
        with LOCK:
            before = len(STATE["watchlist"])
            STATE["watchlist"] = [w for w in STATE["watchlist"] if w["symbol"] != symbol]
            STATE["quotes"].pop(symbol, None)
            STATE["history"].pop(symbol, None)
            removed = before != len(STATE["watchlist"])
        save_watchlist(STATE["watchlist"])
        self._json({"ok": removed, "message": "已移除" if removed else "未找到该股票"})


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def main():
    global COLLECTOR, REFRESH_SECONDS
    ap = argparse.ArgumentParser(description="A 股实时行情监测看板（网页版）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 %d" % DEFAULT_PORT)
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help="采集间隔秒数，默认 %d" % DEFAULT_INTERVAL)
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    REFRESH_SECONDS = max(MIN_INTERVAL, args.interval)
    STATE["watchlist"] = load_watchlist()

    COLLECTOR = Collector()
    COLLECTOR.start()
    COLLECTOR.trigger()   # 启动先拉一次，页面打开即有数据

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        log("端口 %d 启动失败：%s" % (args.port, e))
        log("换个端口重试：python monitor.py --port %d" % (args.port + 1))
        sys.exit(1)

    url = "http://127.0.0.1:%d/" % args.port
    log("网页看板已启动：%s" % url)
    log("采集间隔：连续竞价 %d 秒，集合竞价 %d 秒" % (REFRESH_SECONDS, AUCTION_INTERVAL))
    log("按 Ctrl+C 停止")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
