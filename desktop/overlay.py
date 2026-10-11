# -*- coding: utf-8 -*-
"""
desktop/overlay.py —— 全屏透明桌面弹幕层（带启动自检）

把消息以弹幕的形式铺满整个屏幕，同时做到「完全不影响正常用电脑」：

  · 背景彻底透明 —— 只有文字浮在桌面上，壁纸、图标原样可见
  · 鼠标完全穿透 —— 点击、拖拽、框选、双击图标都直接落到桌面上
  · 不抢键盘焦点 —— 打字、快捷键、游戏操作一概不受影响
  · 不出现在任务栏和 Alt+Tab 列表里
  · 永远保持在最顶层

实现原理（全部使用 Windows 原生能力，只依赖 Python 标准库）：
  WS_EX_TRANSPARENT  鼠标事件直接穿透到下层窗口
  WS_EX_LAYERED      分层窗口，配合颜色键实现逐像素透明
  WS_EX_NOACTIVATE   点击不会激活本窗口，不抢焦点
  WS_EX_TOOLWINDOW   从 Alt+Tab / 任务栏中隐藏
  SetLayeredWindowAttributes 把背景色设为透明键

--------------------------------------------------------------------------
★ 启动自检（重要）
--------------------------------------------------------------------------
全屏 + 置顶 + 无边框 + 鼠标穿透，这几种属性叠在一起，一旦某一步没生效，
用户就可能被一个全黑、又关不掉的窗口困住，只能重启电脑。

因此本程序在正式运行前，会向**操作系统**求证三件事，而不是听自己怎么说：

  1. 颜色键是否真的被系统接受   → GetLayeredWindowAttributes 读回来核对
  2. 鼠标是否真的能穿透         → WindowFromPoint 看落点是不是本窗口
  3. 屏幕是否真的没被盖住       → 对比启动前后的屏幕像素

三项全部通过才进入正式运行；**任何一项不通过，会立刻收回窗口并退出**，
屏幕上不会留下任何东西。加 --skip-check 可跳过自检（不建议）。
"""

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from http.client import HTTPConnection

# 让 desktop/overlay.py 被单独执行时也能导入项目根目录的 settings
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)
try:
    from settings import load_settings, settings_mtime
except Exception:                       # 单独把这个文件拷走用也不会因此崩掉
    load_settings = None
    settings_mtime = None

# 被当作「透明键」的颜色。选一个正文里几乎不可能出现的值，
# 凡是渲染成这个颜色的像素都会被挖空。
TRANSPARENT_KEY = "#010203"
BAND_COLOR = "#0b0f17"

IS_WINDOWS = sys.platform == "win32"

# 显示区域的中文名，用于日志与面板回显
REGION_TEXT = {
    "full": "全屏铺满",
    "top": "上半屏",
    "bottom": "下半屏",
    "bar": "顶部条带",
}

# --------------------------------------------------------------------------- #
# 气泡通知外观
# --------------------------------------------------------------------------- #
#
# 参考手机上的通知卡片：左侧一个圆角应用图标，右边两行文字（标题 + 正文），
# 右上角标注来源，整张卡片从屏幕边缘滑入、停留几秒后滑走。
# 卡片是画在同一个透明弹幕层里的（颜色键只挖掉背景色，卡片不会被挖掉）。

NOTIFY_POS_TEXT = {
    "br": "右下角",
    "tr": "右上角",
    "bl": "左下角",
    "tl": "左上角",
}

NOTIFY_BG = "#ffffff"          # 卡片底色
NOTIFY_EDGE = "#e3e7ef"        # 卡片描边
NOTIFY_SHADOW = "#c7cedb"      # 卡片阴影
NOTIFY_TITLE = "#1b2433"       # 标题（发送者）
NOTIFY_BODY = "#3d4759"        # 正文（消息内容）
NOTIFY_META = "#98a2b3"        # 右上角来源（群名 / 时间）
NOTIFY_ICON_TEXT = "#ffffff"   # 图标里的首字
NOTIFY_RADIUS = 16             # 卡片圆角半径
NOTIFY_ICON = 26               # 图标边长

# --------------------------------------------------------------------------- #
# Win32 常量与函数绑定
# --------------------------------------------------------------------------- #

GWL_EXSTYLE = -20

WS_EX_TRANSPARENT = 0x00000020   # 鼠标穿透
WS_EX_TOOLWINDOW = 0x00000080    # 不出现在 Alt+Tab
WS_EX_LAYERED = 0x00080000       # 分层窗口（颜色键透明的前提）
WS_EX_NOACTIVATE = 0x08000000    # 点击不激活、不抢焦点

LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

GA_ROOT = 2

SM_CXSCREEN = 0
SM_CYSCREEN = 1

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

# 每屏采样点数（列 x 行），自检和基准取样共用
CHECK_COLS = 8
CHECK_ROWS = 6

# 每个动作准备几个候选热键，前一个被别的软件占用就自动往后试
HOTKEY_CANDIDATES = {
    "toggle": [
        ("Ctrl+Alt+D", MOD_CONTROL | MOD_ALT, 0x44),      # D
        ("Ctrl+Alt+F9", MOD_CONTROL | MOD_ALT, 0x78),     # F9
        ("Ctrl+Shift+F9", MOD_CONTROL | MOD_SHIFT, 0x78),
    ],
    "quit": [
        ("Ctrl+Alt+Q", MOD_CONTROL | MOD_ALT, 0x51),      # Q
        ("Ctrl+Alt+F10", MOD_CONTROL | MOD_ALT, 0x79),    # F10
        ("Ctrl+Shift+F10", MOD_CONTROL | MOD_SHIFT, 0x79),
    ],
}

_user32 = ctypes.WinDLL("user32", use_last_error=True) if IS_WINDOWS else None
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True) if IS_WINDOWS else None
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None

if IS_WINDOWS:
    # 64 位用 Ptr 版本，32 位用普通版本；签名不设对会在 64 位下截断句柄
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _get_style = _user32.GetWindowLongPtrW
        _set_style = _user32.SetWindowLongPtrW
        _get_style.restype = ctypes.c_ssize_t
        _get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        _set_style.restype = ctypes.c_ssize_t
        _set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    else:
        _get_style = _user32.GetWindowLongW
        _set_style = _user32.SetWindowLongW
        _get_style.restype = ctypes.c_long
        _get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        _set_style.restype = ctypes.c_long
        _set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    _user32.GetParent.restype = wintypes.HWND
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]

    _user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    _user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    _user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
    _user32.GetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]

    _user32.WindowFromPoint.restype = wintypes.HWND
    _user32.WindowFromPoint.argtypes = [wintypes.POINT]

    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    # GetPixel 由 gdi32 导出，不在 user32 里
    _gdi32.GetPixel.restype = wintypes.DWORD
    _gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]

    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]

    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                       wintypes.UINT, wintypes.UINT]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #

def enable_dpi_awareness():
    """开启 DPI 感知，避免在 125% / 150% 缩放的屏幕上窗口铺不满或发虚。

    必须在创建任何窗口之前调用，否则拿到的屏幕尺寸是缩放后的逻辑值。
    """
    if not IS_WINDOWS:
        return
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_size():
    """物理像素的屏幕尺寸（需先调用 enable_dpi_awareness）。"""
    if not IS_WINDOWS:
        return 0, 0
    return (_user32.GetSystemMetrics(SM_CXSCREEN),
            _user32.GetSystemMetrics(SM_CYSCREEN))


def hex_to_colorref(hex_color):
    """'#rrggbb' -> Windows COLORREF（0x00BBGGRR）。"""
    text = hex_color.lstrip("#")
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return (b << 16) | (g << 8) | r


def colorref_to_rgb(value):
    return (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)


def grid_points(width, height, cols=CHECK_COLS, rows=CHECK_ROWS, margin=0.05):
    """在一块矩形范围内取均匀分布的采样点（相对坐标）。"""
    points = []
    for i in range(cols):
        for j in range(rows):
            fx = margin + (1 - 2 * margin) * (i / max(1, cols - 1))
            fy = margin + (1 - 2 * margin) * (j / max(1, rows - 1))
            points.append((int(width * fx), int(height * fy)))
    return points


def sample_screen(points):
    """读取屏幕上若干点当前的颜色。返回 [(r,g,b) 或 None]。"""
    if not IS_WINDOWS:
        return []
    hdc = _user32.GetDC(None)
    if not hdc:
        return []
    try:
        out = []
        for x, y in points:
            value = _gdi32.GetPixel(hdc, int(x), int(y))
            out.append(None if value == 0xFFFFFFFF else colorref_to_rgb(value))
        return out
    finally:
        _user32.ReleaseDC(None, hdc)


def near(a, b, tol=4):
    if a is None or b is None:
        return False
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def parse_filters(raw):
    """把屏蔽词解析成列表。支持中英文逗号、换行分隔，也接受已经是列表的输入。"""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = str(raw).replace("，", ",").replace("；", ",") \
                        .replace(";", ",").replace("\n", ",").split(",")
    return [str(w).strip() for w in items if str(w).strip()]


def compute_geometry(screen_w, screen_h, args):
    """确定窗口覆盖的范围（纯计算，不依赖窗口对象）。"""
    if args.geometry:
        spec = args.geometry.replace(" ", "")
        size, _, rest = spec.partition("+")
        try:
            w, h = (int(v) for v in size.lower().split("x"))
        except ValueError:
            w, h = screen_w, screen_h
        x = y = 0
        if rest:
            parts = spec.split("+")[1:]
            if len(parts) >= 2:
                x, y = int(parts[0]), int(parts[1])
        return w, h, x, y

    if args.height:
        h = int(args.height)
    elif getattr(args, "ratio", 0):
        # 按屏幕高度比例取范围（上半屏 / 下半屏）
        h = max(80, int(screen_h * float(args.ratio)))
    else:
        h = screen_h
    h = min(h, screen_h)
    y = screen_h - h if args.anchor == "bottom" else 0
    return screen_w, h, 0, y


# --------------------------------------------------------------------------- #
# 窗口属性操作（全部向系统求证）
# --------------------------------------------------------------------------- #

def resolve_hwnd(root):
    """取真正承载这个窗口的顶层句柄。

    Tk 的 winfo_id() 给的是内层窗口，真正被显示的顶层窗口是它的祖先；
    扩展样式和颜色键都必须设在祖先上才有效。
    """
    own = wintypes.HWND(root.winfo_id())
    ancestor = _user32.GetAncestor(own, GA_ROOT)
    return int(ancestor) if ancestor else int(own)


def candidate_hwnds(root, hwnd):
    """可能承载窗口的句柄候选（顶层 → 自身 → 父）。"""
    result = []
    for value in (hwnd, int(root.winfo_id())):
        if value and value not in result:
            result.append(value)
    try:
        parent = _user32.GetParent(wintypes.HWND(hwnd))
        if parent and int(parent) not in result:
            result.append(int(parent))
    except Exception:
        pass
    return result


def add_ex_styles(hwnd, wanted):
    """把扩展样式按位或上去，返回读取回来的最新值。"""
    handle = wintypes.HWND(hwnd)
    current = _get_style(handle, GWL_EXSTYLE)
    _set_style(handle, GWL_EXSTYLE, current | wanted)
    return _get_style(handle, GWL_EXSTYLE)


def set_color_key(hwnd, key_colorref, alpha=0):
    """给分层窗口设置颜色键（可选叠加整体透明度），并**读回来核对**。

    alpha = 0 时只设颜色键：文字完全不透明，背景被挖空。
    alpha > 0 时同时启用 LWA_ALPHA：颜色键像素依旧完全挖空，
    其余像素（也就是文字）按该透明度与桌面混合 —— 这就是「弹幕不透明度」。

    这个组合行为是实测确认过的（见 README「不透明度」一节）：
    背景仍被彻底打穿，文字则真正半透明。

    返回 (是否真的生效, 说明)。Tk 的 -transparentcolor 只会默默接受设置、
    不给任何反馈；这里直接调 API 并把结果读回来验，不听自述。
    """
    handle = wintypes.HWND(hwnd)
    add_ex_styles(hwnd, WS_EX_LAYERED)          # 颜色键的前提

    alpha = max(0, min(255, int(alpha)))
    flags = LWA_COLORKEY | (LWA_ALPHA if alpha > 0 else 0)
    if not _user32.SetLayeredWindowAttributes(handle, key_colorref, alpha,
                                              flags):
        return False, "SetLayeredWindowAttributes 被系统拒绝"

    read_key = wintypes.DWORD(0)
    read_alpha = ctypes.c_ubyte(0)
    read_flags = wintypes.DWORD(0)
    if not _user32.GetLayeredWindowAttributes(
            handle, ctypes.byref(read_key), ctypes.byref(read_alpha),
            ctypes.byref(read_flags)):
        return False, "GetLayeredWindowAttributes 读不回来"

    got = read_key.value
    if not (read_flags.value & LWA_COLORKEY):
        return False, f"系统没记住颜色键（flags={read_flags.value}）"
    if got != key_colorref:
        return False, f"颜色键对不上：设 {key_colorref:#08x}，读回 {got:#08x}"
    if alpha > 0 and not (read_flags.value & LWA_ALPHA):
        return False, f"系统没记住透明度（flags={read_flags.value}）"

    note = f"颜色键已生效（{got:#08x}）"
    if alpha > 0:
        note += f"，文字透明度 {read_alpha.value}/255"
    return True, note


def click_blocking_points(hwnds, points):
    """返回被本窗口挡住的采样点数量。

    WindowFromPoint 会自动跳过带 WS_EX_TRANSPARENT 的窗口，
    所以如果返回的句柄是弹幕层自己，就说明穿透没生效。
    """
    blocked = 0
    for x, y in points:
        try:
            hit = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        except Exception:
            continue
        if hit and int(hit) in hwnds:
            blocked += 1
    return blocked


# --------------------------------------------------------------------------- #
# 颜色工具
# --------------------------------------------------------------------------- #

PALETTE = ["#7dd3fc", "#a5b4fc", "#f0abfc", "#fda4af", "#fdba74",
           "#fcd34d", "#86efac", "#67e8f9", "#c4b5fd", "#f9a8d4",
           "#5eead4", "#fca5a5"]


def color_for(name):
    h = 0
    for ch in (name or "?"):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return PALETTE[h % len(PALETTE)]


def dim_color(hex_color, factor):
    """把颜色按比例压暗，让弹幕更柔和、不刺眼。"""
    if factor >= 0.999:
        return hex_color
    text = hex_color.lstrip("#")
    try:
        r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def rounded_points(x1, y1, x2, y2, r):
    """圆角矩形的顶点序列。

    tkinter 的画布没有「圆角」这种东西，通行做法是给 create_polygon
    传一组带重复点的坐标并开 smooth=True —— 样条会把这些点收成圆角。
    """
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


# --------------------------------------------------------------------------- #
# SSE 客户端
# --------------------------------------------------------------------------- #

class SSEClient(threading.Thread):
    """连接后台服务的 /events，把消息丢进队列；断线自动重连。"""

    def __init__(self, host, port, out_queue, on_state=None):
        super().__init__(daemon=True, name="sse-client")
        self.host = host
        self.port = port
        self.out = out_queue
        self.on_state = on_state or (lambda s: None)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        fails = 0
        last_error = None
        while not self._stop.is_set():
            try:
                conn = HTTPConnection(self.host, self.port, timeout=40)
                conn.request("GET", "/events", headers={"Accept": "text/event-stream"})
                resp = conn.getresponse()
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                if fails:
                    print(f"[sse] 已重新连上 {self.host}:{self.port}", flush=True)
                fails, last_error = 0, None
                self.on_state("connected")
                while not self._stop.is_set():
                    line = resp.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", "ignore").strip()
                    if not text.startswith("data:"):
                        continue
                    try:
                        self.out.put(json.loads(text[5:].strip()))
                    except Exception:
                        continue
            except Exception as exc:
                fails += 1
                message = str(exc)
                # 断线是常态（服务没开、端口变了），只提醒第一次和变化时，
                # 否则日志会被刷屏，真正的问题反而看不见。
                if fails == 1 or message != last_error:
                    last_error = message
                    self.on_state(f"连不上 {self.host}:{self.port} —— {message}")
                    self.on_state(
                        f"提示：请确认后台服务在 {self.host}:{self.port} 上运行；"
                        f"若在设置面板里改过端口，需要重新启动一次弹幕层")
                elif fails % 20 == 0:
                    self.on_state(f"仍在重试连接（已尝试 {fails} 次）")
            self._stop.wait(3)
        self.on_state("stopped")


# --------------------------------------------------------------------------- #
# 全局热键
# --------------------------------------------------------------------------- #

class HotkeyWatcher(threading.Thread):
    """在后台线程里注册并监听系统级热键。

    RegisterHotKey 注册到「调用它的那个线程」的消息队列，所以注册和收消息
    必须在同一个线程里，不能放在 tkinter 主线程（会被 Tk 的消息循环抢走）。

    每个动作可以给多个候选组合，逐个尝试直到有一个注册成功——热键被别的
    软件占用是很常见的事，硬绑一个组合很容易翻车。
    """

    def __init__(self, candidates, on_hotkey):
        super().__init__(daemon=True, name="hotkey-watcher")
        self.candidates = candidates        # {"toggle": [(显示名, mods, vk), ...]}
        self.on_hotkey = on_hotkey
        self.ready = threading.Event()      # 注册完成信号，避免主线程读到空结果
        self.registered = {}                # 动作名 -> 实际生效的显示名
        self._ids = {}                      # 动作名 -> hotkey id
        self.thread_id = None

    def run(self):
        if not IS_WINDOWS:
            self.ready.set()
            return

        self.thread_id = _kernel32.GetCurrentThreadId()
        next_id = 1
        for action, options in self.candidates.items():
            for label, mods, vk in options:
                try:
                    if _user32.RegisterHotKey(None, next_id,
                                              mods | MOD_NOREPEAT, vk):
                        self.registered[action] = label
                        self._ids[action] = next_id
                        next_id += 1
                        break
                except Exception:
                    continue

        self.ready.set()                    # ★ 注册完毕，主线程可以放心读了

        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                pressed = int(msg.wParam)
                for action, hotkey_id in self._ids.items():
                    if hotkey_id == pressed:
                        try:
                            self.on_hotkey(action)
                        except Exception:
                            pass
                        break

        for hotkey_id in self._ids.values():
            try:
                _user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass

    def stop(self):
        if self.thread_id:
            try:
                _user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# 弹幕层
# --------------------------------------------------------------------------- #

class Overlay:
    LANE_GAP = 6
    TITLE = "WeChat Danmaku Overlay"

    def __init__(self, args, check_points=None, baseline=None):
        self.args = args
        self.check_points = check_points or []   # 绝对坐标的采样点
        self.baseline = baseline or []           # 启动前的屏幕取样
        self.queue = queue.Queue()
        self.items = []
        self.lanes = []
        self.notifications = []      # 屏幕上的气泡通知（最新在列表末尾）
        self.hint_id = None
        self.last_tick = time.time()
        self.visible = True
        self.running = True
        self.hwnd = 0
        self.click_through = False
        self.transparent_ok = False
        self.transparent_note = ""
        self.check_report = []
        self.filters = parse_filters(getattr(args, "filters", ""))
        self.settings_file = getattr(args, "settings", None)
        self.settings_mtime = 0.0

        self.root = tk.Tk()
        self.root.title(self.TITLE)
        self.root.overrideredirect(True)              # 无边框、不进任务栏
        self.root.attributes("-topmost", True)

        width, height, x, y = self.resolve_geometry()
        self.width, self.height = width, height
        self.origin = (x, y)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        # 画布底色：透明模式下就是「透明键」那个颜色，凡此颜色的像素都会被挖空
        bg = BAND_COLOR if args.band else TRANSPARENT_KEY
        self.root.configure(bg=bg)
        self.canvas = tk.Canvas(self.root, width=width, height=height,
                                bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        if args.band:
            try:
                self.root.attributes("-alpha", args.alpha)
            except Exception:
                pass

        self.lane_height = int(args.font * 1.8) + self.LANE_GAP
        self._recalc_lanes()

        self.root.update()          # 先让窗口真正映射出来，句柄才有效
        self.hwnd = resolve_hwnd(self.root) if IS_WINDOWS else 0

        # ★ 真正的透明：自己调 API 设颜色键，并读回来核对
        if not args.band:
            self.transparent_ok, self.transparent_note = self._apply_transparency()

        # ★ 鼠标穿透 + 其它扩展样式
        if not args.no_click_through:
            self.click_through = self._apply_click_through()

        # 若调用方没提供采样点（例如非全屏），按窗口实际位置补一份
        if not self.check_points:
            self._build_check_points()

        self.client = SSEClient(args.host, args.port, self.queue, self.on_state)
        self.client.start()

        self.watcher = None
        if not args.no_hotkey:
            self.watcher = HotkeyWatcher(HOTKEY_CANDIDATES, self.on_hotkey)
            self.watcher.start()
            # 等注册结果，否则主线程会读到还没填好的 registered
            self.watcher.ready.wait(timeout=3)

        # ★ 启动自检：向系统求证，不听自己的自述
        self.root.update()
        if not args.skip_check:
            problems = self._self_check()
            if problems:
                self._abort(problems)

        # 此处状态已确定，可以显示提示了
        self.show_hint()

        self.root.bind("<Escape>", lambda e: self.quit())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        # 只有自检通过了才敢定时保顶
        self.root.after(5000, self.keep_top)

        # 与设置面板联动：settings.json 一改就应用，不必重启弹幕层
        if self.settings_file and load_settings is not None:
            try:
                self.settings_mtime = settings_mtime(self.settings_file)
                self.root.after(900, self._poll_settings)
            except Exception as exc:
                print(f"[overlay] 设置联动未启用：{exc}", flush=True)

        if args.auto_exit > 0:
            print(f"[overlay] 已设定 {args.auto_exit:g} 秒后自动退出", flush=True)
            self.root.after(int(args.auto_exit * 1000), self.quit)

    # ------------------------------------------------------------ 几何

    def resolve_geometry(self):
        return compute_geometry(self.root.winfo_screenwidth(),
                                self.root.winfo_screenheight(), self.args)

    def _build_check_points(self):
        x0, y0 = self.origin
        self.check_points = [(x0 + px, y0 + py)
                             for px, py in grid_points(self.width, self.height)]

    def _recalc_lanes(self):
        top = 8
        usable = max(self.lane_height, self.height - top - self.args.font)
        count = max(1, int(usable // self.lane_height))
        self.lanes = [0.0] * count
        self.lane_count = count
        self.lane_top = top

    def _pick_lane(self, now):
        best_free, best_free_t = -1, float("inf")
        best_busy, best_busy_t = 0, float("inf")
        for i, ready in enumerate(self.lanes):
            if ready <= now:
                if ready < best_free_t:
                    best_free, best_free_t = i, ready
            elif ready < best_busy_t:
                best_busy, best_busy_t = i, ready
        if best_free >= 0:
            return best_free, 0.0
        return best_busy, min(best_busy_t - now, 1.0)

    # ------------------------------------------------------------ 透明 / 穿透

    def _alpha_byte(self):
        """把 0.35~1.0 的「不透明度」换算成 0~255 的 alpha；1.0 表示不启用。"""
        opacity = float(getattr(self.args, "opacity", 1.0) or 1.0)
        if opacity >= 0.995:
            return 0                      # 完全不透明时不必启用 alpha
        return max(1, min(255, int(round(opacity * 255))))

    def _apply_transparency(self):
        """设置颜色键（含文字透明度）并核对。失败则返回 False（调用方会收回窗口）。"""
        if not IS_WINDOWS:
            return False, "非 Windows 系统，颜色键透明不可用"

        key = hex_to_colorref(TRANSPARENT_KEY)
        alpha = self._alpha_byte()
        notes = []
        for hwnd in candidate_hwnds(self.root, self.hwnd):
            ok, note = set_color_key(hwnd, key, alpha)
            notes.append(f"{note}（句柄 {hwnd:#x}）")
            if ok:
                self.hwnd = hwnd
                return True, note

        # 退路：交给 Tk 自己设一遍，再核对
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
            self.root.update()
            for hwnd in candidate_hwnds(self.root, self.hwnd):
                ok, note = set_color_key(hwnd, key, alpha)
                if ok:
                    self.hwnd = hwnd
                    return True, note
        except Exception as exc:
            notes.append(f"Tk 退路异常：{exc}")

        return False, "；".join(notes)

    def apply_opacity(self, opacity):
        """运行中调整文字透明度（不必重启）。"""
        self.args.opacity = float(opacity)
        if self.args.band or not IS_WINDOWS:
            return
        key = hex_to_colorref(TRANSPARENT_KEY)
        ok, note = set_color_key(self.hwnd, key, self._alpha_byte())
        self.transparent_ok = ok
        print(f"[overlay] 文字透明度 → {float(opacity):.0%}  {note}", flush=True)

    def _apply_click_through(self):
        wanted = (WS_EX_TRANSPARENT | WS_EX_LAYERED
                  | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        need = WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        for hwnd in candidate_hwnds(self.root, self.hwnd):
            style = add_ex_styles(hwnd, wanted)
            if (style & need) == need:
                self.hwnd = hwnd
                return True
        return False

    def _self_check(self):
        """向系统求证三件事。返回问题列表（空表示全过）。"""
        problems = []
        points = self.check_points

        # ① 颜色键是否真的生效
        if not self.args.band:
            self.check_report.append(
                f"颜色键      {'通过' if self.transparent_ok else '失败'}"
                f"   {self.transparent_note}")
            if not self.transparent_ok:
                problems.append(f"透明色键未生效：{self.transparent_note}")

        # ② 鼠标是否真的能穿透
        if self.click_through:
            blocked = click_blocking_points(
                {self.hwnd, int(self.root.winfo_id())}, points)
            if blocked:
                self.check_report.append(
                    f"鼠标穿透    失败   {blocked}/{len(points)} 个点仍被弹幕层接收")
                problems.append(
                    f"鼠标穿透未生效：{blocked}/{len(points)} 个采样点仍被弹幕层挡住")
            else:
                self.check_report.append(
                    f"鼠标穿透    通过   {len(points)} 个采样点全部穿透")
        else:
            self.check_report.append("鼠标穿透    未开启（--no-click-through）")

        # ③ 屏幕是否真的没被盖住（对比启动前后的像素）
        #
        # 判据要经得起两种失效方式：
        #   a) 颜色键没注册上   → 屏幕被铺满 #010203（近黑）
        #   b) 画布底色 ≠ 颜色键 → 屏幕被铺成任意一块平色
        # 所以不能只找「透明键那个颜色」，而要问：
        #   启动前后有多少点变了？这些变了的点是不是变成了同一块平色？
        # 真的透明时，文字只会让极少数点改变，且颜色各不相同；
        # 被不透明窗口盖住时，成片的点会齐刷刷变成同一种颜色。
        if not self.args.band:
            if self.baseline and len(self.baseline) == len(points):
                after = sample_screen(points)
                changed = [i for i, (b, a) in enumerate(zip(self.baseline, after))
                           if not near(b, a, tol=8)]
                ratio = len(changed) / len(points)

                flat_ratio = 0.0
                if changed:
                    buckets = {}
                    for i in changed:
                        color = after[i]
                        if color is None:
                            continue
                        bucket = tuple(v // 8 for v in color)   # 量化后归类
                        buckets[bucket] = buckets.get(bucket, 0) + 1
                    if buckets:
                        flat_ratio = max(buckets.values()) / len(changed)

                covered = ratio >= 0.5 and flat_ratio >= 0.8
                detail = (f"变动 {ratio:.0%}，其中同色占 {flat_ratio:.0%}")
                if covered:
                    self.check_report.append(f"屏幕遮挡    失败   {detail}")
                    problems.append(
                        f"屏幕被挡住了：{detail} —— 成片的采样点变成了同一块平色，"
                        f"说明窗口是不透明地铺在屏幕上，而不是只留下文字")
                else:
                    self.check_report.append(f"屏幕遮挡    通过   {detail}")
            else:
                self.check_report.append("屏幕遮挡    跳过   未取到启动前基准")
        return problems

    def _abort(self, problems):
        """自检不通过：立刻收回窗口，绝不会把屏幕留在黑屏状态。"""
        self.running = False
        try:
            self.root.withdraw()
            self.root.update()
        except Exception:
            pass
        try:
            self.client.stop()
        except Exception:
            pass
        if self.watcher:
            self.watcher.stop()
        try:
            self.root.destroy()
        except Exception:
            pass

        print()
        print("!" * 58)
        print("  自检未通过 —— 已立刻收回弹幕层，屏幕没有被占用")
        print("!" * 58)
        for line in self.check_report:
            print(f"   {line}")
        print("-" * 58)
        for p in problems:
            print(f"   ✗ {p}")
        print("-" * 58)
        print("   可加 --band 改用半透明黑条模式，或加 --skip-check 强制运行。")
        print("!" * 58, flush=True)
        raise SystemExit(2)

    # ------------------------------------------------------------ 提示

    def show_hint(self):
        if self.args.quiet:
            return
        parts = []
        if self.click_through:
            parts.append("鼠标可穿透")
        modes = []
        if not getattr(self.args, "no_danmaku", False):
            modes.append("弹幕")
        if not getattr(self.args, "no_notify", False):
            modes.append("气泡通知")
        parts.append(("＋".join(modes) + "已就绪") if modes else "弹幕与通知都已关闭")
        if self.watcher is not None and self.watcher.registered:
            toggle = self.watcher.registered.get("toggle")
            quit_key = self.watcher.registered.get("quit")
            if toggle:
                parts.append(f"{toggle} 隐藏")
            if quit_key:
                parts.append(f"{quit_key} 退出")
        parts.append("等消息中…")
        self.hint_id = self.canvas.create_text(
            self.width // 2, 60, text="  ·  ".join(parts), anchor="center",
            fill="#a8b3c4", font=("Microsoft YaHei", max(12, self.args.font // 2)))

    def hide_hint(self):
        if self.hint_id is not None:
            self.canvas.delete(self.hint_id)
            self.hint_id = None

    # ------------------------------------------------------------ 状态

    def on_state(self, state):
        if state != "connected":
            print(f"[sse] {state}", flush=True)

    # ------------------------------------------------------------ 热键

    def on_hotkey(self, action):
        """热键回调跑在监听线程里，切回主线程再操作界面。"""
        if action == "toggle":
            self.root.after(0, self.toggle_visible)
        elif action == "quit":
            self.root.after(0, self.quit)

    def toggle_visible(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False
            print("[overlay] 弹幕已隐藏，再按一次可恢复", flush=True)
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.after(200, self._reapply_window_props)
            self.visible = True
            print("[overlay] 弹幕已恢复", flush=True)

    def _reapply_window_props(self):
        """窗口重新映射后扩展样式可能被重置，重新补一遍。"""
        if IS_WINDOWS:
            self.hwnd = resolve_hwnd(self.root)
            if not self.args.band:
                self._apply_transparency()
            if not self.args.no_click_through:
                self._apply_click_through()
        self.keep_topmost()

    def keep_topmost(self):
        """把窗口重新钉回最顶层。有些程序会把自己的窗口设为 TOPMOST 顶上来。"""
        if not IS_WINDOWS:
            return
        try:
            if self.hwnd:
                _user32.SetWindowPos(
                    wintypes.HWND(self.hwnd), wintypes.HWND(HWND_TOPMOST),
                    0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                return
        except Exception:
            pass
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def keep_top(self):
        if self.running:
            if self.visible and not self.args.no_keep_top:
                self.keep_topmost()
            self.root.after(5000, self.keep_top)

    # ------------------------------------------------------------ 设置联动

    def _poll_settings(self):
        """轮询 settings.json；发现改动就立刻应用。"""
        if not self.running or not self.settings_file:
            return
        try:
            stamp = settings_mtime(self.settings_file)
            if stamp and stamp != self.settings_mtime:
                self.settings_mtime = stamp
                self.apply_settings(load_settings(self.settings_file))
        except Exception as exc:
            print(f"[overlay] 应用设置失败：{exc}", flush=True)
        if self.running:
            self.root.after(900, self._poll_settings)

    def apply_settings(self, cfg):
        """把设置面板里的改动落到正在运行的窗口上。"""
        display = (cfg or {}).get("display") or {}
        changed = []

        # ---- 字号：变了要重算轨道 ----
        font = int(display.get("font", self.args.font))
        if font != self.args.font:
            self.args.font = font
            self.lane_height = int(font * 1.8) + self.LANE_GAP
            self._recalc_lanes()
            changed.append(f"字号 {font}")

        # ---- 文字不透明度：要重新设置分层窗口属性 ----
        opacity = float(display.get("opacity", self.args.opacity))
        if abs(opacity - float(self.args.opacity)) > 0.001:
            self.apply_opacity(opacity)
            changed.append(f"不透明度 {opacity:.0%}")

        # ---- 其余项在生成每条弹幕时读取，改完即刻对下一条生效 ----
        simple = [("speed", int, "速度"), ("dim", float, "亮度"),
                  ("density", int, "同屏密度"), ("frame_ms", int, "刷新间隔")]
        for key, cast, label in simple:
            if key in display:
                new = cast(display[key])
                attr = "max_items" if key == "density" else key
                if getattr(self.args, attr) != new:
                    setattr(self.args, attr, new)
                    changed.append(f"{label} {new}")

        for key, attr, label in (("shadow", "no_shadow", "描边"),
                                 ("keep_top", "no_keep_top", "自动置顶")):
            if key in display:
                new = not bool(display[key])
                if getattr(self.args, attr) != new:
                    setattr(self.args, attr, new)
                    changed.append(f"{label} {'开' if not new else '关'}")

        for key, attr, label in (("show_sender", "no_sender", "发送者"),
                                 ("show_chat", "no_chat", "群名")):
            if key in display:
                new = not bool(display[key])
                if getattr(self.args, attr) != new:
                    setattr(self.args, attr, new)
                    changed.append(f"显示{label} {'开' if not new else '关'}")

        # ---- 两个总开关：弹幕 / 气泡通知，各自独立 ----
        for key, attr, label in (("danmaku_enabled", "no_danmaku", "弹幕"),
                                 ("notify_enabled", "no_notify", "气泡通知")):
            if key in display:
                new = not bool(display[key])
                if getattr(self.args, attr) != new:
                    setattr(self.args, attr, new)
                    if new:
                        # 刚关掉：把已经在屏上的收干净，别让它们赖着不走
                        if attr == "no_notify":
                            self._notify_clear()
                        else:
                            self._clear_items()
                    changed.append(f"{label} {'开' if not new else '关'}")

        # ---- 气泡通知的位置与尺寸 ----
        notify_pos = display.get("notify_pos")
        if notify_pos in NOTIFY_POS_TEXT and notify_pos != self.args.notify_pos:
            self.args.notify_pos = notify_pos
            changed.append(f"气泡位置 {NOTIFY_POS_TEXT[notify_pos]}")
        for key, cast, label in (("notify_life", float, "气泡停留"),
                                 ("notify_max", int, "气泡上限"),
                                 ("notify_width", int, "气泡宽度")):
            if key in display:
                new = cast(display[key])
                if getattr(self.args, key) != new:
                    setattr(self.args, key, new)
                    changed.append(f"{label} {new:g}" if isinstance(new, float)
                                   else f"{label} {new}")
        if "notify_width" in display or "notify_pos" in display:
            self._notify_relayout()

        if "filters" in display:
            new_filters = parse_filters(display["filters"])
            if new_filters != self.filters:
                self.set_filters(display["filters"])

        # ---- 显示区域：改了要重新摆放窗口 ----
        region = display.get("region")
        anchor = display.get("anchor", self.args.anchor)
        if region or anchor != self.args.anchor:
            self._apply_region(region or self.args.region, anchor,
                               int(display.get("bar_height", 340)), changed)

        if changed:
            print("[overlay] 设置已更新：" + "，".join(changed), flush=True)

    def _apply_region(self, region, anchor, bar_height, changed):
        """按显示区域重新摆放窗口（透明键是窗口属性，不需要重设）。"""
        probe = argparse.Namespace(geometry="", height=0, anchor=anchor, ratio=0)
        if region == "top":
            probe.ratio = 0.5
        elif region == "bottom":
            probe.anchor, probe.ratio = "bottom", 0.5
        elif region == "bar":
            probe.height = bar_height
        try:
            w, h, x, y = compute_geometry(self.root.winfo_screenwidth(),
                                          self.root.winfo_screenheight(), probe)
        except Exception:
            return
        if (w, h, x, y) == (self.width, self.height, self.origin[0],
                            self.origin[1]):
            self.args.region, self.args.anchor = region, anchor
            return

        self.width, self.height = w, h
        self.origin = (x, y)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.canvas.configure(width=w, height=h)
        self.root.update()
        self._build_check_points()
        self._recalc_lanes()
        self._clear_items()
        self._notify_clear()          # 位置变了，旧气泡的目标坐标也不再成立
        if not self.args.band:
            key = hex_to_colorref(TRANSPARENT_KEY)
            set_color_key(self.hwnd, key, self._alpha_byte())
        if not getattr(self.args, "no_keep_top", False):
            self.keep_topmost()
        self.args.region, self.args.anchor = region, anchor
        changed.append(f"显示区域 {REGION_TEXT.get(region, region)}")

    def _clear_items(self):
        for it in self.items:
            self.canvas.delete(it["id"])
            if it["shadow"]:
                self.canvas.delete(it["shadow"])
        self.items = []

    # ------------------------------------------------------------ 屏蔽词

    def _is_filtered(self, content, sender, chat):
        if not self.filters:
            return False
        haystack = f"{sender} {chat} {content}".lower()
        return any(word.lower() in haystack for word in self.filters)

    def set_filters(self, raw):
        self.filters = parse_filters(raw)
        self.args.filters = raw
        shown = "、".join(self.filters) if self.filters else "（空）"
        print(f"[overlay] 屏蔽关键词 → {shown}", flush=True)

    # ------------------------------------------------------------ 消息

    def drain_queue(self):
        while True:
            try:
                msg = self.queue.get_nowait()
            except queue.Empty:
                break
            self.spawn(msg)
        self.root.after(60, self.drain_queue)

    def spawn(self, msg):
        """一条新消息 → 按开关决定走弹幕、气泡通知，还是两者都走。"""
        content = (msg.get("content") or "").strip()
        if not content:
            return
        sender = msg.get("sender") or msg.get("chat") or "未知"
        chat = msg.get("chat") or ""

        # 关键词屏蔽（面板里可改，改完立刻生效）—— 弹幕与通知一起跳过
        if self._is_filtered(content, sender, chat):
            return

        if not getattr(self.args, "no_danmaku", False):
            self._spawn_danmaku(content, sender, chat)
        if not getattr(self.args, "no_notify", False):
            self._spawn_notify(content, sender, chat, msg)

    def _spawn_danmaku(self, content, sender, chat):
        if len(self.items) >= self.args.max_items:
            return                                  # 屏幕上太挤就先不显示

        show_sender = not getattr(self.args, "no_sender", False)
        show_chat = not getattr(self.args, "no_chat", False)
        if not show_sender:
            text = content
        elif show_chat and chat and chat != sender:
            text = f"[{chat}] {sender}：{content}"
        else:
            text = f"{sender}：{content}"

        font = ("Microsoft YaHei", self.args.font, "bold")
        width = self._text_width(text)
        lane, delay = self._pick_lane(time.time())

        def launch():
            self.hide_hint()
            now = time.time()
            lane_now, _ = self._pick_lane(now)
            y = self.lane_top + lane_now * self.lane_height + self.args.font

            base = color_for(sender)
            color = dim_color(base, self.args.dim)

            x = float(self.width)
            shadow = None
            if not self.args.no_shadow:
                # 先垫一层暗色描边，保证深浅任何壁纸上都看得清
                shadow = self.canvas.create_text(
                    x + 1, y + 1, text=text, anchor="nw", font=font,
                    fill=self.shadow_color())
            item = self.canvas.create_text(
                x, y, text=text, anchor="nw", font=font, fill=color)

            speed = max(20.0, float(self.args.speed))
            self.items.append({
                "id": item, "shadow": shadow, "x": x, "y": y,
                "w": width, "speed": speed,
            })
            self.lanes[lane_now] = now + (width + 40) / speed

        if delay > 0.05:
            self.root.after(int(delay * 1000), launch)
        else:
            launch()

    # ------------------------------------------------------------ 气泡通知
    #
    # 一整张卡片由若干画布元素拼成（阴影、底板、图标、三行文字），
    # 全部 id 存在一个 dict 里统一移动。生命周期分三段：
    #     in   从屏幕边缘滑入 → hold 停留几秒 → out 滑走并销毁

    def _spawn_notify(self, content, sender, chat, msg):
        limit = max(1, int(getattr(self.args, "notify_max", 4)))
        while len(self.notifications) >= limit:
            self._notify_destroy(self.notifications[0])   # 最旧的先退场

        card = self._build_notify_card(content, sender, chat, msg)
        if card is None:
            return
        self.hide_hint()
        self.notifications.append(card)
        self._notify_relayout()

    def _build_notify_card(self, content, sender, chat, msg):
        """把一条消息画成手机通知那样的圆角卡片，返回它的状态字典。"""
        width = max(200, int(getattr(self.args, "notify_width", 320)))
        accent = color_for(sender)
        pad = 14
        text_left = pad + NOTIFY_ICON + 12
        text_width = width - text_left - pad

        # 先在「以卡片左上角为原点」的坐标里画文字，量出高度后再补底板
        title_id = self.canvas.create_text(
            text_left, pad + 2, text=sender, anchor="nw",
            font=("Microsoft YaHei", 11, "bold"), fill=NOTIFY_TITLE)
        meta_id = None
        if chat and chat != sender:
            meta_id = self.canvas.create_text(
                width - pad, pad + 4, text=chat, anchor="ne",
                font=("Microsoft YaHei", 8), fill=NOTIFY_META)
        body_id = self.canvas.create_text(
            text_left, pad + 22, text=content, anchor="nw", width=text_width,
            font=("Microsoft YaHei", 10), fill=NOTIFY_BODY)

        box = self.canvas.bbox(body_id) or (0, 0, 0, pad + 40)
        height = max(66, (box[3] - box[1]) + pad * 2 + 10)

        icon_id = self.canvas.create_polygon(
            rounded_points(pad, pad + 2, pad + NOTIFY_ICON, pad + 2 + NOTIFY_ICON, 9),
            smooth=True, fill=accent, outline=accent)
        glyph_id = self.canvas.create_text(
            pad + NOTIFY_ICON / 2, pad + 2 + NOTIFY_ICON / 2,
            text=(sender or "微")[:1], anchor="center",
            font=("Microsoft YaHei", 11, "bold"), fill=NOTIFY_ICON_TEXT)

        card_id = self.canvas.create_polygon(
            rounded_points(0, 0, width, height, NOTIFY_RADIUS), smooth=True,
            fill=NOTIFY_BG, outline=NOTIFY_EDGE, width=1)
        shadow_id = self.canvas.create_polygon(
            rounded_points(3, 3, width + 3, height + 3, NOTIFY_RADIUS),
            smooth=True, fill=NOTIFY_SHADOW, outline=NOTIFY_SHADOW)

        # 底板是最后画的（要等量出高度），所以画完再把内容逐个提到最上层；
        # 逐个抬升的顺序决定了最终层级：阴影 < 底板 < 标题 < 来源 < 正文 < 图标
        self.canvas.tag_lower(shadow_id, card_id)
        for item in (title_id, meta_id, body_id, icon_id, glyph_id):
            if item:
                self.canvas.tag_raise(item)

        ids = [shadow_id, card_id, icon_id, glyph_id, title_id, body_id]
        if meta_id:
            ids.append(meta_id)

        margin = 16
        pos = getattr(self.args, "notify_pos", "br")
        right_side = pos in ("br", "tr")
        if right_side:
            target_x = self.width - width - margin
            start_x = self.width + 20
        else:
            target_x = margin
            start_x = -width - 20
        start_y = (self.height - height - margin if pos in ("br", "bl")
                   else margin)

        for item in ids:
            self.canvas.move(item, start_x, start_y)

        return {
            "ids": ids, "w": width, "h": height,
            "x": float(start_x), "y": float(start_y),
            "ax": float(start_x), "ay": float(start_y),
            "tx": float(target_x), "ty": float(start_y),
            "born": time.time(), "phase": "in",
            # 退场方向固定：贴着右边就往右滑走，贴着左边就往左滑走。
            # （不能拿「当前 x 与目标 x」比大小来决定方向，那样卡片会在
            #   角落里来回弹，永远退不了场。）
            "out_dx": 360.0 if right_side else -360.0,
            "life": max(1.0, float(getattr(self.args, "notify_life", 6))),
        }

    def _notify_relayout(self):
        """重新分配每个气泡的目标位置：最新的贴着角落，旧的依次让开。"""
        margin, gap = 16, 10
        pos = getattr(self.args, "notify_pos", "br")
        cursor = self.height - margin if pos in ("br", "bl") else margin
        for card in reversed(self.notifications):
            if pos in ("br", "bl"):
                card["ty"] = cursor - card["h"]
                cursor = card["ty"] - gap
            else:
                card["ty"] = cursor
                cursor = card["ty"] + card["h"] + gap
            card["tx"] = (self.width - card["w"] - margin
                          if pos in ("br", "tr") else margin)

    def _notify_destroy(self, card):
        for item in card["ids"]:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        if card in self.notifications:
            self.notifications.remove(card)

    def _notify_clear(self):
        for card in list(self.notifications):
            self._notify_destroy(card)
        self.notifications = []

    def _notify_tick(self, now, dt):
        for card in list(self.notifications):
            if card["phase"] == "in":
                card["x"] += (card["tx"] - card["x"]) * min(1.0, dt * 11)
                if abs(card["tx"] - card["x"]) < 1.0:
                    card["x"] = card["tx"]
                    card["phase"] = "hold"
                    card["born"] = now
            elif card["phase"] == "hold" and now - card["born"] > card["life"]:
                card["phase"] = "out"
            elif card["phase"] == "out":
                card["x"] += card.get("out_dx", 360.0) * dt
                if (card["x"] + card["w"] < -30) or (card["x"] > self.width + 30):
                    self._notify_destroy(card)
                    self._notify_relayout()
                    continue

            card["y"] += (card["ty"] - card["y"]) * min(1.0, dt * 9)
            dx = card["x"] - card["ax"]
            dy = card["y"] - card["ay"]
            if dx or dy:
                for item in card["ids"]:
                    self.canvas.move(item, dx, dy)
                card["ax"], card["ay"] = card["x"], card["y"]


    def shadow_color(self):
        return "#000000" if self.args.band else "#04060a"

    def _text_width(self, text):
        try:
            import tkinter.font as tkfont
            font = tkfont.Font(family="Microsoft YaHei",
                               size=self.args.font, weight="bold")
            return font.measure(text)
        except Exception:
            return len(text) * self.args.font

    # ------------------------------------------------------------ 动画

    def tick(self):
        now = time.time()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now

        alive = []
        for it in self.items:
            it["x"] -= it["speed"] * dt
            if it["x"] + it["w"] < -20:
                self.canvas.delete(it["id"])
                if it["shadow"]:
                    self.canvas.delete(it["shadow"])
                continue
            self.canvas.coords(it["id"], it["x"], it["y"])
            if it["shadow"]:
                self.canvas.coords(it["shadow"], it["x"] + 1, it["y"] + 1)
            alive.append(it)
        self.items = alive

        if self.notifications:
            self._notify_tick(now, dt)

        for i, ready in enumerate(self.lanes):
            if ready > now + 30:
                self.lanes[i] = now

        if self.running:
            self.root.after(self.args.frame_ms, self.tick)

    # ------------------------------------------------------------ 运行

    def quit(self):
        if not self.running:
            return
        self.running = False
        try:
            self.client.stop()
        except Exception:
            pass
        if self.watcher:
            self.watcher.stop()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.print_banner()
        self.root.after(60, self.drain_queue)
        self.root.after(self.args.frame_ms, self.tick)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.quit()

    def print_banner(self):
        mode = "全屏透明" if (self.transparent_ok and not self.args.band) \
            else "半透明黑条"
        print("=" * 58)
        print("   桌面弹幕层  ·  已启动" + ("（自检通过）" if not self.args.skip_check
                                            else "（已跳过自检）"))
        print("=" * 58)
        print(f"   显示模式   {mode}")
        modes = []
        if not getattr(self.args, "no_danmaku", False):
            modes.append("弹幕")
        if not getattr(self.args, "no_notify", False):
            modes.append(f"气泡通知（{NOTIFY_POS_TEXT.get(self.args.notify_pos, '右下角')}）")
        print(f"   显示内容   {'＋'.join(modes) if modes else '（都已关闭，不会有任何显示）'}")
        print(f"   显示区域   {REGION_TEXT.get(self.args.region, self.args.region)}"
              f"   覆盖 {self.width} x {self.height} @ "
              f"({self.origin[0]}, {self.origin[1]})")
        print(f"   弹幕参数   字号 {self.args.font} · 速度 {self.args.speed} · "
              f"不透明度 {float(self.args.opacity):.0%} · "
              f"同屏上限 {self.args.max_items}")
        if self.filters:
            print(f"   屏蔽关键词 {'、'.join(self.filters)}")
        if self.settings_file:
            print(f"   设置联动   面板改完立刻生效（{os.path.basename(self.settings_file)}）")
        print(f"   屏幕尺寸   {self.root.winfo_screenwidth()} x "
              f"{self.root.winfo_screenheight()}")
        print(f"   窗口句柄   {self.hwnd:#x}")
        if self.check_report:
            print("-" * 58)
            for line in self.check_report:
                print(f"   {line}")
        print("-" * 58)
        if not self.click_through and not self.args.no_click_through:
            print("   注意：未能设置穿透样式，弹幕层会挡住鼠标点击。")
            print("         可尝试以管理员身份运行，或改用 --no-click-through。")
        if self.watcher is not None:
            reg = self.watcher.registered
            if reg:
                bits = []
                if "toggle" in reg:
                    bits.append(f"{reg['toggle']} 隐藏/显示")
                if "quit" in reg:
                    bits.append(f"{reg['quit']} 退出")
                print(f"   全局热键   {'    '.join(bits)}")
            else:
                print("   全局热键   注册失败（候选组合都被占用）")
                print("              退出请在本控制台按 Ctrl+C")
        print(f"   消息来源   {self.args.host}:{self.args.port}/events")
        print("-" * 58)
        print("   退出：在控制台按 Ctrl+C")
        print("=" * 58, flush=True)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(
        description="全屏透明桌面弹幕层（鼠标穿透，不影响正常用电脑）")

    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)

    parser.add_argument("--font", type=int, default=26, help="字号，默认 26")
    parser.add_argument("--speed", type=int, default=190, help="速度（像素/秒），默认 190")
    parser.add_argument("--dim", type=float, default=1.0,
                        help="文字亮度系数 0.3~1.0，调低更柔和不刺眼，默认 1.0")
    parser.add_argument("--opacity", type=float, default=1.0,
                        help="文字不透明度 0.35~1.0，默认 1.0。"
                             "调低后文字真正半透明，背景依旧完全透明")
    parser.add_argument("--max-items", type=int, default=45,
                        help="同时在屏的弹幕上限，默认 45")

    parser.add_argument("--filters", default="",
                        help="屏蔽关键词，逗号分隔；命中的消息不上屏")
    parser.add_argument("--no-sender", action="store_true", help="弹幕里不显示发送者")
    parser.add_argument("--no-chat", action="store_true", help="弹幕里不显示群名")

    # ---- 两个总开关：弹幕 / 气泡通知，各自独立 ----
    parser.add_argument("--no-danmaku", action="store_true",
                        help="关闭弹幕（横向滚动字幕）")
    parser.add_argument("--no-notify", action="store_true",
                        help="关闭气泡通知（手机通知样式的角落卡片）")
    parser.add_argument("--notify-pos", choices=list(NOTIFY_POS_TEXT),
                        default="br",
                        help="气泡出现的位置：br 右下（默认）/ tr 右上 / bl 左下 / tl 左上")
    parser.add_argument("--notify-life", type=float, default=6,
                        help="气泡停留秒数，默认 6")
    parser.add_argument("--notify-max", type=int, default=4,
                        help="同屏最多堆叠几个气泡，默认 4")
    parser.add_argument("--notify-width", type=int, default=320,
                        help="气泡卡片宽度（像素），默认 320")

    parser.add_argument("--settings", default=None,
                        help="settings.json 路径。给出后，面板里改了设置会立刻生效，"
                             "无需重启弹幕层。")

    parser.add_argument("--band", action="store_true",
                        help="改用半透明黑条背景（不透明，但更清晰）")
    parser.add_argument("--alpha", type=float, default=0.45, help="黑条模式的不透明度")

    parser.add_argument("--anchor", choices=["top", "bottom"], default="top",
                        help="弹幕从屏幕上方还是下方开始铺，默认 top")
    parser.add_argument("--height", type=int, default=0,
                        help="覆盖高度，默认铺满整个屏幕")
    parser.add_argument("--ratio", type=float, default=0,
                        help="按屏幕高度的比例取范围，如 0.5 = 半屏。"
                             "与 --height 同时给出时以 --height 为准")
    parser.add_argument("--region", choices=list(REGION_TEXT), default="full",
                        help="显示区域：full 全屏 / top 上半屏 / bottom 下半屏 / bar 顶部条带")
    parser.add_argument("--geometry", default="",
                        help="手动指定范围，如 1920x400+0+0（多显示器时可用）")

    parser.add_argument("--frame-ms", type=int, default=28, help="刷新间隔（毫秒）")
    parser.add_argument("--no-shadow", action="store_true", help="不画文字描边")
    parser.add_argument("--no-click-through", action="store_true",
                        help="不做鼠标穿透（弹幕层会接收鼠标事件）")
    parser.add_argument("--no-hotkey", action="store_true", help="不注册全局热键")
    parser.add_argument("--no-keep-top", action="store_true", help="不自动保持置顶")
    parser.add_argument("--quiet", action="store_true", help="不显示启动提示文字")

    # 安全相关开关
    parser.add_argument("--skip-check", action="store_true",
                        help="跳过启动自检（不建议：自检是防止黑屏遮挡的最后一道闸）")
    parser.add_argument("--auto-exit", type=float, default=0, metavar="秒",
                        help="N 秒后自动退出，默认 0（不自动退出）")
    return parser


def main():
    args = build_parser().parse_args()

    # 只给了 --region 而没给范围参数时，按区域推导（面板联动时用得上）
    if not args.height and not args.ratio:
        if args.region == "top":
            args.ratio = 0.5
        elif args.region == "bottom":
            args.anchor, args.ratio = "bottom", 0.5

    if not IS_WINDOWS:
        print("[warn] 非 Windows 系统，透明与穿透效果可能不可用。", flush=True)

    enable_dpi_awareness()          # 必须在创建窗口前调用

    # ★ 建窗口之前先记下桌面原本的样子，供自检对比
    check_points, baseline = [], []
    if IS_WINDOWS and not args.skip_check and not args.band:
        try:
            sw, sh = screen_size()
            if sw and sh:
                w, h, x, y = compute_geometry(sw, sh, args)
                check_points = [(x + px, y + py)
                                for px, py in grid_points(w, h)]
                baseline = sample_screen(check_points)
        except Exception:
            check_points, baseline = [], []

    overlay = Overlay(args, check_points, baseline)
    try:
        overlay.run()
    except KeyboardInterrupt:
        overlay.quit()


if __name__ == "__main__":
    main()
