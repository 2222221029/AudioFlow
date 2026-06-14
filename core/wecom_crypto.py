#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import struct
import time
import uuid
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class WeComCrypto:
    """Minimal Enterprise WeChat callback crypto helper."""

    block_size = 32

    def __init__(self, token, encoding_aes_key, corp_id):
        self.token = str(token or "").strip()
        self.corp_id = str(corp_id or "").strip()
        key = str(encoding_aes_key or "").strip()
        if len(key) != 43:
            raise ValueError("EncodingAESKey 必须是 43 位")
        self.aes_key = base64.b64decode(key + "=")
        if len(self.aes_key) != 32:
            raise ValueError("EncodingAESKey 无效")

    def signature(self, timestamp, nonce, encrypt):
        values = [self.token, str(timestamp), str(nonce), str(encrypt)]
        values.sort()
        return hashlib.sha1("".join(values).encode("utf-8")).hexdigest()

    def verify_signature(self, msg_signature, timestamp, nonce, encrypt):
        expected = self.signature(timestamp, nonce, encrypt)
        if expected != str(msg_signature or ""):
            raise ValueError("企业微信回调签名验证失败")

    def decrypt(self, encrypt):
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
        plain = cipher.decrypt(base64.b64decode(encrypt))
        plain = self._unpad(plain)
        if len(plain) < 20:
            raise ValueError("企业微信回调密文无效")
        msg_len = struct.unpack("!I", plain[16:20])[0]
        msg = plain[20:20 + msg_len]
        receive_id = plain[20 + msg_len:].decode("utf-8", errors="ignore")
        if self.corp_id and receive_id and receive_id != self.corp_id:
            raise ValueError("企业微信回调 CorpID 不匹配")
        return msg.decode("utf-8")

    def encrypt(self, xml_text, nonce=None, timestamp=None):
        nonce = str(nonce or uuid.uuid4().hex[:10])
        timestamp = str(timestamp or int(time.time()))
        payload = (
            get_random_bytes(16)
            + struct.pack("!I", len(xml_text.encode("utf-8")))
            + xml_text.encode("utf-8")
            + self.corp_id.encode("utf-8")
        )
        encrypted = base64.b64encode(
            AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16]).encrypt(self._pad(payload))
        ).decode("utf-8")
        signature = self.signature(timestamp, nonce, encrypted)
        return (
            "<xml>"
            f"<Encrypt><![CDATA[{encrypted}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            "</xml>"
        )

    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        self.verify_signature(msg_signature, timestamp, nonce, echostr)
        return self.decrypt(echostr)

    def decrypt_message(self, msg_signature, timestamp, nonce, body):
        encrypt = parse_xml_text(body, "Encrypt")
        if not encrypt:
            raise ValueError("企业微信回调缺少 Encrypt")
        self.verify_signature(msg_signature, timestamp, nonce, encrypt)
        return self.decrypt(encrypt)

    def _pad(self, data):
        amount = self.block_size - (len(data) % self.block_size)
        return data + bytes([amount]) * amount

    def _unpad(self, data):
        amount = data[-1]
        if amount < 1 or amount > self.block_size:
            raise ValueError("企业微信回调填充无效")
        return data[:-amount]


def parse_xml_text(xml_text, key, default=""):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return default
    node = root.find(key)
    return node.text if node is not None and node.text is not None else default


def parse_wecom_message(xml_text):
    root = ET.fromstring(xml_text)
    return {child.tag: child.text or "" for child in root}
