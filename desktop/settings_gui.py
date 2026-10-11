# -*- coding: utf-8 -*-
"""
desktop/settings_gui.py —— 图形化设置面板（微信风格界面）

把弹幕与气泡通知的可调项（显示区域 / 速度 / 字号 / 不透明度 / 密度 /
关键词屏蔽 / 通知位置与停留时间）做成本地可视化面板，改完立刻生效，
不必重启弹幕层。

界面照着微信 Windows 版浅色模式来：左边一条浅灰窄栏（圆角方块 + 单字
图标，选中转微信绿），右边主区是顶栏 + 圆角白卡分组，主色 #07c160。

面板自己负责：
  · 读写 settings.json（唯一设置真源）
  · 实时预览（改滑块，上面那块模拟区域立刻就变）
  · 一键启停后台服务与桌面弹幕层
  · 局域网推送信息（地址、示例命令、手机端怎么配）

放在 desktop/ 下与弹幕层同目录，两者共用 settings.py。
"""

import ctypes
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from settings import (DEFAULT_SETTINGS, SETTINGS_PATH,   # noqa: E402
                      find_lan_ip, load_settings,
                      save_settings, to_overlay_args)

# --------------------------------------------------------------------------- #
# 外观（微信浅色主题）
# --------------------------------------------------------------------------- #
#
# 照着微信 Windows 版浅色模式取色：主色是微信绿 #07c160，页面底浅灰
# #f5f5f5，左侧栏比页面再深一档（#dedede），卡片纯白，分隔线 #e7e7e7，
# 正文近黑。侧栏图标未选中是深灰、选中转绿——微信侧栏最好认的就是这处。

BG          = "#f5f5f5"      # 页面底色
NAV_BG      = "#dedede"      # 左侧导航栏底色
NAV_ICON    = "#4a4a4a"      # 侧栏图标（未选中）
NAV_HOVER   = "#d0d0d0"      # 侧栏悬停
TOPBAR_BG   = "#ededed"      # 顶栏灰
PANEL       = "#ffffff"      # 卡片底
PANEL_2     = "#f7f7f7"      # 次级填充（输入框、次要按钮）
PANEL_3     = "#ebebeb"      # 悬停
LINE        = "#e7e7e7"      # 描边
LINE_2      = "#dcdcdc"      # 稍重一点的描边（按钮边）
TEXT        = "#191919"      # 主文字
MUTED       = "#7f7f7f"      # 次文字
FAINT       = "#a6a6a6"      # 更弱的文字
ACCENT      = "#07c160"      # 主色：微信绿
ACCENT_H    = "#2ecf78"      # 主色悬停
ACCENT_P    = "#06ad56"      # 主色按下
ACCENT_D    = "#06ad56"      # 主色文字（淡底上用）
ACCENT_SOFT = "#e8f8ee"      # 主色淡底
OK          = "#07c160"
WARN        = "#fa9d3b"
DANGER      = "#fa5151"
DANGER_SOFT = "#fdeceb"
PREVIEW_BG  = "#ededed"      # 预览区「桌面」
SHADOW      = "#d9d9d9"
FONT        = "Microsoft YaHei"

WINDOW_W, WINDOW_H = 1060, 900
NAV_W = 64                   # 左侧导航栏宽度

CREATE_NO_WINDOW = 0x08000000

NOTIFY_POS_LABELS = [("br", "右下角"), ("tr", "右上角"),
                     ("bl", "左下角"), ("tl", "左上角")]

# 左侧导航：key / 单字图标 / 小标签
NAV_ITEMS = [
    ("display", "幕", "弹幕"),
    ("look",    "观", "外观"),
    ("notify",  "铃", "通知"),
    ("content", "筛", "内容"),
    ("source",  "源", "来源"),
    ("network", "网", "网络"),
]

# 每一页在顶栏的副标题
PAGE_DESC = {
    "display": "总开关 / 显示区域",
    "look":    "速度 / 字号 / 透明度",
    "notify":  "位置 / 停留 / 尺寸",
    "content": "关键词 / 昵称显示",
    "source":  "微信 / 演示数据",
    "network": "手机推送设置",
}


def enable_dpi_awareness():
    """高分屏下界面才不会被系统拉伸成模糊的。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def font_of(size, bold=False):
    return tkfont.Font(family=FONT, size=size,
                       weight="bold" if bold else "normal")


def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    """在画布上画一个圆角矩形。

    tkinter 没有圆角图元；给 create_polygon 传一组重复点并开 smooth=True，
    样条会把直角收成圆角——这是最省事又不会掉画质的做法。
    """
    r = max(0.0, min(float(r), (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


# --------------------------------------------------------------------------- #
# 小控件
# --------------------------------------------------------------------------- #

class RoundedButton(tk.Canvas):
    """圆角按钮。

    tk.Button 只能画方角（在 Windows 上还带着系统主题的立体感），
    所以自己画一个：圆角底板 + 居中文字，带悬停与按下反馈。
    """

    def __init__(self, parent, text, command=None, kind="ghost", bg=BG,
                 padx=18, height=34, radius=8, size=10, bold=True,
                 min_width=0):
        # 注意：不能叫 self._w / self._h —— tkinter 内部用 self._w 保存控件路径名
        self._font = font_of(size, bold)
        self._bw = int(max(min_width, self._font.measure(text) + padx * 2))
        self._bh = int(height)
        super().__init__(parent, width=self._bw, height=self._bh, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text = text
        self.command = command
        self.kind = kind
        self.radius = radius
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._palette(kind)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.redraw()

    def _palette(self, kind):
        self._colors = {
            #           常态底       常态字       悬停底      按下底      描边
            "primary": (ACCENT,      "#ffffff", ACCENT_H,   ACCENT_P,   None),
            "accent":  (ACCENT_SOFT, ACCENT_D,  "#d9f2e4",  "#cdeedb",  "#bfe9d2"),
            "danger":  (DANGER_SOFT, DANGER,    "#fbe0df",  "#f7d2d1",  "#f2c7c6"),
            "ghost":   (PANEL,       TEXT,      PANEL_2,    PANEL_3,    LINE_2),
        }[kind]

    def _on_enter(self, _e=None):
        self._hover = True
        self.redraw()

    def _on_leave(self, _e=None):
        self._hover = self._pressed = False
        self.redraw()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self.redraw()

    def _on_release(self, event=None):
        was = self._pressed
        self._pressed = False
        self.redraw()
        if was and self._enabled and self.command:
            self.command()

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        self.configure(cursor="hand2" if flag else "arrow")
        self.redraw()

    def set_text(self, text):
        self.text = text
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self._bw, self._bh
        normal, fg, hover, pressed, edge = self._colors
        white_text = self.kind == "primary"
        if not self._enabled:
            fill, color, outline = PANEL_2, FAINT, LINE_2
        elif self._pressed:
            fill = pressed
            outline = pressed if edge is None else edge
            color = "#ffffff" if white_text else fg
        elif self._hover:
            fill = hover
            outline = hover if edge is None else edge
            color = "#ffffff" if white_text else fg
        else:
            fill, color = normal, fg
            outline = normal if edge is None else edge
        rounded_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self.radius,
                     fill=fill, outline=outline, width=1)
        self.create_text(w / 2, h / 2 + 0.5, text=self.text, fill=color,
                         font=self._font)


class Chip(tk.Canvas):
    """互斥选项用的小圆角标签（选中＝微信绿实心白字）。"""

    def __init__(self, parent, text, command, bg=PANEL, height=30, radius=7,
                 padx=16, size=10):
        # 同样避开 self._w（tkinter 内部占用）
        self._font = font_of(size)
        self._bw = int(self._font.measure(text) + padx * 2)
        self._bh = int(height)
        super().__init__(parent, width=self._bw, height=self._bh, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text = text
        self.command = command
        self.radius = radius
        self.selected = False
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.command())
        self.redraw()

    def _set_hover(self, flag):
        self._hover = flag
        self.redraw()

    def set_selected(self, flag):
        self.selected = bool(flag)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self._bw, self._bh
        if self.selected:
            fill, color, edge = ACCENT, "#ffffff", ACCENT
        elif self._hover:
            fill, color, edge = PANEL_2, TEXT, LINE_2
        else:
            fill, color, edge = PANEL, MUTED, LINE_2
        rounded_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, self.radius,
                     fill=fill, outline=edge, width=1)
        self.create_text(w / 2, h / 2 + 0.5, text=self.text, fill=color,
                         font=self._font)


class NavIcon(tk.Canvas):
    """左侧导航栏的一项：单字图标 + 小标签。

    仿微信侧栏——图标平时深灰，选中的那一项转成微信绿，不给底色块
    （底色只在鼠标划过时淡淡浮出来）。
    """

    W, H = NAV_W, 62
    BOX = 38

    def __init__(self, parent, glyph, title, command, bg=NAV_BG):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.glyph = glyph
        self.title = title
        self.command = command
        self.selected = False
        self._hover = False
        self._font_g = font_of(15, True)
        self._font_t = font_of(8)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.command())
        self.redraw()

    def _set_hover(self, flag):
        self._hover = flag
        self.redraw()

    def set_selected(self, flag):
        self.selected = bool(flag)
        self.redraw()

    def redraw(self):
        self.delete("all")
        cx = self.W / 2.0
        top = (self.H - self.BOX) / 2.0 - 4
        x1, y1 = cx - self.BOX / 2.0, top
        x2, y2 = cx + self.BOX / 2.0, top + self.BOX

        if self.selected:
            fill, fg = NAV_BG, ACCENT
        elif self._hover:
            fill, fg = NAV_HOVER, TEXT
        else:
            fill, fg = NAV_BG, NAV_ICON

        rounded_rect(self, x1, y1, x2, y2, 8, fill=fill, outline=fill)
        self.create_text(cx, (y1 + y2) / 2 + 0.5, text=self.glyph, fill=fg,
                         font=self._font_g)
        self.create_text(cx, y2 + 11, text=self.title,
                         fill=ACCENT if self.selected else MUTED,
                         font=self._font_t)


class Badge(tk.Canvas):
    """状态标签：一个小圆点 + 一行灰字（微信里就是这么素的）。"""

    H = 26

    def __init__(self, parent, width=138, bg=TOPBAR_BG):
        super().__init__(parent, width=width, height=self.H, bg=bg,
                         highlightthickness=0, bd=0)
        self._bw = int(width)
        self.text = ""
        self.color = FAINT
        self._font = font_of(9)
        self.redraw()

    def set_state(self, text, color):
        self.text = text
        self.color = color
        self.redraw()

    def redraw(self):
        self.delete("all")
        cy = self.H / 2.0
        self.create_oval(3, cy - 3.5, 10, cy + 3.5, fill=self.color,
                         outline="")
        self.create_text(16, cy + 0.5, text=self.text, anchor="w",
                         fill=MUTED, font=self._font)


class Switch(tk.Canvas):
    """开关：圆角拨杆，比原生 Checkbutton 顺眼。

    拨杆位置由 W / H 直接算：关=左半圆圆心，开=右半圆圆心。
    早先这里写成了 W-H+2 / H-2，两个值几乎相等，所以拨杆看着不动。
    """

    W, H = 42, 24

    def __init__(self, parent, variable, command=None, bg=PANEL):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.command = command
        self._hover = False
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.var.trace_add("write", lambda *_: self.redraw())
        self.redraw()

    def _set_hover(self, flag):
        self._hover = flag
        self.redraw()

    def _toggle(self, _event=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def redraw(self):
        self.delete("all")
        on = bool(self.var.get())
        r = self.H / 2.0
        track = ACCENT if on else ("#ccd1d9" if not self._hover else "#bfc5cf")
        rounded_rect(self, 0, 0, self.W, self.H, r, fill=track, outline=track)
        # 关：圆心在左半圆；开：圆心在右半圆
        cx = (self.W - r) if on else r
        knob_r = r - 3
        self.create_oval(cx - knob_r, 3, cx + knob_r, self.H - 3,
                         fill="#ffffff", outline="")


class CanvasSlider(tk.Canvas):
    """自绘滑块。

    不用 tk.Scale 的原因：它把「滑块本体」和背景画成同一个颜色，
    在深色主题下几乎看不见，拖起来像在盲操。
    """

    H = 24
    PAD = 10
    KNOB_R = 7

    def __init__(self, parent, variable, from_, to, resolution=1,
                 on_change=None):
        super().__init__(parent, height=self.H, bg=PANEL,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.from_ = float(from_)
        self.to_ = float(to)
        self.resolution = resolution or 1
        self.on_change = on_change
        self._dragging = False
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.var.trace_add("write", lambda *_: self.redraw())

    # ---- 取值 ----

    def _knob_x(self, w):
        span = self.to_ - self.from_
        ratio = 0.0
        if span > 0:
            try:
                ratio = (float(self.var.get()) - self.from_) / span
            except Exception:
                ratio = 0.0
        ratio = min(1.0, max(0.0, ratio))
        return self.PAD + ratio * max(1, w - 2 * self.PAD)

    def _value_at(self, x):
        w = max(1, self.winfo_width())
        usable = max(1, w - 2 * self.PAD)
        ratio = min(1.0, max(0.0, (x - self.PAD) / usable))
        raw = self.from_ + ratio * (self.to_ - self.from_)
        raw = round(raw / self.resolution) * self.resolution
        return min(self.to_, max(self.from_, raw))

    # ---- 交互 ----

    def _press(self, event):
        self._dragging = True
        self._apply(self._value_at(event.x))

    def _drag(self, event):
        if self._dragging:
            self._apply(self._value_at(event.x))

    def _release(self, _event):
        self._dragging = False

    def _apply(self, value):
        if isinstance(self.var, tk.IntVar):
            value = int(round(value))
        else:
            value = round(float(value), 3)
        if self.var.get() == value:
            return
        self.var.set(value)
        if self.on_change:
            self.on_change()

    # ---- 绘制 ----

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.H
        if w < 20:
            return
        cy = h / 2
        x = self._knob_x(w)
        self.create_line(self.PAD, cy, w - self.PAD, cy, fill="#e3e3e3",
                         width=4, capstyle="round")
        if x > self.PAD + 1:
            self.create_line(self.PAD, cy, x, cy, fill=ACCENT, width=4,
                             capstyle="round")
        self.create_oval(x - self.KNOB_R, cy - self.KNOB_R,
                         x + self.KNOB_R, cy + self.KNOB_R,
                         fill="#ffffff", outline=ACCENT, width=2)


class SliderRow(tk.Frame):
    """一行滑块：标题 + 当前值 + 拖条。"""

    def __init__(self, parent, label, variable, from_, to, resolution=1,
                 fmt="{:.0f}", suffix="", hint="", on_change=None):
        super().__init__(parent, bg=PANEL)
        self.variable = variable
        self.fmt = fmt
        self.suffix = suffix
        self.on_change = on_change

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=PANEL, fg=TEXT, font=(FONT, 10),
                 anchor="w").pack(side="left")
        self.value_label = tk.Label(head, text="", bg=PANEL, fg=ACCENT_D,
                                    font=(FONT, 10, "bold"), width=8,
                                    anchor="e")
        self.value_label.pack(side="right")

        self.slider = CanvasSlider(self, variable, from_, to, resolution,
                                   on_change=self._changed)
        self.slider.pack(fill="x", pady=(3, 0))

        if hint:
            tk.Label(self, text=hint, bg=PANEL, fg=FAINT, font=(FONT, 9),
                     anchor="w", justify="left").pack(fill="x")
        self.variable.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def _changed(self):
        self.refresh()
        if self.on_change:
            self.on_change()

    def refresh(self):
        try:
            raw = self.variable.get()
        except Exception:
            return
        self.value_label.config(text=f"{self.fmt.format(raw)}{self.suffix}")


class SwitchRow(tk.Frame):
    """一行开关：标题 + 说明 + 拨杆。"""

    def __init__(self, parent, label, variable, hint="", on_change=None,
                 bold_label=False):
        super().__init__(parent, bg=PANEL)
        left = tk.Frame(self, bg=PANEL)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=label, bg=PANEL, fg=TEXT,
                 font=(FONT, 10, "bold") if bold_label else (FONT, 10),
                 anchor="w").pack(fill="x")
        if hint:
            tk.Label(left, text=hint, bg=PANEL, fg=FAINT, font=(FONT, 9),
                     anchor="w", justify="left").pack(fill="x")
        Switch(self, variable, command=on_change, bg=PANEL).pack(side="right",
                                                                 padx=(10, 0))


class RadioRow(tk.Frame):
    """一排互斥按钮，用来选「显示区域」这类少选项的设置。"""

    def __init__(self, parent, label, variable, options, on_change=None):
        super().__init__(parent, bg=PANEL)
        self.variable = variable
        self.on_change = on_change
        self.chips = []

        tk.Label(self, text=label, bg=PANEL, fg=TEXT, font=(FONT, 10),
                 anchor="w").pack(anchor="w")
        row = tk.Frame(self, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        for value, text in options:
            chip = Chip(row, text, lambda v=value: self._pick(v), bg=PANEL)
            chip.pack(side="left", padx=(0, 8))
            self.chips.append((value, chip))
        self.variable.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def _pick(self, value):
        self.variable.set(value)
        if self.on_change:
            self.on_change()

    def refresh(self):
        current = self.variable.get()
        for value, chip in self.chips:
            chip.set_selected(value == current)


class ScrollArea(tk.Frame):
    """可垂直滚动的容器。

    设置项比较多，固定高度装不下；内容装得下时自动隐藏滚动条。
    """

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.vbar = tk.Scrollbar(self, orient="vertical", bd=0, relief="flat",
                                 width=8, command=self.canvas.yview,
                                 bg=LINE_2, troughcolor=BG,
                                 activebackground=ACCENT)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_scroll(self, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vbar.pack_forget()
        elif not self.vbar.winfo_ismapped():
            self.vbar.pack(side="right", fill="y")
        self.vbar.set(first, last)

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _inside(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event):
        widget = self.canvas.winfo_containing(event.x_root, event.y_root)
        if not self._inside(widget):
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class Card(tk.Frame):
    """圆角白底分组卡片。

    tk.Frame 自己画不出圆角，所以拿一块 Canvas 当底：先铺一个圆角多边形，
    再把真正的 body（Frame）挂成 canvas 上的窗口。body 尺寸一变就重算卡片
    高度、重画圆角底，这样圆角能跟着内容长高。
    """

    RADIUS = 6
    PAD = 16

    def __init__(self, parent, title="", subtitle=""):
        super().__init__(parent, bg=BG)
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0,
                                 height=self.PAD * 2 + 40)
        self._canvas.pack(fill="x")
        self.body = tk.Frame(self._canvas, bg=PANEL)
        self._win = self._canvas.create_window(
            self.PAD, self.PAD, window=self.body, anchor="nw")
        if title:
            head = tk.Frame(self.body, bg=PANEL)
            head.pack(fill="x", pady=(0, 10))
            tk.Label(head, text=title, bg=PANEL, fg=TEXT,
                     font=(FONT, 11, "bold"), anchor="w").pack(side="left")
            if subtitle:
                tk.Label(head, text=subtitle, bg=PANEL, fg=FAINT,
                         font=(FONT, 9), anchor="w").pack(side="left",
                                                          padx=(8, 0))
        self._geom = None
        self.body.bind("<Configure>", lambda e: self._relayout())
        self._canvas.bind("<Configure>", lambda e: self._relayout())

    def _relayout(self):
        """按 body 的实际高度重画卡片底，并把 body 宽度拉到卡片内宽。"""
        w = self._canvas.winfo_width()
        if w <= 1:
            return
        self._canvas.itemconfigure(self._win, width=max(1, w - self.PAD * 2))
        h = self.body.winfo_reqheight() + self.PAD * 2
        if self._geom == (w, h):
            return
        self._geom = (w, h)
        if self._canvas.winfo_reqheight() != h:
            self._canvas.configure(height=h)
        self._canvas.delete("cardbg")
        rounded_rect(self._canvas, 0.5, 0.5, w - 0.5, h - 0.5, self.RADIUS,
                     fill=PANEL, outline=LINE, width=1, tags="cardbg")
        self._canvas.tag_lower("cardbg")


# --------------------------------------------------------------------------- #
# 主面板
# --------------------------------------------------------------------------- #

SAMPLE_MESSAGES = [
    ("项目冲刺群", "老王", "今晚十一点发版，大家注意"),
    ("绫地宁宁", "绫地宁宁", "在吗？帮我看下这个 Bug"),
    ("前端·小张", "前端·小张", "PR 我改好了，麻烦 review 一下"),
    ("全家人的群", "妈妈", "天气转凉了，记得加衣服"),
    ("产品需求群", "李工", "需求文档更新了，请查收"),
    ("丛雨", "丛雨", "主人，晚饭记得吃哦"),
]


class SettingsApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.proc_service = None
        self.proc_overlay = None
        self.log_queue = queue.Queue()
        self.preview_items = []
        self.preview_index = 0
        self.preview_lane_ready = []
        self._notify_signature = None
        self._save_job = None
        self._nav_items = {}
        self.page = "display"

        root.title("微信弹幕 · 设置")
        root.configure(bg=BG)
        root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        root.minsize(960, 760)

        self._make_vars()
        self._build_shell()

        self.load_into_ui()
        self.show_page("display")
        self.root.after(80, self._pump_log)
        self.root.after(120, self._preview_tick)
        self.root.after(200, self._preview_spawn)
        self.root.after(900, self._poll_children)

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------ 变量

    def _make_vars(self):
        self.var_danmaku = tk.BooleanVar(value=True)
        self.var_notify = tk.BooleanVar(value=True)

        self.var_region = tk.StringVar(value="full")
        self.var_anchor = tk.StringVar(value="top")
        self.var_speed = tk.IntVar(value=190)
        self.var_font = tk.IntVar(value=26)
        self.var_opacity = tk.DoubleVar(value=1.0)
        self.var_dim = tk.DoubleVar(value=1.0)
        self.var_density = tk.IntVar(value=45)
        self.var_bar_height = tk.IntVar(value=340)
        self.var_shadow = tk.BooleanVar(value=True)
        self.var_click = tk.BooleanVar(value=True)
        self.var_keeptop = tk.BooleanVar(value=True)

        self.var_notify_pos = tk.StringVar(value="br")
        self.var_notify_life = tk.IntVar(value=6)
        self.var_notify_max = tk.IntVar(value=4)
        self.var_notify_width = tk.IntVar(value=320)

        self.var_show_sender = tk.BooleanVar(value=True)
        self.var_show_chat = tk.BooleanVar(value=True)
        self.var_popup_life = tk.IntVar(value=8)
        self.var_sound = tk.BooleanVar(value=False)

        self.var_demo = tk.BooleanVar(value=False)
        self.var_wxauto = tk.BooleanVar(value=True)

        self.var_lan = tk.BooleanVar(value=False)
        self.var_port = tk.IntVar(value=8765)
        self.var_open_browser = tk.BooleanVar(value=True)

    # ------------------------------------------------------------ 外壳

    def _build_shell(self):
        """微信式布局：左边一条窄导航栏，右边是主区。"""
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)

        self._build_nav(shell)

        self.main = tk.Frame(shell, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

        self._build_topbar(self.main)
        self._build_preview(self.main)
        # 底部先摆（side=bottom），最后再让中间区域填充剩余空间，
        # 否则「填充」会把按钮栏和日志挤出窗口外。
        self._build_log(self.main)
        self._build_footer(self.main)
        self._build_body(self.main)

    # ------------------------------------------------------------ 左栏

    def _build_nav(self, parent):
        nav = tk.Frame(parent, bg=NAV_BG, width=NAV_W)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        avatar = tk.Canvas(nav, width=NAV_W, height=62, bg=NAV_BG,
                           highlightthickness=0, bd=0)
        avatar.pack(pady=(12, 0))
        self._draw_avatar(avatar, "雨")

        tk.Frame(nav, height=1, bg="#d0d0d0").pack(fill="x", padx=16, pady=8)

        for key, glyph, title in NAV_ITEMS:
            item = NavIcon(nav, glyph, title,
                           lambda k=key: self.show_page(k))
            item.pack(pady=1)
            self._nav_items[key] = item

        bottom = tk.Frame(nav, bg=NAV_BG)
        bottom.pack(side="bottom", fill="x", pady=(0, 12))
        NavIcon(bottom, "退", "退出", self.on_close).pack()

    def _draw_avatar(self, canvas, text):
        """左上角头像——微信用的是圆角方形，底色就是微信绿。"""
        rounded_rect(canvas, 14, 4, 50, 40, 6, fill=ACCENT, outline=ACCENT)
        canvas.create_text(32, 22, text=text, fill="#ffffff",
                           font=(FONT, 15, "bold"))

    # ------------------------------------------------------------ 顶栏

    def _build_topbar(self, parent):
        bar = tk.Frame(parent, bg=TOPBAR_BG, height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=TOPBAR_BG)
        left.pack(side="left", padx=(18, 0))
        self.lbl_page = tk.Label(left, text="", bg=TOPBAR_BG, fg=TEXT,
                                 font=(FONT, 13, "bold"), anchor="w")
        self.lbl_page.pack(anchor="w", pady=(11, 0))
        self.lbl_desc = tk.Label(left, text="", bg=TOPBAR_BG, fg=MUTED,
                                 font=(FONT, 9), anchor="w")
        self.lbl_desc.pack(anchor="w")

        right = tk.Frame(bar, bg=TOPBAR_BG)
        right.pack(side="right", padx=(0, 18))
        self.badge_overlay = Badge(right)
        self.badge_overlay.pack(side="right", pady=16)
        self.badge_service = Badge(right)
        self.badge_service.pack(side="right", padx=(0, 8), pady=16)

        tk.Frame(parent, height=1, bg=LINE_2).pack(fill="x")

    # ------------------------------------------------------------ 预览

    def _build_preview(self, parent):
        card = Card(parent, "实时预览", "虚线框是弹幕区域，角落卡片是气泡通知")
        card.pack(fill="x", padx=18, pady=(12, 0))

        self.preview = tk.Canvas(card.body, height=150, bg=PREVIEW_BG,
                                 highlightthickness=1,
                                 highlightbackground=LINE_2, bd=0)
        self.preview.pack(fill="x")
        self.preview.bind("<Configure>", lambda e: self._draw_region_box())

    def _preview_sample(self):
        """按当前设置挑一条示例消息，构造预览用的弹幕数据。"""
        if not SAMPLE_MESSAGES:
            return None
        chat, sender, content = SAMPLE_MESSAGES[
            self.preview_index % len(SAMPLE_MESSAGES)]
        self.preview_index += 1
        if self.var_show_sender.get():
            if self.var_show_chat.get() and chat != sender:
                text = f"[{chat}] {sender}：{content}"
            else:
                text = f"{sender}：{content}"
        else:
            text = content
        return sender, text

    def _preview_spawn(self):
        if self.var_danmaku.get():
            limit = max(3, min(12, int(self.var_density.get() / 6) + 3))
            if len(self.preview_items) < limit:
                item = self._preview_sample()
                if item:
                    self._spawn_preview_item(*item)
        self.root.after(2100, self._preview_spawn)

    def _preview_color(self, sender):
        # 预览底色是浅色，所以用饱和度高一点的色板，不然看不清
        palette = ["#1d4ed8", "#6d28d9", "#be185d", "#b91c1c", "#c2410c",
                   "#a16207", "#047857", "#0e7490", "#4338ca", "#a21caf"]
        h = 0
        for ch in (sender or "?"):
            h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        return palette[h % len(palette)]

    def _blend(self, color, base, t):
        """把 color 朝 base 混合 t（0~1），用来近似「不透明度」。"""
        c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(
            int(c[i] * (1 - t) + b[i] * t) for i in range(3))

    def _spawn_preview_item(self, sender, text):
        width = max(320, self.preview.winfo_width())
        height = self.preview.winfo_height()
        if height < 20:
            # 第一帧 canvas 还没量出高度，先用标称值，免得轨道算到 0 把字顶出画布
            height = 150
        font_size = max(9, int(self.var_font.get() * 0.5))
        lane_h = max(24, int(font_size * 1.9))
        lanes = max(1, int((height - 12) / lane_h))
        if len(self.preview_lane_ready) != lanes:
            self.preview_lane_ready = [0.0] * lanes

        # 选一条「已经让开」的轨道；全满就这条先不放——
        # 宁可少显示一条，也别让两条弹幕糊成一团看不清。
        now = time.time()
        lane = next((i for i, ready in enumerate(self.preview_lane_ready)
                     if ready <= now), None)
        if lane is None:
            return
        # 顶部留出 10px 余量，第一条别贴着虚线框
        y = int(10 + lane * lane_h + lane_h / 2)

        base = self._preview_color(sender)
        dim = max(0.3, min(1.0, float(self.var_dim.get())))
        base = self._blend(base, PREVIEW_BG, (1 - dim) * 0.55)
        # canvas 画不出半透明，用向底色混合来近似真实观感
        opacity = max(0.35, min(1.0, float(self.var_opacity.get())))
        color = self._blend(base, PREVIEW_BG, (1 - opacity) * 0.7)

        shadow = None
        if self.var_shadow.get():
            shadow = self.preview.create_text(
                width + 1, y + 1, text=text, anchor="w",
                font=(FONT, font_size, "bold"), fill="#ffffff")
        item = self.preview.create_text(
            width, y, text=text, anchor="w",
            font=(FONT, font_size, "bold"), fill=color)

        speed = max(30.0, float(self.var_speed.get()) * 0.62)
        text_w = sum(1.6 if ord(ch) > 0x2E80 else 0.62 for ch in text) * font_size
        # 留出更大的间隔，免得同一轨道上两条弹幕贴在一起看不清
        self.preview_lane_ready[lane] = now + (text_w + 200) / speed
        self.preview_items.append({"id": item, "shadow": shadow, "x": float(width),
                                   "y": y, "speed": speed})

    def _preview_tick(self):
        try:
            dt = 0.033
            alive = []
            for it in self.preview_items:
                it["x"] -= it["speed"] * dt
                if it["x"] < -self.preview.winfo_width() - 80:
                    self.preview.delete(it["id"])
                    if it["shadow"]:
                        self.preview.delete(it["shadow"])
                    continue
                self.preview.coords(it["id"], it["x"], it["y"])
                if it["shadow"]:
                    self.preview.coords(it["shadow"], it["x"] + 1, it["y"] + 1)
                alive.append(it)
            self.preview_items = alive
        except Exception:
            pass
        self._draw_preview_notify()
        self.root.after(33, self._preview_tick)

    def _draw_region_box(self):
        """在预览里画出弹幕生效区域（上半屏 / 下半屏 / 条带）。"""
        c = self.preview
        c.delete("region")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        region = self.var_region.get()
        if region == "top":
            box = (4, 4, w - 4, h / 2)
        elif region == "bottom":
            box = (4, h / 2, w - 4, h - 4)
        elif region == "bar":
            ratio = min(0.9, max(0.15, self.var_bar_height.get() / 1440.0))
            box = (4, 4, w - 4, h * ratio)
        else:
            box = (4, 4, w - 4, h - 4)
        c.create_rectangle(*box, outline="#8fd9ae", dash=(6, 5), width=1,
                           tags="region")

    def _draw_preview_notify(self):
        """在预览的角落画一张通知卡片，让「气泡通知」的设置看得见。"""
        c = self.preview
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        signature = (self.var_notify.get(), self.var_notify_pos.get(),
                     int(self.var_notify_width.get()),
                     self.var_show_sender.get(), self.var_show_chat.get(), w, h)
        if signature == self._notify_signature:
            return
        self._notify_signature = signature
        c.delete("notify")
        if not self.var_notify.get():
            return

        scale = 0.62
        cw = max(170, int(self.var_notify_width.get() * scale))
        ch = 46
        margin = 8
        pos = self.var_notify_pos.get()
        x = w - cw - margin if pos in ("br", "tr") else margin
        y = h - ch - margin if pos in ("br", "bl") else margin

        chat, sender, content = SAMPLE_MESSAGES[0]
        title = sender if self.var_show_sender.get() else "微信"
        meta = chat if (self.var_show_chat.get() and chat != sender) else ""
        body = content if self.var_show_sender.get() else f"{sender}：{content}"
        accent = self._preview_color(sender)

        rounded_rect(c, x + 2, y + 2, x + cw + 2, y + ch + 2, 10,
                     fill=SHADOW, outline=SHADOW, tags="notify")
        rounded_rect(c, x, y, x + cw, y + ch, 10, fill=PANEL,
                     outline=LINE, tags="notify")
        rounded_rect(c, x + 8, y + 8, x + 30, y + 30, 7,
                     fill=accent, outline=accent, tags="notify")
        c.create_text(x + 19, y + 19, text=(sender or "微")[:1], anchor="center",
                      fill="#ffffff", font=(FONT, 8, "bold"), tags="notify")
        c.create_text(x + 38, y + 12, text=title, anchor="nw", fill=TEXT,
                      font=(FONT, 8, "bold"), tags="notify")
        if meta:
            c.create_text(x + cw - 9, y + 13, text=meta, anchor="ne",
                          fill=FAINT, font=(FONT, 7), tags="notify")
        c.create_text(x + 38, y + 25, text=body[:22], anchor="nw", fill=MUTED,
                      font=(FONT, 7), tags="notify")

    # ------------------------------------------------------------ 主体

    def _build_body(self, parent):
        body = tk.Frame(parent, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(12, 0))

        self.content = ScrollArea(body)
        self.content.pack(fill="both", expand=True)
        self.inner = self.content.inner
        self._build_pages()

    def _new_page(self):
        return tk.Frame(self.inner, bg=BG)

    def _build_pages(self):
        self.pages = {}

        # ---------------- 弹幕与通知（总开关 + 显示区域） ----------------
        page = self._new_page()
        self.pages["display"] = page

        card = Card(page, "显示什么", "两个开关互不影响，可只开其一")
        card.pack(fill="x", pady=(0, 10))
        SwitchRow(card.body, "弹幕", self.var_danmaku,
                  "横向滚动的字幕，从屏幕一侧飘到另一侧，适合边干活边扫一眼",
                  on_change=self.on_change, bold_label=True).pack(fill="x")
        tk.Frame(card.body, height=10, bg=PANEL).pack(fill="x")
        SwitchRow(card.body, "气泡通知", self.var_notify,
                  "手机通知那样的圆角卡片，从角落滑入、停留几秒后滑走",
                  on_change=self.on_change, bold_label=True).pack(fill="x")

        card2 = Card(page, "显示区域", "弹幕与通知都铺在这个范围内")
        card2.pack(fill="x", pady=(0, 10))
        RadioRow(card2.body, "范围", self.var_region,
                 [("full", "全屏铺满"), ("top", "上半屏"),
                  ("bottom", "下半屏"), ("bar", "顶部条带")],
                 on_change=self.on_change).pack(fill="x")
        self.row_bar_height = SliderRow(
            card2.body, "条带高度", self.var_bar_height, 120, 900, 20,
            fmt="{:.0f}", suffix=" px", on_change=self.on_change)
        self.row_bar_height.pack(fill="x", pady=(12, 0))
        self.var_region.trace_add("write", lambda *_: self._sync_region_rows())
        self._sync_region_rows()

        # ---------------- 弹幕外观 ----------------
        page = self._new_page()
        self.pages["look"] = page

        card = Card(page, "弹幕外观", "只影响弹幕，不影响气泡通知")
        card.pack(fill="x", pady=(0, 10))
        SliderRow(card.body, "弹幕速度", self.var_speed, 40, 600, 10,
                  fmt="{:.0f}", suffix=" px/s",
                  hint="数值越大飞得越快", on_change=self.on_change).pack(fill="x")
        SliderRow(card.body, "弹幕字号", self.var_font, 12, 52, 1,
                  fmt="{:.0f}", suffix=" px",
                  on_change=self.on_change).pack(fill="x", pady=(10, 0))
        SliderRow(card.body, "文字不透明度", self.var_opacity, 0.35, 1.0, 0.05,
                  fmt="{:.0%}",
                  hint="调低后文字真正半透明，桌面会透过文字显示；背景始终完全透明",
                  on_change=self.on_change).pack(fill="x", pady=(10, 0))
        SliderRow(card.body, "文字亮度", self.var_dim, 0.3, 1.0, 0.05,
                  fmt="{:.0%}", hint="调低更柔和、不刺眼",
                  on_change=self.on_change).pack(fill="x", pady=(10, 0))
        SliderRow(card.body, "同屏密度", self.var_density, 5, 120, 5,
                  fmt="{:.0f}", suffix=" 条",
                  hint="屏幕上同时最多显示几条", on_change=self.on_change
                  ).pack(fill="x", pady=(10, 0))

        card3 = Card(page, "行为", "")
        card3.pack(fill="x")
        SwitchRow(card3.body, "文字描边", self.var_shadow,
                  "浅色壁纸下也能看清文字", on_change=self.on_change).pack(fill="x")
        tk.Frame(card3.body, height=8, bg=PANEL).pack(fill="x")
        SwitchRow(card3.body, "鼠标穿透", self.var_click,
                  "关掉后弹幕层会挡住鼠标点击，强烈建议保持开启",
                  on_change=self.on_change).pack(fill="x")
        tk.Frame(card3.body, height=8, bg=PANEL).pack(fill="x")
        SwitchRow(card3.body, "自动保持置顶", self.var_keeptop,
                  "始终浮在其他窗口之上", on_change=self.on_change).pack(fill="x")

        # ---------------- 气泡通知 ----------------
        page = self._new_page()
        self.pages["notify"] = page

        card = Card(page, "气泡通知", "手机通知样式：圆角卡片 + 图标 + 两行文字")
        card.pack(fill="x", pady=(0, 10))
        SwitchRow(card.body, "启用气泡通知", self.var_notify,
                  "关掉后只显示弹幕", on_change=self.on_change,
                  bold_label=True).pack(fill="x")
        tk.Frame(card.body, height=12, bg=PANEL).pack(fill="x")
        RadioRow(card.body, "出现位置", self.var_notify_pos,
                 NOTIFY_POS_LABELS, on_change=self.on_change).pack(fill="x")

        card2 = Card(page, "尺寸与节奏", "")
        card2.pack(fill="x", pady=(0, 10))
        SliderRow(card2.body, "停留时间", self.var_notify_life, 2, 30, 1,
                  fmt="{:.0f}", suffix=" 秒",
                  hint="到期后自动向屏幕外滑走",
                  on_change=self.on_change).pack(fill="x")
        SliderRow(card2.body, "同屏上限", self.var_notify_max, 1, 10, 1,
                  fmt="{:.0f}", suffix=" 个",
                  hint="超出时最旧的那张先退场",
                  on_change=self.on_change).pack(fill="x", pady=(10, 0))
        SliderRow(card2.body, "卡片宽度", self.var_notify_width, 220, 520, 10,
                  fmt="{:.0f}", suffix=" px",
                  on_change=self.on_change).pack(fill="x", pady=(10, 0))

        card3 = Card(page, "说明", "")
        card3.pack(fill="x")
        tk.Label(card3.body,
                 text=("气泡出现在「显示区域」的范围内——如果区域选了上半屏，\n"
                       "气泡就落在上半屏的角落。弹幕层是鼠标穿透的，气泡不能点。"),
                 bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w",
                 justify="left").pack(fill="x")

        # ---------------- 内容与屏蔽 ----------------
        page = self._new_page()
        self.pages["content"] = page

        card = Card(page, "显示内容", "")
        card.pack(fill="x", pady=(0, 10))
        SwitchRow(card.body, "显示发送者昵称", self.var_show_sender,
                  "", on_change=self.on_change).pack(fill="x")
        tk.Frame(card.body, height=8, bg=PANEL).pack(fill="x")
        SwitchRow(card.body, "显示群名", self.var_show_chat,
                  "形如 [项目群] 老王：消息内容", on_change=self.on_change).pack(fill="x")

        card2 = Card(page, "关键词屏蔽", "命中的消息不上屏，弹幕与通知一起跳过")
        card2.pack(fill="x", pady=(0, 10))
        self.txt_filters = tk.Text(card2.body, height=6, bg=PANEL_2, fg=TEXT,
                                   insertbackground=ACCENT, bd=0,
                                   highlightthickness=1,
                                   highlightbackground=LINE,
                                   font=(FONT, 10), wrap="word", padx=8, pady=6)
        self.txt_filters.pack(fill="x")
        tk.Label(card2.body, text="一行一个词，或用逗号分隔", bg=PANEL, fg=FAINT,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(4, 0))
        row = tk.Frame(card2.body, bg=PANEL)
        row.pack(fill="x", pady=(8, 0))
        for word in ("红包", "广告", "拼多多", "点赞"):
            chip = Chip(row, f"+ {word}", lambda w=word: self._add_filter(w),
                        bg=PANEL, height=28, radius=7, padx=12, size=9)
            chip.pack(side="left", padx=(0, 6))
        self.txt_filters.bind("<KeyRelease>", lambda e: self.on_change())

        card3 = Card(page, "其他", "")
        card3.pack(fill="x")
        SliderRow(card3.body, "弹窗停留时间", self.var_popup_life, 3, 30, 1,
                  fmt="{:.0f}", suffix=" 秒",
                  hint="浏览器展示页的右下角弹窗（桌面弹幕层不涉及）",
                  on_change=self.on_change).pack(fill="x")
        tk.Frame(card3.body, height=8, bg=PANEL).pack(fill="x")
        SwitchRow(card3.body, "新消息提示音", self.var_sound,
                  "浏览器展示页里生效", on_change=self.on_change).pack(fill="x")

        # ---------------- 消息源 ----------------
        page = self._new_page()
        self.pages["source"] = page

        card = Card(page, "消息来源", "可以同时开启，也可以都关掉，只靠外部推送")
        card.pack(fill="x", pady=(0, 10))
        SwitchRow(card.body, "演示数据", self.var_demo,
                  "播放模拟消息，不需要微信。适合先看效果",
                  on_change=self.on_change).pack(fill="x")
        tk.Frame(card.body, height=8, bg=PANEL).pack(fill="x")
        SwitchRow(card.body, "监听微信消息", self.var_wxauto,
                  "需要安装与微信版本匹配的监听库，见下方说明",
                  on_change=self.on_change).pack(fill="x")

        card_wake = Card(page, "唤醒微信无障碍",
                         "微信 4.1+ 必须做这一步，连不上多半是它")
        card_wake.pack(fill="x", pady=(0, 10))
        # 按钮放在说明之前——它是这一页最要紧的东西，不能被挤到折叠线以下
        wake_row = tk.Frame(card_wake.body, bg=PANEL)
        wake_row.pack(fill="x")
        RoundedButton(wake_row, "唤醒微信", self._wake_wechat, kind="primary",
                      bg=PANEL, height=34, radius=8, size=10).pack(side="left")
        RoundedButton(wake_row, "查看状态",
                      lambda: self._wake_wechat("status"), kind="ghost",
                      bg=PANEL, height=34, radius=8, size=9).pack(side="left",
                                                                  padx=(8, 0))
        tk.Label(card_wake.body,
                 text="微信 4.1+ 是自绘界面，默认不向系统暴露控件树，"
                      "任何监听库都只能\n看到一层 Qt 空壳。先点「唤醒微信」把树打开，"
                      "服务才连得上。\n"
                      "做法：临时置系统读屏标志 + 向微信进程写入 1 个状态字节，"
                      "写完立刻\n校验，不生效会自动回滚；成功后自动收回读屏标志，"
                      "不留全局副作用。\n"
                      "不注入代码、不重启微信、不改微信文件。\n"
                      "⚠ 微信每次重启后，需要重新唤醒一次。",
                 bg=PANEL, fg=MUTED, font=(FONT, 9), justify="left",
                 anchor="w").pack(fill="x", pady=(9, 0))

        card2 = Card(page, "微信监听说明", "")
        card2.pack(fill="x", pady=(0, 10))
        info = tk.Text(card2.body, height=11, bg=PANEL_2, fg="#44506a",
                       bd=0, highlightthickness=1, highlightbackground=LINE,
                       font=(FONT, 9), wrap="word", padx=9, pady=8)
        info.pack(fill="x")
        info.insert("1.0", (
            "监听库与微信版本的对应关系：\n"
            "  · 微信 3.9.x          →  pip install wxauto（旧版已从 PyPI 下架）\n"
            "  · 微信 4.0 ~ 4.1.x    →  pip install wxauto4（免费版）\n"
            "  · 更高版本            →  wxautox4（Plus 版，需激活码）\n\n"
            "wxauto4 免费版的监听方式是回调式，且只有两种读法：\n"
            "  · 不填监听会话  → 读「当前打开的那个聊天窗口」，切到哪个看哪个；\n"
            "  · 填了监听会话  → 为该会话单独开一个窗口后台收，群聊推荐，\n"
            "                     但微信会多出一个窗口，介意就留空。\n\n"
            "若微信版本过新，直接读窗口会连不上——此时用「网络与推送」里的\n"
            "外部推送，任何设备都能把消息投到屏幕上，且与微信版本无关。"))
        info.config(state="disabled")

        card3 = Card(page, "外部推送", "任何程序都能把消息投到屏幕上")
        card3.pack(fill="x")
        tk.Label(card3.body, text="POST /api/push", bg=PANEL, fg=ACCENT_D,
                 font=("Consolas", 11, "bold"), anchor="w").pack(fill="x")
        tk.Label(card3.body,
                 text='{"chat":"项目群","sender":"老王","content":"上线了！"}',
                 bg=PANEL, fg=MUTED, font=("Consolas", 9),
                 anchor="w").pack(fill="x", pady=(2, 0))
        RoundedButton(card3.body, "复制示例命令", self._copy_curl,
                      kind="ghost", bg=PANEL, height=32, radius=8,
                      size=9).pack(anchor="w", pady=(10, 0))

        # ---------------- 网络与推送 ----------------
        page = self._new_page()
        self.pages["network"] = page

        card = Card(page, "监听范围", "决定谁能把消息推过来")
        card.pack(fill="x", pady=(0, 10))
        RadioRow(card.body, "允许访问的设备", self.var_lan,
                 [(False, "仅本机（最安全）"), (True, "局域网内的设备（手机可推送）")],
                 on_change=self.on_change).pack(fill="x")
        tk.Frame(card.body, height=10, bg=PANEL).pack(fill="x")
        SliderRow(card.body, "端口", self.var_port, 1024, 65535, 1,
                  fmt="{:.0f}", hint="改完需要重启后台服务",
                  on_change=self.on_change).pack(fill="x")
        tk.Frame(card.body, height=6, bg=PANEL).pack(fill="x")
        SwitchRow(card.body, "启动服务时自动打开浏览器", self.var_open_browser,
                  "", on_change=self.on_change).pack(fill="x")

        card2 = Card(page, "推送地址", "")
        card2.pack(fill="x", pady=(0, 10))
        self.lbl_url = tk.Label(card2.body, text="", bg=PANEL, fg=ACCENT_D,
                                font=("Consolas", 12, "bold"), anchor="w")
        self.lbl_url.pack(fill="x")
        self.lbl_url_hint = tk.Label(card2.body, text="", bg=PANEL, fg=FAINT,
                                     font=(FONT, 9), anchor="w",
                                     justify="left")
        self.lbl_url_hint.pack(fill="x", pady=(4, 0))
        row = tk.Frame(card2.body, bg=PANEL)
        row.pack(fill="x", pady=(10, 0))
        for text, cmd in (("复制地址", self._copy_url),
                          ("复制推送示例", self._copy_lan_curl)):
            RoundedButton(row, text, cmd, kind="ghost", bg=PANEL, height=32,
                          radius=8, size=9).pack(side="left", padx=(0, 8))
        self.var_lan.trace_add("write", lambda *_: self.refresh_network())
        self.var_port.trace_add("write", lambda *_: self.refresh_network())

        card3 = Card(page, "手机转发怎么配", "不需要装任何东西到电脑上")
        card3.pack(fill="x")
        self.txt_guide = tk.Text(card3.body, height=8, bg=PANEL_2,
                                 fg="#44506a", bd=0, highlightthickness=1,
                                 highlightbackground=LINE, font=(FONT, 9),
                                 wrap="word", padx=9, pady=8)
        self.txt_guide.pack(fill="x")

    def _sync_region_rows(self):
        """只有「顶部条带」才需要条带高度。"""
        if self.var_region.get() == "bar":
            self.row_bar_height.pack(fill="x", pady=(12, 0))
        else:
            self.row_bar_height.pack_forget()
        self._draw_region_box()

    def _add_filter(self, word):
        current = self.txt_filters.get("1.0", "end").strip()
        words = [w.strip() for w in current.replace("，", ",")
                 .replace("\n", ",").split(",") if w.strip()]
        if word not in words:
            words.append(word)
            self.txt_filters.delete("1.0", "end")
            self.txt_filters.insert("1.0", "\n".join(words))
            self.on_change()

    # ------------------------------------------------------------ 底部

    def _build_footer(self, parent):
        footer = tk.Frame(parent, bg=BG)
        footer.pack(side="bottom", fill="x", padx=18, pady=(10, 0))

        left = tk.Frame(footer, bg=BG)
        left.pack(side="left")
        self.btn_apply = RoundedButton(left, "保存设置", self.save_now,
                                       kind="primary", bg=BG)
        self.btn_apply.pack(side="left", padx=(0, 8))
        self.btn_reset = RoundedButton(left, "恢复默认", self.reset_defaults,
                                       kind="ghost", bg=BG)
        self.btn_reset.pack(side="left", padx=(0, 8))

        right = tk.Frame(footer, bg=BG)
        right.pack(side="right")
        self.btn_stop_all = RoundedButton(right, "全部停止", self.stop_all,
                                          kind="danger", bg=BG)
        self.btn_stop_all.pack(side="right", padx=(8, 0))
        self.btn_overlay = RoundedButton(right, "启动桌面弹幕层",
                                         self.toggle_overlay, kind="accent",
                                         bg=BG, min_width=140)
        self.btn_overlay.pack(side="right", padx=(8, 0))
        self.btn_service = RoundedButton(right, "启动后台服务",
                                         self.toggle_service, kind="primary",
                                         bg=BG, min_width=126)
        self.btn_service.pack(side="right", padx=(8, 0))

        self.lbl_saved = tk.Label(left, text="", bg=BG, fg=OK,
                                  font=(FONT, 9))
        self.lbl_saved.pack(side="left", padx=(4, 0))

    def _build_log(self, parent):
        card = Card(parent, "运行日志", "服务与弹幕层的输出")
        card.pack(side="bottom", fill="x", padx=18, pady=(10, 14))

        self.log = tk.Text(card.body, height=4, bg=PANEL_2, fg="#4a4a4a",
                           bd=0, highlightthickness=1, highlightbackground=LINE,
                           font=("Consolas", 9), wrap="word", padx=8, pady=6)
        self.log.pack(fill="both", expand=True)
        self.log.config(state="disabled")

    # ------------------------------------------------------------ 页面切换

    def show_page(self, key):
        self.page = key
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for k, item in self._nav_items.items():
            item.set_selected(k == key)
        # 顶栏跟着换标题，像微信切换会话时那样
        title = dict((k, t) for k, _g, t in NAV_ITEMS).get(key, "设置")
        self.lbl_page.config(text=title)
        self.lbl_desc.config(text=PAGE_DESC.get(key, ""))

    # ------------------------------------------------------------ 读写设置

    def load_into_ui(self):
        cfg = self.cfg
        server = cfg.get("server", {})
        sources = cfg.get("sources", {})
        display = cfg.get("display", {})

        self.var_danmaku.set(bool(display.get("danmaku_enabled", True)))
        self.var_notify.set(bool(display.get("notify_enabled", True)))

        self.var_region.set(display.get("region", "full"))
        self.var_anchor.set(display.get("anchor", "top"))
        self.var_bar_height.set(int(display.get("bar_height", 340)))
        self.var_speed.set(int(display.get("speed", 190)))
        self.var_font.set(int(display.get("font", 26)))
        self.var_opacity.set(float(display.get("opacity", 1.0)))
        self.var_dim.set(float(display.get("dim", 1.0)))
        self.var_density.set(int(display.get("density", 45)))
        self.var_shadow.set(bool(display.get("shadow", True)))
        self.var_click.set(bool(display.get("click_through", True)))
        self.var_keeptop.set(bool(display.get("keep_top", True)))

        pos = display.get("notify_pos", "br")
        if pos not in dict(NOTIFY_POS_LABELS):
            pos = "br"
        self.var_notify_pos.set(pos)
        self.var_notify_life.set(int(display.get("notify_life", 6)))
        self.var_notify_max.set(int(display.get("notify_max", 4)))
        self.var_notify_width.set(int(display.get("notify_width", 320)))

        self.var_show_sender.set(bool(display.get("show_sender", True)))
        self.var_show_chat.set(bool(display.get("show_chat", True)))
        self.var_popup_life.set(int(display.get("popup_life", 8)))
        self.var_sound.set(bool(display.get("sound", False)))

        self.var_demo.set(bool(sources.get("demo", {}).get("enabled", False)))
        self.var_wxauto.set(bool(sources.get("wxauto", {}).get("enabled", True)))

        host = server.get("host", "127.0.0.1")
        self.var_lan.set(host in ("0.0.0.0", "::"))
        self.var_port.set(int(server.get("port", 8765)))
        self.var_open_browser.set(bool(server.get("open_browser", True)))

        filters = display.get("filters") or []
        self.txt_filters.delete("1.0", "end")
        if filters:
            self.txt_filters.insert("1.0", "\n".join(str(f) for f in filters))

        self.refresh_network()

    def collect(self):
        """把界面上的值收拢回设置字典（保留 _说明 等注释键）。"""
        cfg = self.cfg
        cfg.setdefault("server", {})
        cfg.setdefault("sources", {})
        cfg.setdefault("display", {})

        cfg["server"].update({
            "host": "0.0.0.0" if self.var_lan.get() else "127.0.0.1",
            "port": int(self.var_port.get()),
            "open_browser": bool(self.var_open_browser.get()),
        })
        cfg["sources"].setdefault("demo", {}).update(
            {"enabled": bool(self.var_demo.get())})
        cfg["sources"].setdefault("wxauto", {}).update(
            {"enabled": bool(self.var_wxauto.get())})

        raw_filters = self.txt_filters.get("1.0", "end")
        filters = [w.strip() for w in raw_filters.replace("，", ",")
                   .replace("\n", ",").replace("；", ",").split(",") if w.strip()]

        cfg["display"].update({
            "danmaku_enabled": bool(self.var_danmaku.get()),
            "notify_enabled": bool(self.var_notify.get()),
            "region": self.var_region.get(),
            "anchor": self.var_anchor.get(),
            "bar_height": int(self.var_bar_height.get()),
            "speed": int(self.var_speed.get()),
            "font": int(self.var_font.get()),
            "opacity": round(float(self.var_opacity.get()), 2),
            "dim": round(float(self.var_dim.get()), 2),
            "density": int(self.var_density.get()),
            "shadow": bool(self.var_shadow.get()),
            "click_through": bool(self.var_click.get()),
            "keep_top": bool(self.var_keeptop.get()),
            "notify_pos": self.var_notify_pos.get(),
            "notify_life": int(self.var_notify_life.get()),
            "notify_max": int(self.var_notify_max.get()),
            "notify_width": int(self.var_notify_width.get()),
            "show_sender": bool(self.var_show_sender.get()),
            "show_chat": bool(self.var_show_chat.get()),
            "popup_life": int(self.var_popup_life.get()),
            "sound": bool(self.var_sound.get()),
            "filters": filters,
        })
        return cfg

    def on_change(self):
        """滑块一变就存盘——存盘后弹幕层 1 秒内自动应用。"""
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(320, self.save_now)

    def save_now(self):
        self._save_job = None
        try:
            save_settings(self.collect())
        except Exception as exc:
            self.log_line(f"[面板] 保存失败：{exc}")
            return
        self.lbl_saved.config(text="已保存 · 弹幕层会自动应用")
        self.root.after(1800, lambda: self.lbl_saved.config(text=""))
        self.refresh_network()

    def reset_defaults(self):
        if not messagebox.askyesno("恢复默认", "把显示设置恢复成默认值？"):
            return
        self.cfg = dict(DEFAULT_SETTINGS)
        self.load_into_ui()
        self.save_now()
        self.log_line("[面板] 已恢复默认设置")

    # ------------------------------------------------------------ 网络信息

    def refresh_network(self):
        lan = self.var_lan.get()
        port = int(self.var_port.get())
        if lan:
            ip = find_lan_ip()
            url = f"http://{ip}:{port}/"
            self.lbl_url.config(text=url)
            self.lbl_url_hint.config(
                text="手机与这台电脑连同一个 Wi-Fi，即可用上面的地址推送。\n"
                     "首次使用如弹出「防火墙」提示，请选择「允许访问」。")
        else:
            url = f"http://127.0.0.1:{port}/"
            self.lbl_url.config(text=url)
            self.lbl_url_hint.config(
                text="当前只有本机能访问。想让手机推送，把上面的「监听范围」\n"
                     "改成「局域网内的设备」。")

        guide = (
            "手机端（与电脑同一个 Wi-Fi）\n"
            "  1. 监听范围改成「局域网内的设备」，点「保存设置」\n"
            "  2. 重新启动一次后台服务\n"
            f"  3. 手机浏览器打开 {url.replace('/api/push', '')}api/status\n"
            "     能看到 JSON 就说明通了\n\n"
            "然后用下面任一方式自动转发微信通知：\n"
            "  · Android：MacroDroid / Tasker，触发条件选「收到微信通知」，\n"
            f"    动作选「HTTP POST」→ {url}api/push\n"
            '  · 请求体：{"content":"通知内容","sender":"微信"}\n'
        )
        if hasattr(self, "txt_guide"):
            self.txt_guide.config(state="normal")
            self.txt_guide.delete("1.0", "end")
            self.txt_guide.insert("1.0", guide)
            self.txt_guide.config(state="disabled")

    def _curl_cmd(self, base=None):
        base = base or f"http://127.0.0.1:{int(self.var_port.get())}/"
        return ('curl -X POST %sapi/push -H "Content-Type: application/json" '
                '-d "{\\"chat\\":\\"项目群\\",\\"sender\\":\\"老王\\",'
                '\\"content\\":\\"上线了！\\"}"' % base)

    def _to_clipboard(self, text, note):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.lbl_saved.config(text=note)
        self.root.after(1800, lambda: self.lbl_saved.config(text=""))

    def _copy_url(self):
        lan = self.var_lan.get()
        url = (f"http://{find_lan_ip()}:{int(self.var_port.get())}/" if lan
               else f"http://127.0.0.1:{int(self.var_port.get())}/")
        self._to_clipboard(url, "地址已复制")

    def _copy_curl(self):
        base = (f"http://{find_lan_ip()}:{int(self.var_port.get())}/"
                if self.var_lan.get()
                else f"http://127.0.0.1:{int(self.var_port.get())}/")
        self._to_clipboard(self._curl_cmd(base), "推送命令已复制")

    def _copy_lan_curl(self):
        self._copy_curl()

    # ------------------------------------------------------------ 唤醒微信

    def _wake_wechat(self, action="wake"):
        """唤醒微信的无障碍控件树（微信 4.1+ 必需，微信重启后要重做）。

        实际动作交给 tools/wechat_uia_wake.py：它置系统读屏标志、向微信进程
        写入 1 个状态字节，然后用控件树是否物化来校验，不生效自动回滚。
        """
        script = os.path.join(ROOT_DIR, "tools", "wechat_uia_wake.py")
        if not os.path.exists(script):
            self.log_line(f"[唤醒微信] 找不到脚本：{script}")
            return

        if action == "wake":
            self.log_line("[唤醒微信] 开始唤醒…（写入 1 个状态字节，"
                          "不生效会自动回滚；微信不需要重启）")
        else:
            self.log_line("[唤醒微信] 读取当前状态…")

        proc = self._spawn([sys.executable, "-u", script, action], "唤醒微信")
        if proc is None:
            return

        def _wait(handle=proc, act=action):
            try:
                code = handle.wait()
            except Exception:
                return
            if act == "wake":
                if code == 0:
                    self.log_queue.put("[唤醒微信] ✅ 成功，控件树已打开。"
                                       "若服务早就启动着，请去「弹幕与通知」页"
                                       "重启一次服务才能连上。")
                else:
                    self.log_queue.put("[唤醒微信] ❌ 没成功（退出码 %s）。"
                                       "确认微信已登录、窗口没最小化；"
                                       "微信若以管理员运行，本面板也要管理员权限。"
                                       % code)

        threading.Thread(target=_wait, daemon=True).start()

    # ------------------------------------------------------------ 进程管理

    def _spawn(self, args, tag):
        try:
            proc = subprocess.Popen(
                args, cwd=ROOT_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except Exception as exc:
            self.log_line(f"[{tag}] 启动失败：{exc}")
            return None
        threading.Thread(target=self._reader, args=(proc, tag),
                         daemon=True).start()
        return proc

    def _reader(self, proc, tag):
        try:
            for line in proc.stdout:
                self.log_queue.put(f"[{tag}] {line.rstrip()}")
        except Exception:
            pass

    def _kill(self, proc):
        """连子进程一起结束（虚拟环境的 python.exe 会再拉一个解释器）。"""
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               creationflags=CREATE_NO_WINDOW,
                               capture_output=True)
            else:
                proc.terminate()
        except Exception:
            pass

    def toggle_service(self):
        if self.proc_service and self.proc_service.poll() is None:
            self._kill(self.proc_service)
            self.proc_service = None
            self.log_line("[面板] 已停止后台服务")
        else:
            if self._service_reachable():
                # 端口上已经有服务了。再起一个会抢不到端口，白折腾一场。
                self.log_line("[面板] 后台服务已经在运行了（不是本面板启动的），"
                              "不重复启动；要重启请先关掉原来那个窗口")
                self._refresh_buttons()
                return
            self.save_now()
            args = [sys.executable, "-u", "app.py", "--no-open"]
            self.proc_service = self._spawn(args, "服务")
            if self.proc_service:
                self.log_line("[面板] 已启动后台服务")
        self._refresh_buttons()

    def toggle_overlay(self):
        if self.proc_overlay and self.proc_overlay.poll() is None:
            self._kill(self.proc_overlay)
            self.proc_overlay = None
            self.log_line("[面板] 已停止桌面弹幕层")
        elif self._overlay_window():
            hwnd = self._overlay_window()
            if self._ask_overlay_to_quit(hwnd):
                self.log_line("[面板] 弹幕层是其他窗口启动的，已通知它退出")
                self.root.after(1500, self._refresh_buttons)
            else:
                self.log_line("[面板] 无法通知弹幕层退出，请用 Ctrl+Alt+Q "
                              "或关闭它的控制台窗口")
            return
        else:
            self.save_now()
            # 走统一的启动器：它会按 settings.json 生成参数，
            # 和「双击 bat 启动」完全同一条路径。--no-service 表示
            # 后台服务由面板上的按钮管，不让启动器另起一个。
            args = [sys.executable, "-u", "run_overlay.py", "--no-service"]
            self.proc_overlay = self._spawn(args, "弹幕层")
            if self.proc_overlay:
                self.log_line("[面板] 已启动桌面弹幕层")
        self._refresh_buttons()

    def stop_all(self):
        self._kill(self.proc_overlay)
        self._kill(self.proc_service)
        self.proc_overlay = None
        self.proc_service = None
        hwnd = self._overlay_window()          # 别处启动的弹幕层也一并收掉
        if hwnd and self._ask_overlay_to_quit(hwnd):
            self.log_line("[面板] 已通知其他窗口启动的弹幕层退出")
        self.log_line("[面板] 已停止全部")
        self._refresh_buttons()

    def _poll_children(self):
        self._refresh_buttons()
        self.root.after(1200, self._poll_children)

    # ---- 探测「不是本面板启动的」服务和弹幕层 ----
    #
    # 用户常常先双击 bat 起了服务/弹幕层，再打开面板。如果面板只认自己的
    # 子进程，就会显示「未启动」，而且点一下还会再起一个，导致两个服务抢端口。
    # 所以这里主动探测一次运行状态。

    def _service_addr(self):
        host = find_lan_ip() if self.var_lan.get() else "127.0.0.1"
        return host, int(self.var_port.get())

    def _service_reachable(self):
        host, port = self._service_addr()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.35)
                return sock.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _overlay_window(self):
        """返回弹幕层窗口句柄（可能不存在）。

        句柄是 64 位的，必须显式声明 restype，否则会被截断成 32 位。
        """
        if sys.platform != "win32":
            return None
        try:
            user32 = ctypes.windll.user32
            user32.FindWindowW.restype = ctypes.c_void_p
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            return user32.FindWindowW(None, "WeChat Danmaku Overlay")
        except Exception:
            return None

    def _ask_overlay_to_quit(self, hwnd):
        """给别处启动的弹幕层发 WM_CLOSE —— 等同于点它自己的关闭按钮。"""
        try:
            user32 = ctypes.windll.user32
            user32.PostMessageW.restype = ctypes.c_bool
            user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                            ctypes.c_void_p, ctypes.c_void_p]
            return bool(user32.PostMessageW(hwnd, 0x0010, None, None))
        except Exception:
            return False

    def _refresh_buttons(self):
        s_mine = self.proc_service is not None and self.proc_service.poll() is None
        o_mine = self.proc_overlay is not None and self.proc_overlay.poll() is None
        s_outside = (not s_mine) and self._service_reachable()
        o_outside = (not o_mine) and bool(self._overlay_window())

        self.btn_service.set_text("停止后台服务" if s_mine else "启动后台服务")
        self.btn_overlay.set_text("停止桌面弹幕层" if (o_mine or o_outside)
                                  else "启动桌面弹幕层")

        if s_mine:
            text, color = "服务 · 运行中", OK
        elif s_outside:
            text, color = "服务 · 外部启动", WARN
        else:
            text, color = "服务 · 未启动", FAINT
        self.badge_service.set_state(text, color)

        if o_mine:
            text, color = "弹幕 · 运行中", OK
        elif o_outside:
            text, color = "弹幕 · 外部启动", WARN
        else:
            text, color = "弹幕 · 未启动", FAINT
        self.badge_overlay.set_state(text, color)

    # ------------------------------------------------------------ 日志

    def log_line(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _pump_log(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_line(line)
        self.root.after(120, self._pump_log)

    # ------------------------------------------------------------ 关闭

    def on_close(self):
        s_alive = self.proc_service is not None and self.proc_service.poll() is None
        o_alive = self.proc_overlay is not None and self.proc_overlay.poll() is None
        if s_alive or o_alive:
            if not messagebox.askyesno(
                    "退出面板",
                    "后台服务和弹幕层还在运行。\n\n"
                    "选择「是」＝一并停止它们再退出\n"
                    "选择「否」＝只关掉面板，让它们继续跑"):
                self.root.destroy()
                return
            self.stop_all()
        self.root.destroy()


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    SettingsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
