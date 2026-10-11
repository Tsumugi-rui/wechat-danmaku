# -*- coding: utf-8 -*-
"""
settings.py —— 统一的设置真源

图形化设置面板（desktop/settings_gui.py）、桌面弹幕层（desktop/overlay.py）、
服务端（app.py）都读写同一个 settings.json，避免「面板改了、实际没生效」。

三方职责：
    设置面板   读写 settings.json，并把改动推给正在运行的弹幕层
    桌面弹幕层 启动时读、运行中轮询，实现改完立刻生效（不必重启）
    服务端     读 server 段决定监听地址与端口
"""

import copy
import json
import os
import socket
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")

# --------------------------------------------------------------------------- #
# 默认设置
# --------------------------------------------------------------------------- #
#
# display 段的字段：
#   总开关    danmaku_enabled  弹幕（滚动字幕）
#             notify_enabled   气泡通知（手机通知样式）
#   显示区域  region           全屏 / 上半屏 / 下半屏 / 顶部条
#   弹幕速度  speed            像素每秒
#   弹幕字号  font
#   不透明度  opacity          文字浓度，越低越柔和
#   弹幕密度  density          同屏最多几条
#   关键词屏蔽 filters
#   气泡通知  notify_pos / notify_life / notify_max / notify_width
# 另加桌面层特有的：文字描边、鼠标穿透、自动置顶、刷新间隔。

DEFAULT_SETTINGS = {
    "server": {
        "host": "127.0.0.1",      # 改成 0.0.0.0 即可让手机推送
        "port": 8765,
        "open_browser": True,
    },
    "sources": {
        "demo": {"enabled": False, "interval": 5.0},
        "wxauto": {"enabled": True, "interval": 0.6, "watch_chats": []},
    },
    "display": {
        "danmaku_enabled": True,  # 弹幕总开关
        "notify_enabled": True,   # 气泡通知总开关
        "region": "full",         # full | top | bottom | bar
        "bar_height": 340,        # region=bar 时覆盖的高度
        "anchor": "top",          # top | bottom
        "speed": 190,
        "font": 26,
        "opacity": 1.0,           # 0.35 ~ 1.0
        "dim": 1.0,               # 文字亮度系数 0.3 ~ 1.0
        "density": 45,            # 同屏弹幕上限
        "shadow": True,           # 文字描边（浅色壁纸也能看清）
        "click_through": True,    # 鼠标穿透
        "keep_top": True,         # 自动保持置顶
        "frame_ms": 28,           # 刷新间隔（毫秒）
        "notify_pos": "br",       # br 右下 | tr 右上 | bl 左下 | tl 左上
        "notify_life": 6,         # 气泡停留秒数
        "notify_max": 4,          # 同屏最多几个气泡
        "notify_width": 320,      # 气泡宽度（像素）
        "popup_life": 8,          # 浏览器展示页弹窗停留秒数
        "sound": False,           # 新消息提示音（浏览器页用）
        "show_sender": True,
        "show_chat": True,
        "filters": [],            # 屏蔽关键词，命中则不上屏
    },
}

REGION_LABELS = {
    "full": "全屏铺满",
    "top": "上半屏",
    "bottom": "下半屏",
    "bar": "顶部条带",
}


# --------------------------------------------------------------------------- #
# 读 / 写
# --------------------------------------------------------------------------- #

def _deep_merge(base, override):
    """把 override 合并进 base（只对 dict 递归），返回新对象。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_settings(path=None):
    """读取设置；文件不存在或损坏时返回默认值（绝不抛异常）。"""
    path = path or SETTINGS_PATH
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user = json.load(fh)
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)
    # 以默认值为底，缺什么补什么（老配置文件也能直接用）
    return _deep_merge(DEFAULT_SETTINGS, user)


def save_settings(cfg, path=None):
    """原子写入：先写临时文件再替换，避免写一半被读到。"""
    path = path or SETTINGS_PATH
    payload = json.dumps(cfg, ensure_ascii=False, indent=2)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def settings_mtime(path=None):
    """返回文件修改时间，用于「改完立刻生效」的轮询判断。"""
    path = path or SETTINGS_PATH
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #

def find_lan_ip():
    """探测本机在局域网中的地址（手机要连的那个）。

    通过「连一个外网地址但不真的发包」拿到默认出口网卡的 IP，
    比遍历网卡更可靠，也不需要联网。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except Exception:
        pass
    finally:
        sock.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def display_url(cfg):
    """按设置算出「该让别人访问的地址」。"""
    server = cfg.get("server", {})
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 8765))
    if host in ("0.0.0.0", "::", ""):
        return f"http://{find_lan_ip()}:{port}/"
    return f"http://{host}:{port}/"


# --------------------------------------------------------------------------- #
# 设置 → 弹幕层命令行
# --------------------------------------------------------------------------- #

def to_overlay_args(display, server=None):
    """把设置翻译成桌面弹幕层的命令行参数。

    弹幕层刻意只认命令行参数（便于单文件调试），由这里统一做翻译，
    保证「面板里看到的」和「实际跑起来的」永远一致。

    server 必须一起传：否则弹幕层会去连默认端口，
    而服务可能被面板改到了别的端口，两边对不上（表现为日志里反复
    disconnected）。
    """
    display = display or {}
    args = []

    if server:
        host = server.get("host", "127.0.0.1")
        if host in ("0.0.0.0", "::", ""):     # 服务监听全网卡，本机连回环
            host = "127.0.0.1"
        args += ["--host", host, "--port", str(int(server.get("port", 8765)))]

    region = display.get("region", "full")
    anchor = display.get("anchor", "top")
    # --region 必须一起传：弹幕层用它判断「当前是什么区域」，
    # 否则面板保存时会把区域误判成 full 而改回全屏。
    args += ["--region", region]

    if region == "full":
        args += ["--anchor", "top", "--height", "0"]
    elif region == "top":
        args += ["--anchor", "top", "--height", "0", "--ratio", "0.5"]
    elif region == "bottom":
        args += ["--anchor", "bottom", "--height", "0", "--ratio", "0.5"]
    else:                                  # bar
        args += ["--anchor", anchor,
                 "--height", str(int(display.get("bar_height", 340)))]

    args += ["--speed", str(int(display.get("speed", 190)))]
    args += ["--font", str(int(display.get("font", 26)))]
    args += ["--dim", str(float(display.get("dim", 1.0)))]
    args += ["--opacity", str(float(display.get("opacity", 1.0)))]
    args += ["--max-items", str(int(display.get("density", 45)))]
    args += ["--frame-ms", str(int(display.get("frame_ms", 28)))]

    if not display.get("shadow", True):
        args.append("--no-shadow")
    if not display.get("click_through", True):
        args.append("--no-click-through")
    if not display.get("keep_top", True):
        args.append("--no-keep-top")
    if not display.get("show_sender", True):
        args.append("--no-sender")
    if not display.get("show_chat", True):
        args.append("--no-chat")

    # ---- 两个总开关：弹幕 / 气泡通知，各自独立 ----
    if not display.get("danmaku_enabled", True):
        args.append("--no-danmaku")
    if not display.get("notify_enabled", True):
        args.append("--no-notify")

    args += ["--notify-pos", str(display.get("notify_pos", "br"))]
    args += ["--notify-life", str(float(display.get("notify_life", 6)))]
    args += ["--notify-max", str(int(display.get("notify_max", 4)))]
    args += ["--notify-width", str(int(display.get("notify_width", 320)))]

    filters = display.get("filters") or []
    if isinstance(filters, str):
        filters = [f.strip() for f in filters.split(",") if f.strip()]
    if filters:
        args += ["--filters", ",".join(filters)]

    return args
