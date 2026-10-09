# 行情浮窗

轻量级 A 股桌面行情浮窗。无边框、半透明、可置顶，用来盯自选股票的现价和涨跌。

A lightweight Windows desktop widget for China A-share real-time quotes.

## 功能

- 桌面浮窗：拖动摆放，位置、透明度、主题会记住
- 右键管理自选：按代码或名称添加、删除
- 红涨绿跌，价格变动时闪一下
- 涨跌异动气泡提醒，可调阈值
- 早盘集合竞价（9:15–9:25）每 10 秒刷新，连续竞价每 2 秒刷新
- `Alt+V` 显示或隐藏浮窗
- 交易日 15:00:05 可自动退出（周末和节假日不退）

行情优先走腾讯公开接口，失败时改用新浪。不需要登录或密钥。

## 运行

需要 Python 3.8+，并带上自带的 Tcl/Tk。

```bat
python widget.py
```

不想看到黑色控制台窗口：

```bat
pythonw widget.py
```

也可以双击 `start-widget.bat`。

网页看板是另一个程序：

```bat
python monitor.py
```

浏览器会打开 `http://127.0.0.1:8899/`。

## 数据放在哪

直接用源码运行时，自选和窗口设置写在项目目录：

- `watchlist.json`
- `widget.json`

安装版写在用户目录，覆盖安装不会清掉自选：

`%APPDATA%\行情浮窗`

## 打包安装包

```bat
python -m PyInstaller --noconfirm --clean StockWidget.spec
```

再用 Inno Setup 编译 `installer.iss`，得到 `installer\行情浮窗安装包.exe`。

## 许可

[MIT](LICENSE)
