# -*- coding: utf-8 -*-
"""消息源集合。"""

from .base import BaseSource
from .demo_source import DemoSource
from .wxauto_source import WeChatSource

# 名称 -> 类，app.py 按需实例化
SOURCE_REGISTRY = {
    WeChatSource.name: WeChatSource,
    DemoSource.name: DemoSource,
}

__all__ = ["BaseSource", "WeChatSource", "DemoSource", "SOURCE_REGISTRY"]
