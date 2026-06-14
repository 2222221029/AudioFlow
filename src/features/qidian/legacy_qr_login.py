# -*- coding: utf-8 -*-
"""Compatibility wrapper for legacy imports.

New code should import from src.features.qidian.audio_system.
"""

from src.features.qidian.audio_system import QidianAudioSystem, QrcodeLogin


__all__ = ["QidianAudioSystem", "QrcodeLogin"]
