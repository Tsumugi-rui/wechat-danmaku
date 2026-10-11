# -*- coding: utf-8 -*-
"""
hub.py —— 消息中枢

职责：接收各个消息源（微信监听 / 演示数据 / 外部 HTTP 推送）产生的消息，
统一编号、补齐时间戳，然后广播给所有已连接的前端（SSE 长连接）。

只用标准库实现，无需第三方依赖。
"""

import itertools
import json
import queue
import threading
import time
from collections import deque

# 全局自增序号，保证每条消息 id 唯一
_seq = itertools.count(1)


def build_message(chat, sender, content, mtype="text", source="unknown", extra=None):
    """把任意来源的信息标准化为统一的消息结构。"""
    now = time.time()
    return {
        "id": f"{int(now * 1000)}-{next(_seq)}",
        "chat": str(chat or "").strip(),          # 会话名（好友昵称 / 群名）
        "sender": str(sender or "").strip(),      # 发送者（群聊里就是群成员）
        "content": str(content or ""),            # 正文
        "type": mtype,                            # text / image / file / system
        "source": source,                         # 来源适配器名
        "ts": now,
        "time": time.strftime("%H:%M:%S", time.localtime(now)),
    }


class MessageHub:
    """线程安全的消息广播中心。

    每个前端连接持有一个 queue.Queue，publish 时把消息投递到所有队列。
    队列满了就丢弃最旧的，保证慢客户端不会拖垮服务。
    """

    def __init__(self, history_size=300, queue_size=500):
        self._lock = threading.Lock()
        self._subscribers = {}                      # client_id -> Queue
        self._history = deque(maxlen=history_size)
        self._queue_size = queue_size
        self._counter = itertools.count(1)
        self._stats = {"total": 0, "dropped": 0}

    # ---------- 订阅管理 ----------

    def subscribe(self):
        with self._lock:
            cid = next(self._counter)
            q = queue.Queue(maxsize=self._queue_size)
            self._subscribers[cid] = q
            return cid, q

    def unsubscribe(self, cid):
        with self._lock:
            self._subscribers.pop(cid, None)

    @property
    def subscriber_count(self):
        with self._lock:
            return len(self._subscribers)

    # ---------- 消息发布 ----------

    def publish(self, msg):
        """把一条标准消息广播出去，同时写入历史。"""
        with self._lock:
            self._history.append(msg)
            self._stats["total"] += 1
            targets = list(self._subscribers.values())

        for q in targets:
            try:
                q.put_nowait(msg)
            except queue.Full:
                # 客户端消费不过来，扔掉最旧的一条再塞
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except queue.Empty:
                    pass
                with self._lock:
                    self._stats["dropped"] += 1

    def history(self, limit=100):
        with self._lock:
            items = list(self._history)
        return items[-limit:]

    def clear(self):
        with self._lock:
            self._history.clear()

    def stats(self):
        with self._lock:
            return dict(self._stats)


# 全局单例，供各模块共用
HUB = MessageHub()


__all__ = ["MessageHub", "HUB", "build_message"]
