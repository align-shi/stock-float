"""退出路径回归测试。

起因：「点退出没反应」——收尾阶段某一步抛异常后 _closing 卡在 True，
后续每次点退出都被守卫挡掉，而窗口不销毁也不隐身，进程还好好的。

核心断言：**收尾阶段任何一步抛异常，窗口也必须销毁**。
之前的写法把异常放出去，_closing 卡在 True，之后每次点退出都被守卫挡掉。
"""
import io
import os
import sys
import tempfile
import time

BASE = r'C:\Users\admin\WorkBuddy\2026-09-23-13-42-45\stock-monitor'
sys.path.insert(0, BASE)

import widget as W                      # noqa: E402
import quotes                           # noqa: E402

TMP = tempfile.mkdtemp()
W.CONF_FILE = os.path.join(TMP, 'widget.json')
quotes.WATCHLIST_FILE = os.path.join(TMP, 'watchlist.json')

LOGGED = []
W.log_error = lambda msg, exc=None: LOGGED.append(
    (msg, '%s: %s' % (type(exc).__name__, exc) if exc else ''))

buf = io.StringIO()
fails = []


def say(s):
    buf.write(s + '\n')


def pump(app, ms=120):
    """把 Tk 事件泵几下，让 after / 销毁真正发生"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            if not app.root.winfo_exists():
                break
            app.root.update()
        except Exception:
            break
        time.sleep(0.01)


def make():
    app = W.StockWidget()
    W.StockWidget.start = lambda self: None
    app.conf['x'], app.conf['y'] = 300, 200
    app.show_widget()
    app.root.update()
    return app


def gone(app):
    """窗口是否真的没了（销毁后 winfo_exists 本身会抛 TclError）"""
    try:
        return not app.root.winfo_exists()
    except Exception:
        return True


def check(name, cond, extra=''):
    say('%-46s %s %s' % (name, 'OK ' if cond else '!!FAIL', extra))
    if not cond:
        fails.append(name)


# ---- 1) 正常退出（没有菜单） -------------------------------------------
app = make()
t0 = time.time()
app.quit()
pump(app, 200)
check('1 正常退出：窗口已销毁', app._destroyed and gone(app),
      '%.0fms' % ((time.time() - t0) * 1000))

# ---- 2) 从菜单回调里退出 -----------------------------------------------
app = make()
m = W.tk.Menu(app.root, tearoff=0)
m.add_command(label='退出', command=app.quit)
app._menu = m
app._menu_loop = True
app.quit()
check('2a 菜单里退出：不当场销毁（交给 popup 收尾）',
      (not app._destroyed) and app._quit_pending)
# 模拟 popup() 的 finally
app._menu_loop = False
app.destroy_menu(m)
if app._quit_pending:
    app._quit_pending = False
    app.destroy_root()
pump(app, 100)
check('2b 菜单循环退出后：窗口已销毁',
      app._destroyed and gone(app))

# ---- 3) save_conf 抛异常 —— 仍必须销毁 ---------------------------------
app = make()
app.save_conf = lambda: (_ for _ in ()).throw(RuntimeError('磁盘写不了'))
try:
    app.quit()
    escaped = False
except Exception as e:
    escaped = True
    say('   异常逃逸: %r' % (e,))
pump(app, 100)
check('3 save_conf 抛异常：仍销毁 且 异常不外泄',
      (not escaped) and app._destroyed and gone(app))
check('3b 且异常被记进日志', any('退出收尾' in a for a, _ in LOGGED),
      str([a for a, _ in LOGGED][-1:]))

# ---- 4) close_menu 抛异常 —— 仍必须销毁 --------------------------------
LOGGED.clear()
app = make()
app.close_menu = lambda: (_ for _ in ()).throw(RuntimeError('EndMenu 炸了'))
try:
    app.quit()
    escaped = False
except Exception as e:
    escaped = True
pump(app, 100)
check('4 close_menu 抛异常：仍销毁 且 异常不外泄',
      (not escaped) and app._destroyed and gone(app))

# ---- 5) dismiss_bubbles 抛异常 —— 仍必须销毁 ---------------------------
app = make()
app.dismiss_bubbles = lambda: (_ for _ in ()).throw(RuntimeError('气泡炸了'))
try:
    app.quit()
    escaped = False
except Exception as e:
    escaped = True
pump(app, 100)
check('5 dismiss_bubbles 抛异常：仍销毁',
      (not escaped) and app._destroyed and gone(app))

# ---- 6) 菜单里退出 + save_conf 抛异常（最贴近真实故障的组合） ----------
app = make()
app.save_conf = lambda: (_ for _ in ()).throw(RuntimeError('写盘失败'))
m = W.tk.Menu(app.root, tearoff=0)
app._menu = m
app._menu_loop = True
app.quit()
app._menu_loop = False
app.destroy_menu(m)
if app._quit_pending:
    app._quit_pending = False
    app.destroy_root()
pump(app, 100)
check('6 菜单退出 + 写盘失败：仍销毁',
      app._destroyed and gone(app))

# ---- 7) 重复点退出：不抛异常，也不重复销毁 ----------------------------
app = make()
app.quit()
try:
    app.quit()
    app.quit()
    escaped = False
except Exception as e:
    escaped = True
    say('   异常逃逸: %r' % (e,))
pump(app, 100)
check('7 重复退出：静默忽略，不抛异常',
      (not escaped) and app._destroyed)

# ---- 8) 菜单里点了退出，但 popup 的 finally 永远不来 —— 兜底生效 -------
app = make()
m = W.tk.Menu(app.root, tearoff=0)
app._menu = m
app._menu_loop = True
app.quit()
check('8a 兜底计时器已挂上：尚未销毁', (not app._destroyed) and app._quit_pending)
pump(app, 1800)          # 等 1.5 秒的兜底定时器
check('8b 1.5s 后兜底销毁生效',
      app._destroyed and gone(app))

say('')
say('RESULT: %s' % ('ALL PASS' if not fails else 'FAILED -> ' + ', '.join(fails)))
print(buf.getvalue())
