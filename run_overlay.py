# -*- coding: utf-8 -*-
"""
run_overlay.py —— 按 settings.json 启动桌面弹幕层

为什么需要这个文件：
    overlay.py 刻意只认命令行参数（便于单文件调试），但设置面板写的是
    settings.json。如果没有统一入口，bat、面板、手动命令三条路就会各自
    拼一套参数，很容易对不上（比如漏传端口，弹幕层就去连了错误的地方）。

用这个启动器，三条路的行为完全一致，且改设置会立刻生效。

用法：
    python run_overlay.py                # 按设置启动，服务没跑就顺手拉起
    python run_overlay.py --no-service   # 不自动拉起后台服务（面板自己管）
    python run_overlay.py --font 40      # 额外参数直接透传给 overlay.py
"""

import os
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from settings import SETTINGS_PATH, load_settings, to_overlay_args   # noqa: E402


def port_open(host, port, timeout=0.6):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, int(port))) == 0


def ensure_service(cfg):
    """服务没在跑就顺手拉起来，省得用户面对一片空白等消息。"""
    server = cfg.get("server", {})
    host = server.get("host", "127.0.0.1")
    probe = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    port = int(server.get("port", 8765))

    if port_open(probe, port):
        print(f"[启动器] 后台服务已在 {probe}:{port} 运行")
        return None

    print(f"[启动器] 后台服务没在跑，先拉起它（{probe}:{port}）…")
    creation = 0x00000008 if sys.platform == "win32" else 0   # DETACHED_PROCESS
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(BASE_DIR, "app.py"), "--no-open"],
            cwd=BASE_DIR, creationflags=creation,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"[启动器] 拉起服务失败：{exc}")
        return None

    for _ in range(20):
        time.sleep(0.4)
        if port_open(probe, port):
            print(f"[启动器] 服务已就绪（{probe}:{port}）")
            return proc
    print("[启动器] 服务启动超时，弹幕层会继续重试连接")
    return proc


def main():
    argv = sys.argv[1:]
    no_service = "--no-service" in argv
    argv = [a for a in argv if a != "--no-service"]

    cfg = load_settings()
    if not no_service:
        ensure_service(cfg)

    cmd = ([sys.executable, "-u", os.path.join(BASE_DIR, "desktop", "overlay.py"),
            "--settings", SETTINGS_PATH]
           + to_overlay_args(cfg.get("display"), cfg.get("server"))
           + argv)
    print("[启动器] 参数与设置面板一致：", " ".join(cmd[3:]), flush=True)
    return subprocess.call(cmd, cwd=BASE_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
