# -*- coding: utf-8 -*-
"""本地自用版兼容层。

服务器授权和旧额度机制已停用。这个模块只保留旧代码需要的
方法名，让搜索、登录和下载流程继续使用用户自己的平台Cookie。
"""


class LocalAuthManager:
    def validate_user_auth(self, *args, **kwargs):
        return self.get_user_vip_info()

    def get_user_vip_info(self, *args, **kwargs):
        return {
            "valid": True,
            "username": "local",
            "user_id": 0,
            "token": "",
            "is_vip": False,
            "is_svip": False,
            "remaining_time": "",
            "remaining_days": 0,
            "message": "本地自用版",
        }

    def deduct_coins(self, *args, **kwargs):
        return {"success": False, "error": "自用版未启用旧额度处理"}

    def refund_coins(self, *args, **kwargs):
        return {"success": False, "error": "自用版未启用旧额度返还"}

    def get_server_cookie(self, *args, **kwargs):
        return {"success": False, "error": "自用版未启用服务器Cookie"}


class LicenseManager:
    def __init__(self):
        self.is_licensed = True
        self.license_token = "LOCAL_SELF_USE"
        self.cloud_auth = LocalAuthManager()

    def quick_validate(self, *args, **kwargs):
        return self.cloud_auth.get_user_vip_info()

    def validate_license(self, *args, **kwargs):
        return self.quick_validate()

    def check_license(self, *args, **kwargs):
        return True

    def validate(self, *args, **kwargs):
        return True, "本地自用版"

    def get_user_info(self):
        return self.quick_validate()

    def get_encrypted_token(self, *args, **kwargs):
        return self.license_token

    def get_license_token(self, *args, **kwargs):
        return self.license_token

    def verify_token(self, *args, **kwargs):
        return True

    def verify_machine_code(self, *args, **kwargs):
        return True

    def get_machine_info(self):
        return {"lx_code": self._get_machine_code()}

    def _get_machine_code(self):
        return "LOCAL-SELF-USE"

    def get_remaining_days(self, *args, **kwargs):
        return 0


def get_license_manager():
    return LicenseManager()
