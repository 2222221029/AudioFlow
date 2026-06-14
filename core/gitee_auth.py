# -*- coding: utf-8 -*-
"""旧云认证接口的本地兼容层。"""

from .license_manager import LocalAuthManager


class CloudAuthManager(LocalAuthManager):
    pass

