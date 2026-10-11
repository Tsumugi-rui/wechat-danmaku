# -*- coding: utf-8 -*-
"""watch_windows.py —— 排查「某个窗口一直强制显示在最前面」

为什么需要它：
    光看 GetWindowText 的有标题窗口会漏掉微信/输入法等的无标题置顶小窗。
    本脚本枚举【全部】可见顶层窗口，标出置顶(TOPMOST)的，并附上所属进程名，
    这样能一眼看出到底是谁在抢最前面。

用法：
    python tools/watch_windows.py --snapshot        # 打一次快照
    python tools/watch_windows.py --watch 25        # 连续采样 25 秒，只打印变化
"""

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

u = ctypes.windll.user32
k = ctypes.windll.kernel32

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

EnumWindows = u.EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def window_text(hwnd):
    n = u.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def process_name(pid):
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"pid:{pid}"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        ok = k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value.split("\\")[-1] if ok else f"pid:{pid}"
    finally:
        k.CloseHandle(handle)


def list_windows():
    """返回全部【可见】顶层窗口：[(hwnd, title, proc, exstyle, rect)]"""
    out = []

    def _cb(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append((
            int(hwnd), window_text(hwnd), process_name(pid.value), ex,
            (rect.left, rect.top, rect.right, rect.bottom),
        ))
        return True

    EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def foreground():
    hwnd = u.GetForegroundWindow()
    return int(hwnd), window_text(hwnd)


def style_flags(ex):
    names = []
    for bit, name in ((WS_EX_TOPMOST, "置顶"),
                      (WS_EX_TRANSPARENT, "鼠标穿透"),
                      (WS_EX_LAYERED, "分层"),
                      (WS_EX_NOACTIVATE, "不激活"),
                      (WS_EX_TOOLWINDOW, "工具窗")):
        if ex & bit:
            names.append(name)
    return "·".join(names) or "普通"


def snapshot(show_all=False):
    rows = list_windows()
    tops = [r for r in rows if r[3] & WS_EX_TOPMOST]
    fh, ft = foreground()
    print(f"[快照] 可见顶层窗口 {len(rows)} 个，其中置顶 {len(tops)} 个")
    print(f"[快照] 当前前台窗口：0x{fh:x} {ft!r}")
    print("-" * 72)
    print(f"{'句柄':<10} {'进程':<22} {'样式':<22} 标题")
    print("-" * 72)
    for hwnd, title, proc, ex, rect in (rows if show_all else tops):
        mark = "★" if (ex & WS_EX_TOPMOST) else " "
        print(f"{mark}0x{hwnd:<8x} {proc[:21]:<22} {style_flags(ex):<22} {title[:34]!r}")
    print("-" * 72)


def watch(seconds):
    print(f"[监测] 采样 {seconds:.0f} 秒，只在状态发生变化时打印 …")
    t0 = time.time()
    last_key = None
    while time.time() - t0 < seconds:
        rows = list_windows()
        tops = sorted((h, proc, title) for h, title, proc, ex, _ in rows
                      if ex & WS_EX_TOPMOST)
        wx = sorted((h, title, style_flags(ex))
                    for h, title, proc, ex, _ in rows
                    if "微信" in title or "WeChat" in title or proc.lower() == "weixin.exe")
        fh, ft = foreground()
        key = (tuple(tops), fh, ft, tuple(wx))
        if key != last_key:
            el = time.time() - t0
            print(f"[{el:6.2f}s] 置顶={[(hex(h), p, t[:12]) for h, p, t in tops]}", flush=True)
            print(f"          微信窗={[(hex(h), s, t[:16]) for h, t, s in wx]} "
                  f"| 前台={ft[:18]!r}", flush=True)
            last_key = key
        time.sleep(0.15)
    print("[监测] 结束")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="打印一次快照")
    ap.add_argument("--all", action="store_true", help="快照时列出全部窗口（不只置顶的）")
    ap.add_argument("--watch", type=float, default=0, metavar="秒", help="连续采样秒数")
    args = ap.parse_args()

    if args.watch:
        watch(args.watch)
    else:
        snapshot(show_all=args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
