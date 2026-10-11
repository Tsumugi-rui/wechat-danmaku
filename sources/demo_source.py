# -*- coding: utf-8 -*-
"""
sources/demo_source.py —— 演示数据源

用途：在没有微信环境 / 不想开微信时，也能立刻看到弹幕与弹窗效果。
开启方式：config.json 里把 demo.enabled 设为 true，或在启动时加 --demo 参数。
"""

import random

from .base import BaseSource

DEMO_FRIENDS = [
    "绫地宁宁", "因幡巡", "茉子", "丛雨", "蕾娜",
    "产品经理·老王", "前端·小张", "设计·阿May",
]

DEMO_GROUPS = [
    "项目冲刺群", "全家人的群", "大学同学会", "摸鱼交流群",
]

DEMO_TEXTS = [
    "在吗？帮我看个东西",
    "今天的会议纪要发你了",
    "这个需求明天能上线吗？",
    "晚上一起吃饭？",
    "收到，我马上处理",
    "哈哈哈哈笑死我了",
    "文件已经上传到群里了",
    "这个 Bug 我复现了，是缓存问题",
    "预算表更新了一版，你看看",
    "周末有局吗，人齐了",
    "刚刚那个方案我改了下",
    "楼下奶茶店买一送一，有人要吗",
    "记得把周报交一下",
    "服务器有点慢，我看下 CPU",
    "新版本灰度放量到 30% 了",
    "今晚 11 点要发版，别睡太早",
]

DEMO_IMAGES = ["[图片] 屏幕截图", "[图片] 表情包", "[文件] 需求文档v3.pdf"]


class DemoSource(BaseSource):
    name = "demo"
    display_name = "演示数据源"

    def __init__(self, config=None, **kwargs):
        super().__init__(config=config, **kwargs)
        self._pools = self.config.get("pools")

    def setup(self):
        self.report("running", "正在播放演示消息（非真实微信数据）")

    def poll(self):
        count = random.randint(1, 3)
        results = []
        for _ in range(count):
            is_group = random.random() < 0.45
            if is_group:
                chat = random.choice(DEMO_GROUPS)
                sender = random.choice(DEMO_FRIENDS)
            else:
                chat = random.choice(DEMO_FRIENDS)
                sender = chat

            content = random.choice(DEMO_TEXTS)
            if random.random() < 0.12:
                content = random.choice(DEMO_IMAGES)

            results.append({
                "chat": chat,
                "sender": sender,
                "content": content,
                "type": "text",
            })
        return results
