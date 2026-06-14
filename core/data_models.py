#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""数据模型 - 所有平台统一的 Book 数据类"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Book:
    """统一的书籍数据模型"""
    title: str = ""
    author: str = ""
    description: str = ""
    book_id: str = ""
    cover_url: str = ""
    source: str = ""
    category: str = ""
    status: str = ""
    episodes: int = 0
    plays: int = 0
    tags: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
