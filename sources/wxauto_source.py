# -*- coding: utf-8 -*-
"""
sources/wxauto_source.py —— 微信消息监听适配器

原理：微信 PC 版把聊天内容渲染在窗口里，本适配器借助 wxauto 系列库
（基于 Windows UI Automation，属于系统无障碍接口，**不改动微信进程本身**）
读取聊天窗口里新出现的消息文本。

重要前提：
  1. 微信 PC 版必须处于登录状态，且窗口不要最小化（可以放在其他窗口后面）。
  2. 本适配器只读取屏幕上「已经渲染出来」的文字，不做任何进程注入或内存读取。
  3. **微信 4.1+ 需要先「唤醒无障碍树」**，否则 UI Automation 只能看到 Qt 空壳，
     任何库都连不上。项目自带 `tools/wechat_uia_wake.py`，或在设置面板「来源」页
     点一下「唤醒微信」。微信重启后需要重新唤醒。
  4. 版本不匹配时库会明确报错，此时可改用外部推送模式。

两种工作模式：
  A. 指定监听会话（事件驱动）：在 settings.json 的 watch_chats 里填好友昵称或群名，
     适配器用 AddListenChat 注册回调，有新消息时库主动回调，最省 CPU、最实时。
     注意：该模式会让微信为每个会话弹出一个独立的子窗口。
  B. 监听当前窗口（轮询）：watch_chats 留空时使用，反复读取「当前打开的聊天窗口」。
     你手动切到哪个会话，就实时显示哪个会话——不需要额外窗口，适合日常挂着。

接口差异说明（容易踩的坑）：
  · wxauto4 免费版**没有** `GetListenMessage()`（那是 Plus 版的拉取式接口），
    免费版是**回调式**：`AddListenChat(nickname, callback)` + `StartListening()`。
    本模块用线程安全队列把回调桥接成基类需要的拉取式 `poll()`。
  · 免费版也**没有** `CurrentChat()`，当前会话名要从 `ChatInfo()["chat_name"]` 取。
"""

import queue
import threading
import time
from collections import OrderedDict

from .base import BaseSource

#: 单条弹幕的最大长度，太长的消息会把屏幕上整条挡住
MAX_CONTENT_LEN = 120


def _load_candidates():
    """收集所有可用的微信自动化库。

    不同微信版本需要不同的库，且各家对 UI 结构的假设完全不同：
        微信 3.9.x          ->  pip install wxauto
        微信 4.0.x ~ 4.1.8  ->  pip install wxauto4      （免费版，Python 3.9-3.12）
        更高版本 / 3.13     ->  pip install wxautox4     （Plus 版，需激活码）
    返回 (候选列表, 失败原因列表)。
    """
    candidates, errors = [], []
    for module_name in ("wxauto4", "wxauto", "wxautox4", "wxautox"):
        try:
            module = __import__(module_name, fromlist=["WeChat"])
            candidates.append((module_name, getattr(module, "WeChat")))
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    return candidates, errors


def _pick(obj, *names, default=""):
    """从对象/字典/元组里按多种可能的字段名取一个值。"""
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value not in (None, ""):
                return value
    return default


def _clean(text):
    """折掉换行与多余空白——弹幕是单行渲染的，带换行会画歪。"""
    text = " ".join(str(text or "").split())
    if len(text) > MAX_CONTENT_LEN:
        text = text[:MAX_CONTENT_LEN] + "…"
    return text


class WeChatSource(BaseSource):
    name = "wxauto"
    display_name = "微信监听（UI Automation）"

    def __init__(self, config=None, **kwargs):
        super().__init__(config=config, **kwargs)
        self._bot = None
        self._lib = ""
        self._watch = []
        self._seen = OrderedDict()      # 去重用的有序集合
        self._seen_limit = 800
        self._events = queue.Queue()    # 回调线程 -> 轮询线程 的桥
        self._listening = False
        self._last_chat = None          # 当前窗口模式：上次读的是哪个会话

    # ---------- 初始化 ----------

    def setup(self):
        watch = self.config.get("watch_chats") or []
        if isinstance(watch, str):
            watch = [w.strip() for w in watch.split(",") if w.strip()]
        self._watch = [str(w).strip() for w in watch if str(w).strip()]

        candidates, load_errors = _load_candidates()
        if not candidates:
            raise RuntimeError(
                "未检测到任何微信监听库。请按微信版本安装其中一个：\n"
                "    微信 3.9.x  ->  pip install wxauto\n"
                "    微信 4.0.x  ->  pip install wxauto4\n"
                "  也可以改用「外部推送」模式，把消息 POST 到 /api/push。\n"
                "  导入详情：" + " | ".join(load_errors)
            )

        self.report("connecting", "正在连接微信窗口…")
        self._bot, connect_errors = self._connect(candidates)
        if self._bot is None:
            raise RuntimeError(
                "找到监听库但无法连接微信窗口。请依次检查：\n"
                "  1. 微信 PC 版已登录，且窗口没有被最小化；\n"
                "  2. 微信 4.1+ 需要先唤醒无障碍树：\n"
                "     双击项目里的「唤醒微信.bat」，或在设置面板「来源」页点「唤醒微信」；\n"
                "  3. 若微信以管理员身份运行，本程序也要以管理员身份运行。\n"
                "  连接详情：" + " | ".join(connect_errors)
            )

        # 读一次当前窗口，把已有消息记为「已见」，避免一启动就把历史消息全刷上屏
        self._prime_seen()

        self.detail = self._start_listening_or_poll()

    def _connect(self, candidates):
        """按兼容性从好到差依次尝试实例化。

        关键字参数逐个降级：某些老版本不认 ads / resize，硬传会直接抛错。
        """
        errors = []
        for lib_name, cls in candidates:
            for kwargs in ({"ads": False, "resize": False},
                           {"resize": False},
                           {"ads": False},
                           {}):
                try:
                    bot = cls(**kwargs)
                except TypeError as exc:
                    errors.append(f"{lib_name}{sorted(kwargs)}: {exc}")
                    continue
                except Exception as exc:
                    errors.append(f"{lib_name}{sorted(kwargs)}: {exc}")
                    break
                self._lib = lib_name
                return bot, errors
        return None, errors

    def _start_listening_or_poll(self):
        """有监听会话且库支持回调时走事件驱动，否则轮询当前窗口。"""
        bot = self._bot
        can_listen = (callable(getattr(bot, "AddListenChat", None))
                      and callable(getattr(bot, "StartListening", None)))

        parts = [f"库={self._lib}"]
        if self._watch and can_listen:
            ok, fails = 0, []
            for who in self._watch:
                try:
                    bot.AddListenChat(nickname=who, callback=self._on_event)
                    ok += 1
                except Exception as exc:
                    fails.append(f"{who}({exc})")
            if ok:
                try:
                    bot.StartListening()
                    self._listening = True
                except Exception as exc:
                    fails.append(f"StartListening({exc})")
            parts.append(f"事件监听 {ok}/{len(self._watch)} 个会话")
            if fails:
                parts.append("失败：" + "，".join(fails))
            if self._listening:
                return "；".join(parts)

        if self._watch and not can_listen:
            parts.append("当前库不支持回调式监听，已退回「读取当前窗口」模式")
        parts.append("读取当前聊天窗口（切到哪个会话就显示哪个）")
        return "；".join(parts)

    def _prime_seen(self):
        """把当前窗口已有的消息全部记入去重表（不发送），防止启动时刷屏。"""
        try:
            msgs = self._bot.GetAllMessage()
        except Exception:
            return
        try:
            chat = (self._bot.ChatInfo() or {}).get("chat_name", "")
        except Exception:
            chat = ""
        self._last_chat = chat
        for msg in msgs or []:
            key = self._dedup_key(msg)
            if key:
                self._seen[key] = time.time()

    # ---------- 回调桥 ----------

    def _on_event(self, msg, chat=None):
        """wxauto4 回调线程调用：只入队，不做任何重活。"""
        try:
            self._events.put((msg, chat))
        except Exception:
            pass

    # ---------- 轮询 ----------

    def poll(self):
        if self._listening:
            return self._drain_events()
        return self._poll_current_window()

    def _drain_events(self):
        """把回调线程塞进队列的消息取出来。"""
        result = []
        while True:
            try:
                msg, chat = self._events.get_nowait()
            except queue.Empty:
                break
            name = _pick(chat, "who", "nickname", "name", default="") if chat else ""
            parsed = self._parse(msg, name)
            if parsed:
                result.append(parsed)
        return result

    def _poll_current_window(self):
        """模式 B：读取当前打开聊天窗口的新消息。"""
        result = []
        try:
            msgs = self._bot.GetAllMessage()
        except AttributeError:
            msgs = self._bot.GetAllMessages()
        except Exception as exc:
            raise RuntimeError(f"读取当前窗口消息失败：{exc}")

        chat = ""
        info = {}
        try:
            info = self._bot.ChatInfo() or {}
            chat = info.get("chat_name", "")
        except Exception:
            pass

        # 会话被切换过：把新会话的历史消息记为已见，本轮不输出，
        # 否则一切换就会把整屏旧消息全刷出来。
        if chat and chat != self._last_chat:
            self._last_chat = chat
            for msg in msgs or []:
                key = self._dedup_key(msg)
                if key:
                    self._seen[key] = time.time()
            return []

        for msg in msgs or []:
            parsed = self._parse(msg, chat)
            if parsed:
                result.append(parsed)
        return result

    # ---------- 消息解析 ----------

    def _dedup_key(self, msg):
        key = _pick(msg, "id", "msg_id", default="")
        if key:
            return str(key)
        content = _pick(msg, "content", "text", "msg", default="")
        return f"{type(msg).__name__}|{content}|{getattr(msg, 'sender', '')}" if content else ""

    def _parse(self, msg, chat_hint=""):
        """把 wxauto 的消息对象转成本项目的统一格式。失败返回 None。"""
        cls = type(msg).__name__
        low = cls.lower()

        # 自己发的消息（sender == 'self' 或类名以 Self 开头）
        is_self = (str(getattr(msg, "sender", "")).lower() == "self"
                   or low.startswith("self"))
        # 系统消息：时间分隔、拍一拍、撤回提示等
        is_system = ("system" in low or "time" in low or "recall" in low
                     or str(getattr(msg, "sender", "")).lower() == "system")

        if is_system and not self.config.get("include_system", False):
            return None
        if is_self and not self.config.get("include_self", False):
            return None

        # 消息类型：老版本库可能给 type 字段，新版本靠类名判断
        mtype = str(_pick(msg, "type", "msg_type", default="")).lower() or low

        content = _pick(msg, "content", "text", "msg", default="")
        if not content:
            return None
        content = _clean(content)

        # 会话名：优先取消息自带的 chat_info，其次用调用方给的提示
        chat_info = getattr(msg, "chat_info", None) or {}
        chat = _pick(chat_info, "chat_name", "name", default="") or chat_hint
        sender = str(_pick(msg, "sender", "who", "nickname", default=""))

        kind = "text"
        if "image" in mtype or "emotion" in mtype:
            kind = "image"
        elif any(w in mtype for w in ("file", "video", "voice", "link", "card")):
            kind = "file"

        # 去重
        dedup_key = self._dedup_key(msg)
        if dedup_key and dedup_key in self._seen:
            return None
        if dedup_key:
            self._seen[dedup_key] = time.time()
            if len(self._seen) > self._seen_limit:
                self._seen.popitem(last=False)

        return {
            "chat": chat,
            # 老版本库可能不给 sender 字段，退而用会话名兜底
            "sender": sender or chat,
            "content": content,
            "type": kind,
        }

    # ---------- 收尾 ----------

    def stop(self):
        if self._listening and self._bot is not None:
            try:
                self._bot.StopListening()
            except Exception:
                pass
            self._listening = False
        super().stop()
