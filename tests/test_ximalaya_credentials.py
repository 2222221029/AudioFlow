import base64

from core.ximalaya_credentials import (
    MOBILE_V4_ANONYMOUS_TICKET,
    extract_ximalaya_mobile_ticket,
    has_ximalaya_mobile_credentials,
    has_ximalaya_mobile_ticket,
    has_ximalaya_web_cookie,
    merge_ximalaya_credentials,
    normalize_ximalaya_mobile_credentials,
    remove_ximalaya_mobile_ticket,
    save_ximalaya_mobile_ticket,
    ximalaya_mobile_credential_status,
    ximalaya_mobile_ticket_uid,
)


def _ticket_for_uid(uid):
    payload = b"ticket-prefix" + f"close!0!0!b=ticket&s=close&uid={uid}".encode()
    return "TAC" + base64.urlsafe_b64encode(payload).decode().rstrip("=")


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


def test_anonymous_uid_zero_ticket_is_never_reported_as_ready():
    assert has_ximalaya_mobile_ticket({"x_tk": MOBILE_V4_ANONYMOUS_TICKET}) is False
    status = ximalaya_mobile_credential_status({"x_tk": MOBILE_V4_ANONYMOUS_TICKET})
    assert status["state"] == "anonymous_ticket"
    assert status["complete"] is False


def test_any_uid_zero_ticket_variant_is_rejected():
    ticket = _ticket_for_uid(0)
    status = ximalaya_mobile_credential_status({
        "x_tk": ticket,
        "cookie": "1&*token=123456&session",
        "user_agent": "ting_9.4.52.3(android)",
    })

    assert ximalaya_mobile_ticket_uid(ticket) == "0"
    assert status["state"] == "anonymous_ticket"
    assert status["complete"] is False


def test_mobile_ticket_and_cookie_account_must_match():
    status = ximalaya_mobile_credential_status({
        "x_tk": _ticket_for_uid(654321),
        "cookie": "1&*token=123456&session",
        "user_agent": "ting_9.4.52.3(android)",
    })

    assert status["state"] == "account_mismatch"
    assert status["account_match"] is False


def test_v4_sign_is_not_accepted_as_x_tk():
    sign = base64.urlsafe_b64encode(b"x" * 32).decode()
    status = ximalaya_mobile_credential_status({
        "x_tk": sign,
        "cookie": "1&*token=123456&session",
        "user_agent": "ting_9.4.52.3(android)",
    })

    assert status["state"] == "sign_as_ticket"
    assert status["complete"] is False


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


def test_stream_full_headers_are_parsed_as_one_mobile_bundle():
    captured = """Host: mobile.ximalaya.com
Accept: */*
Cookie: channel=ios-b1; 1&_device=iPhone&example-device&9.4.94; 1&*token=123456&example-session
User-Agent: ting_v9.4.94_c5(CFNetwork, iOS 26.1, iPhone15,2)
x-tk: signed-mobile-ticket
Accept-Language: zh-CN,zh-Hans;q=0.9
"""

    credential = normalize_ximalaya_mobile_credentials(captured)

    assert credential["x_tk"] == "signed-mobile-ticket"
    assert credential["cookie"].startswith("channel=ios-b1;")
    assert credential["user_agent"].startswith("ting_v9.4.94")
    assert credential["device"] == "ios"
    assert has_ximalaya_mobile_credentials(credential) is True
    assert ximalaya_mobile_credential_status(credential)["state"] == "complete"


def test_x_tk_alone_and_web_cookie_are_not_mobile_credentials():
    assert normalize_ximalaya_mobile_credentials("") == {}
    assert has_ximalaya_mobile_credentials("x-tk: signed-mobile-ticket") is False
    assert has_ximalaya_mobile_credentials("_token=browser-user") is False
    assert ximalaya_mobile_credential_status("x-tk: signed-mobile-ticket")["state"] == "missing_cookie"
