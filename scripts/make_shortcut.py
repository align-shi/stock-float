# -*- coding: utf-8 -*-
"""在桌面创建「开源节流」快捷方式，指向 start-widget.bat。

中文文件名 + 中文描述走 PowerShell -EncodedCommand（Base64/UTF-16LE），
彻底绕开命令行与 -Command 的代码页转换问题。
"""
import base64
import io
import os
import subprocess
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
BASE = SCRIPTS
DESKTOP = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
TARGET = os.path.join(SCRIPTS, "start-widget.bat")
ICON = os.path.join(ROOT, "assets", "stock-widget.ico")
LNK = os.path.join(DESKTOP, "开源节流.lnk")
LOG = os.path.join(BASE, "shortcut.log")

PS = r'''
$ErrorActionPreference = 'Stop'
$log = "{log}"
$lines = New-Object System.Collections.ArrayList

function Say($t) { [void]$lines.Add($t) }

try {
    $ws = New-Object -ComObject WScript.Shell

    # 目标脚本必须存在
    if (-not (Test-Path "{target}")) { throw "目标脚本不存在: {target}" }
    if (-not (Test-Path "{icon}"))   { throw "图标文件不存在: {icon}" }

    $sc = $ws.CreateShortcut("{lnk}")
    $sc.TargetPath       = "{target}"
    $sc.WorkingDirectory = "{workdir}"
    $sc.IconLocation     = "{icon},0"
    $sc.Description      = "{desc}"
    $sc.WindowStyle      = 1
    $sc.Save()

    # 读回校验
    $v = $ws.CreateShortcut("{lnk}")
    Say ("存在      : " + (Test-Path "{lnk}"))
    Say ("文件      : {lnk}")
    Say ("大小      : " + (Get-Item "{lnk}").Length + " B")
    Say ("目标      : " + $v.TargetPath)
    Say ("起始位置  : " + $v.WorkingDirectory)
    Say ("图标      : " + $v.IconLocation)
    Say ("描述      : " + $v.Description)
    Say ("窗口样式  : " + $v.WindowStyle)

    # 目标脚本是否可执行
    Say ("目标可执行: " + (Test-Path $v.TargetPath))
} catch {
    Say ("错误      : " + $_.Exception.Message)
}

$lines | Out-File -FilePath $log -Encoding UTF8
'''

ps = PS
# 注意：不能用 .format()——PowerShell 里有大量花括号会被当成占位符
for key, val in (("{log}", LOG), ("{lnk}", LNK), ("{icon}", ICON),
                 ("{target}", TARGET), ("{workdir}", BASE),
                 ("{desc}", "A股行情浮窗 · 大有能源 / 塞力医疗")):
    ps = ps.replace(key, val)

enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
r = subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
     "-EncodedCommand", enc],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=90)

time.sleep(0.3)
body = open(LOG, encoding="utf-8-sig").read() if os.path.exists(LOG) else ""

out = io.StringIO()
out.write("=" * 62 + "\n")
out.write("桌面快捷方式创建结果\n")
out.write("=" * 62 + "\n")
out.write(body)
if r.returncode != 0:
    out.write("\npowershell exit=%d\n" % r.returncode)
    out.write((r.stdout or "") + (r.stderr or ""))
out.write("\n桌面上的 lnk 文件:\n")
for f in sorted(os.listdir(DESKTOP)):
    if f.lower().endswith(".lnk"):
        out.write("  %s   %d B\n" % (f, os.path.getsize(os.path.join(DESKTOP, f))))

open(LOG, "w", encoding="utf-8").write(out.getvalue())
