# -*- coding: utf-8 -*-
"""jobfill —— 网申自动填表工具。

设计目标只有一个：把「一家公司一百多个框」变成「确认一遍点提交」。
  * profile.yaml    个人资料单一数据源
  * field_map.yaml  标签 → 字段 的识别表
  * learned.yaml    你没认出来的字段，答一次记一辈子
  * 绝不自动提交
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
