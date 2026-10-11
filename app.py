# -*- coding: utf-8 -*-
"""
app.py —— 微信弹幕 / 弹窗显示服务

一句话说明：把微信消息变成屏幕上的弹幕和弹窗。

架构（全部基于 Python 标准库，无需 pip install 即可运行）：
    消息源（微信监听 / 演示数据 / 外部 HTTP 推送）
        ↓
    MessageHub 广播中心
        ↓  Server-Sent Events 长连接
    浏览器展示页  →  弹幕滚动 / 右下角弹窗 / 顶部横幅

对外接口：
    GET  /                    展示页
    GET  /events              SSE 实时消息流
    GET  /api/status          服务与消息源状态
    GET  /api/messages        最近消息历史
    POST /api/push            外部推送一条消息（万能接入方式）
    POST /api/clear           清空历史

启动：
    python app.py            # 默认尝试监听微信
    python app.py --demo     # 演示模式，不需要微信
"""

import argparse
import json
import mimetypes
import os
import queue
import signal
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 让 `python app.py` 在任意工作目录下都能正确导入同级模块
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hub import HUB, build_message                      # noqa: E402
from sources import SOURCE_REGISTRY                     # noqa: E402

try:
    from settings import load_settings as _load_settings
except Exception:                                       # 缺了也能用旧配置启动
    _load_settings = None

WEB_DIR = os.path.join(BASE_DIR, "web")
DEFAULT_CONFIG = os.path.join(BASE_DIR, "config.json")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

def load_config(path=None, force_demo=False, port_override=None, host_override=None,
                settings_path=None):
    cfg = {
        "host": "127.0.0.1",
        "port": 8765,
        "open_browser": True,
        "sources": {
            "wxauto": {"enabled": True, "interval": 0.6, "watch_chats": [],
                       "include_self": False, "include_system": False},
            "demo": {"enabled": False, "interval": 5.0},
        },
    }
    path = path or DEFAULT_CONFIG
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            for key, value in user_cfg.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
        except Exception as exc:
            print(f"[warn] 读取配置 {path} 失败，使用默认配置：{exc}")

    # 图形化设置面板写的是 settings.json —— 它优先级更高，
    # 这样「面板里选的监听地址 / 消息源」和实际跑起来的服务永远一致。
    spath = settings_path or SETTINGS_PATH
    if _load_settings is not None and os.path.exists(spath):
        try:
            st = _load_settings(spath)
            server = st.get("server") or {}
            cfg["host"] = server.get("host", cfg["host"])
            cfg["port"] = int(server.get("port", cfg["port"]))
            cfg["open_browser"] = bool(server.get("open_browser",
                                                 cfg["open_browser"]))
            for name, raw in (st.get("sources") or {}).items():
                if isinstance(raw, dict):
                    cfg.setdefault("sources", {}).setdefault(name, {}).update(raw)
        except Exception as exc:
            print(f"[warn] 读取 {spath} 失败，改用 config.json：{exc}")

    if force_demo:
        cfg["sources"]["demo"]["enabled"] = True
        cfg["sources"]["wxauto"]["enabled"] = False
    if port_override:
        cfg["port"] = int(port_override)
    if host_override:
        cfg["host"] = host_override
    return cfg


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WeChatDanmaku/1.0"
    sys_version = ""

    # ---------- 基础工具 ----------

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("[http] %s %s\n" % (self.address_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        for encoding in ("utf-8", "gbk"):
            try:
                return json.loads(raw.decode(encoding))
            except Exception:
                continue
        return {}

    # ---------- 路由 ----------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/events":
            return self.serve_events()
        if path == "/api/status":
            return self.send_json(self.server.app.status())
        if path == "/api/messages":
            return self.send_json({"messages": HUB.history(120)})
        if path in ("/", "/index.html"):
            return self.serve_static("index.html")
        if path == "/favicon.ico":
            return self.send_bytes(b"", "image/x-icon", 204)
        return self.serve_static(path.lstrip("/"))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path in ("/api/push", "/push"):
            return self.handle_push()
        if path == "/api/clear":
            HUB.clear()
            return self.send_json({"ok": True})
        return self.send_json({"ok": False, "error": "not found"}, 404)

    # ---------- 各路由实现 ----------

    def serve_static(self, rel_path):
        target = os.path.normpath(os.path.join(WEB_DIR, rel_path))
        if not target.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(target):
            return self.send_json({"ok": False, "error": "not found"}, 404)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            body = fh.read()
        return self.send_bytes(body, ctype)

    def handle_push(self):
        """外部推送入口：任何能发 HTTP 的程序都能把消息投到屏幕上。

        请求体示例：
            {"chat": "项目群", "sender": "老王", "content": "上线了！"}
        """
        payload = self.read_json_body()
        content = payload.get("content") or payload.get("text") or payload.get("message")
        if not content:
            return self.send_json({"ok": False, "error": "content 不能为空"}, 400)

        msg = build_message(
            chat=payload.get("chat") or payload.get("room") or "外部推送",
            sender=payload.get("sender") or payload.get("from") or "外部",
            content=content,
            mtype=payload.get("type") or "text",
            source="http",
        )
        HUB.publish(msg)
        self.server.app.mark_push()
        return self.send_json({"ok": True, "message": msg})

    def serve_events(self):
        """SSE 长连接：持续把新消息推给浏览器。

        使用标准 chunked 传输，保证 Chrome/Edge 能实时收到每一条消息，
        而不是等连接断开才一次性交付。
        """
        cid, q = HUB.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self._cors()
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            self.chunk_write("event: hello\ndata: %s\n\n" % json.dumps(
                {"clients": HUB.subscriber_count, "server_time": time.time()},
                ensure_ascii=False))

            while not self.server.shutdown_flag.is_set():
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    self.chunk_write(": keep-alive\n\n")   # 心跳，防止中间层断链
                    continue
                # 客户端中途清屏时，用 clear 事件通知
                event_name = "message" if msg.get("content") is not None else "message"
                self.chunk_write("event: %s\ndata: %s\n\n" % (
                    event_name, json.dumps(msg, ensure_ascii=False)))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            HUB.unsubscribe(cid)

    def chunk_write(self, text):
        data = text.encode("utf-8")
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #

class App:
    def __init__(self, config):
        self.config = config
        self.sources = []
        self.status_map = {}
        self.last_push = 0.0
        self.started_at = time.time()

    def on_message(self, item):
        HUB.publish(build_message(
            chat=item.get("chat"), sender=item.get("sender"),
            content=item.get("content"), mtype=item.get("type", "text"),
            source=item.get("source", "unknown"),
        ))

    def on_status(self, name, status, detail):
        self.status_map[name] = {"status": status, "detail": detail, "at": time.time()}
        icon = {"running": "●", "error": "✕", "connecting": "◌",
                "stopped": "○", "idle": "○"}.get(status, "·")
        print(f"[source] {icon} {name}: {detail or status}")

    def mark_push(self):
        self.last_push = time.time()
        self.status_map["http"] = {
            "status": "running",
            "detail": f"最近一次推送 {time.strftime('%H:%M:%S')}",
            "at": time.time(),
        }

    def start_sources(self):
        for name, raw in (self.config.get("sources") or {}).items():
            if not raw.get("enabled"):
                continue
            cls = SOURCE_REGISTRY.get(name)
            if cls is None:
                print(f"[warn] 未知消息源：{name}")
                continue
            src = cls(
                config=raw,
                on_message=self.on_message,
                on_status=self.on_status,
                interval=float(raw.get("interval", 1.0)),
            )
            src.start()
            self.sources.append(src)
            self.status_map[name] = {"status": "starting", "detail": "启动中", "at": time.time()}

        if not self.sources:
            self.status_map["http"] = {
                "status": "running",
                "detail": "未启用任何自动消息源，等待外部推送",
                "at": time.time(),
            }

    def stop_sources(self):
        for src in self.sources:
            try:
                src.stop()
            except Exception:
                pass

    def status(self):
        all_sources = {name: src.info() for name, src in
                       ((s.name, s) for s in self.sources)}
        for name, info in self.status_map.items():
            all_sources.setdefault(name, {
                "name": name, "display_name": name,
                "status": info["status"], "detail": info["detail"], "count": 0,
            })
        return {
            "ok": True,
            "uptime": round(time.time() - self.started_at, 1),
            "clients": HUB.subscriber_count,
            "history": len(HUB.history(1000)),
            "stats": HUB.stats(),
            "sources": all_sources,
        }


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def find_free_port(host, port, tries=12):
    for offset in range(tries):
        candidate = port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError(f"{host} 上 {port}~{port + tries - 1} 端口都被占用了")


def main():
    parser = argparse.ArgumentParser(description="微信消息 → 弹幕 / 弹窗")
    parser.add_argument("--demo", action="store_true", help="演示模式（不需要微信，播放模拟消息）")
    parser.add_argument("--host", default=None, help="监听地址，默认 127.0.0.1；手机推送可设 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8765")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出 HTTP 访问日志")
    args = parser.parse_args()

    config = load_config(args.config, force_demo=args.demo,
                         port_override=args.port, host_override=args.host)

    app = App(config)
    host = config.get("host", "127.0.0.1")
    port = find_free_port(host, int(config.get("port", 8765)))

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    server.verbose = args.verbose
    server.shutdown_flag = threading.Event()

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    banner = f"""
============================================================
   微信弹幕 / 弹窗  ·  服务已启动
============================================================
   展示页    {url}
   推送接口  POST {url}api/push
   状态查询  GET  {url}api/status
============================================================
"""
    print(banner)
    if not os.path.exists(DEFAULT_CONFIG):
        print(f"[warn] 未找到配置文件 {DEFAULT_CONFIG}")
    print("按 Ctrl+C 停止服务。\n")

    # 横幅打印完再启动消息源，这样它们的日志不会插在横幅中间
    app.start_sources()

    if config.get("open_browser", True) and not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    def shutdown(signum, frame):
        print("\n[info] 正在停止…")
        server.shutdown_flag.set()

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
    except (ValueError, OSError):
        pass

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown_flag.set()
        app.stop_sources()
        server.server_close()
        print("[info] 已退出。")


if __name__ == "__main__":
    main()
