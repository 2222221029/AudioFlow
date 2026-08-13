from core.ximalaya_credentials import (
    extract_ximalaya_mobile_ticket,
    has_ximalaya_mobile_ticket,
    has_ximalaya_web_cookie,
    merge_ximalaya_credentials,
    remove_ximalaya_mobile_ticket,
    save_ximalaya_mobile_ticket,
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


def test_mobile_ticket_accepts_raw_header_and_alias_formats():
    assert extract_ximalaya_mobile_ticket("raw-mobile-ticket") == "raw-mobile-ticket"
    assert extract_ximalaya_mobile_ticket("x-tk: header-ticket") == "header-ticket"
    assert extract_ximalaya_mobile_ticket("x_tk=cookie-ticket") == "cookie-ticket"
    assert extract_ximalaya_mobile_ticket("x-tk=") == ""


def test_independent_mobile_ticket_save_preserves_web_cookie():
    existing = "_token=user-token; device_id=browser-device"

    saved = save_ximalaya_mobile_ticket(existing, "x-tk: mobile-ticket")

    assert saved == "_token=user-token; device_id=browser-device; xmly_x_tk=mobile-ticket"


def test_raw_ticket_through_legacy_merge_is_saved_as_mobile_ticket():
    merged = merge_ximalaya_credentials("_token=user-token", "raw-mobile-ticket")

    assert merged == "_token=user-token; xmly_x_tk=raw-mobile-ticket"


def test_removing_mobile_ticket_preserves_web_cookie():
    existing = "_token=user-token; xmly_x_tk=mobile-ticket; device_id=browser-device"

    remaining = remove_ximalaya_mobile_ticket(existing)

    assert remaining == "_token=user-token; device_id=browser-device"
