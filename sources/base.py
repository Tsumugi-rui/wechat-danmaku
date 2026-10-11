# -*- coding: utf-8 -*-
"""
sources/base.py —— 消息源基类

所有消息源都在独立线程中运行，通过回调把消息交给 MessageHub。
"""

import threading
import time


class BaseSource(threading.Thread):
    """消息源基类。

    子类只需实现 poll()，返回本次拉取到的新消息列表。
    每条消息都是 dict：{"chat", "sender", "content", "type"(可选)}
    """

    name = "base"
    display_name = "基础源"

    def __init__(self, config=None, on_message=None, on_status=None, interval=1.0):
        super().__init__(daemon=True, name=f"source-{self.name}")
        self.config = config or {}
        self.on_message = on_message or (lambda msg: None)
        self.on_status = on_status or (lambda name, status, detail="": None)
        self.interval = interval
        self._stop_event = threading.Event()
        self.status = "idle"
        self.detail = ""
        self.count = 0

    # ---------- 生命周期 ----------

    def run(self):
        try:
            self.setup()
        except Exception as exc:  # 初始化失败不该拖垮整个服务
            self.report("error", f"初始化失败：{exc}")
            return

        self.report("running", self.detail or "监听中")
        while not self._stop_event.is_set():
            started = time.time()
            try:
                messages = self.poll()
                for item in messages or []:
                    self.emit(item)
            except Exception as exc:
                self.report("error", f"轮询异常：{exc}")
                self._stop_event.wait(min(5.0, self.interval * 4))
                continue

            # 按固定节奏轮询，扣掉本次耗时
            elapsed = time.time() - started
            self._stop_event.wait(max(0.05, self.interval - elapsed))

        self.report("stopped", "已停止")

    def stop(self):
        self._stop_event.set()

    # ---------- 需要子类实现 ----------

    def setup(self):
        """在轮询开始前做一些准备工作（可选）。"""

    def poll(self):
        """返回本轮的新消息列表。"""
        raise NotImplementedError

    # ---------- 工具方法 ----------

    def emit(self, item):
        self.count += 1
        self.on_message({
            "chat": item.get("chat", ""),
            "sender": item.get("sender", ""),
            "content": item.get("content", ""),
            "type": item.get("type", "text"),
            "source": self.name,
        })

    def report(self, status, detail=""):
        self.status = status
        self.detail = detail
        self.on_status(self.name, status, detail)

    def info(self):
        return {
            "name": self.name,
            "display_name": self.display_name,
            "status": self.status,
            "detail": self.detail,
            "count": self.count,
        }
