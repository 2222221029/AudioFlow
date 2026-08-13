from core.ximalaya_credentials import (
    has_ximalaya_mobile_ticket,
    has_ximalaya_web_cookie,
    merge_ximalaya_credentials,
)


def test_qr_cookie_refresh_preserves_existing_mobile_ticket():
    existing = "_token=old-user-token; xmly_x_tk=mobile-ticket"
    incoming = "_token=new-user-token; device_id=browser-device"

    merged = merge_ximalaya_credentials(existing, incoming)

    assert merged == "_token=new-user-token; device_id=browser-device; xmly_x_tk=mobile-ticket"


def test_mobile_ticket_can_be_updated_without_erasing_web_cookie():
    existing = "_token=user-token; device_id=browser-device; xmly_x_tk=old-ticket"

    merged = merge_ximalaya_credentials(existing, "xmly_x_tk=new-ticket")

    assert merged == "_token=user-token; device_id=browser-device; xmly_x_tk=new-ticket"


def test_explicit_combined_credentials_replace_both_parts():
    existing = "_token=old-token; xmly_x_tk=old-ticket"

    merged = merge_ximalaya_credentials(existing, "_token=new-token; xmly_x_tk=new-ticket")

    assert merged == "_token=new-token; xmly_x_tk=new-ticket"


def test_mobile_ticket_status_is_case_insensitive_and_never_requires_value_access():
    assert has_ximalaya_mobile_ticket("_token=user-token; XMLY_X_TK=mobile-ticket") is True
    assert has_ximalaya_mobile_ticket("_token=user-token") is False


def test_web_cookie_status_does_not_treat_a_standalone_mobile_ticket_as_browser_login():
    assert has_ximalaya_web_cookie("xmly_x_tk=mobile-ticket") is False
    assert has_ximalaya_web_cookie("_token=user-token; xmly_x_tk=mobile-ticket") is True
