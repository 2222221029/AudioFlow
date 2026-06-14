#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄小说 · 完整版（单文件，无项目内其它 .py 依赖）

流程: 自动设备注册(TTEncrypt) → Cookie → Gorgon/Argus/Ladon 签名
      → 搜索 → 阅读正文(CM) / 听书(真人+AI 音色) → 下载

依赖: pip install requests pycryptodome
听书 CENC: 需 ffmpeg（优先使用脚本上级目录 ffmpeg.exe）

用法:
  python core/vendor/fanqie_portable.py
  # 交互: 1书籍 2听书 3短剧 4漫画 5漫剧 → 搜索(分页拉全) → 选条目 → 下载
  python core/vendor/fanqie_portable.py -q 剑来 --kind book --book 1 --chapter 1 --plain
  python core/vendor/fanqie_portable.py -q 霸道 --kind playlet --book 1 --chapter 1 --download
  python core/vendor/fanqie_portable.py --kind audio -q 神级 --book 1 --chapter 1 --voice --download
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import gzip
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from enum import IntEnum, unique
from http.cookies import SimpleCookie
from pathlib import Path
from random import randint
from struct import unpack
from typing import Any, Literal
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests
from Crypto.Cipher import AES
from Crypto.Cipher.AES import new as aes_new, MODE_CBC
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from Crypto.Util.number import bytes_to_long, long_to_bytes
from requests.exceptions import ChunkedEncodingError, ConnectionError as ReqConnectionError

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
HOST = "https://api5-normal-sinfonlineb.fqnovel.com"
MGET_URL = "https://novelfm-hl.snssdk.com/novelfm/playerapi/full/mget/v1/"
REGISTER_URL = "https://i.snssdk.com/service/2/device_register/?tt_data=a"
AID = 1967
LICENSE_ID = 1611921764
SDK_VER_STR = "v04.06.01-ml-android"
SDK_VER_INT = 135004672

UA = (
    "com.dragon.read/69532 (Linux; U; Android 9; zh_CN; 23116PN5BC; "
    "Build/PQ3A.190705.10241111;tt-ok/3.12.13.20)"
)

ANDROID_VERSIONS = [("9", 28), ("10", 29), ("11", 31), ("12", 32), ("13", 33)]
DEVICE_MODELS = ["23116PN5BC", "RMX1931", "MI8", "P30"]
DEVICE_BRANDS = ["Xiaomi", "realme", "Huawei", "OPPO"]

# 搜索接口额外 query（与抓包模板一致，避免 PARAM_INVALID）
SEARCH_EXTRA: dict[str, str] = {
    "bookshelf_search_plan": "4",
    "live_room_id": "0",
    "from_rs": "false",
    "clicked_content": "page_search_button",
    "search_source_id": "clks###",
    "use_lynx": "false",
    "use_correct": "false",
    "last_search_page_interval": "92096",
    "product_id": "0",
    "line_words_num": "0",
    "last_consume_interval": "0",
    "pad_column_cover": "0",
    "is_first_enter_search": "false",
    "client_ab_info": "{}",
    "gender": "2",
    "cold_start_session_cnt_in_day": "1",
    "host_abi": "armeabi-v7a",
    "dragon_device_type": "pad",
    "sys_mini_window": "0",
    "app_mini_window": "0",
    "compliance_status": "0",
    "har_status": "0",
    "cold_start_session_cnt_in_life": "18",
    "charging": "1",
    "normal_session_cnt_in_life": "8496",
    "is_power_save_mode": "0",
    "app_dark_mode": "0",
    "screen_brightness": "102",
    "battery_pct": "100",
    "down_speed": "0",
    "sys_dark_mode": "0",
    "need_personal_recommend": "1",
    "player_so_load": "1",
    "font_scale": "100",
    "is_android_pad_screen": "0",
    "network_type": "4",
    "rom_version": "PQ3A.190705.10241111+release-keys",
    "current_volume": "100",
    "normal_session_cnt_in_day": "5",
}

# 搜索 tab_type：与 App 搜索页签一致（见 _probe_search_tabs.py）
SEARCH_KINDS: dict[str, dict[str, Any]] = {
    "book": {"tab_type": 3, "label": "书籍", "extract": "book"},
    "audio": {"tab_type": 2, "label": "听书", "extract": "book"},
    "playlet": {"tab_type": 11, "label": "短剧", "extract": "video"},
    "comic": {"tab_type": 8, "label": "漫画", "extract": "book"},
    "manju": {"tab_type": 19, "label": "漫剧", "extract": "video"},
}

SNSSDK_HOST = "https://reading.snssdk.com"

# ---------------------------------------------------------------------------
# TTEncrypt（设备注册体）
# ---------------------------------------------------------------------------
_TT_FIXED = base64.b64decode(
    "TdTC5rgxYgkOUrPHpnM7pByyRiuCmrWKGWs521cXdST0m69/COjWjSanLjfBqVovHwWlGJKu8pSXMrYqOKrdWA=="
)


def _tt_sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def tt_encrypt(data: bytes) -> bytes:
    rnd = os.urandom(32)
    hv = _tt_sha512(_tt_sha512(rnd) + _TT_FIXED)
    key, iv = hv[:16], hv[16:32]
    comp = gzip.compress(data)
    payload = _tt_sha512(comp) + comp
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(payload, AES.block_size))
    return bytes([116, 99, 5, 16, 0, 0]) + rnd + enc


# ---------------------------------------------------------------------------
# 签名算法（内嵌 algos 源码）
# ---------------------------------------------------------------------------
# --- pkcs7_padding.py ---
def pkcs7_padding_data_length(buffer, buffer_size, modulus):
    if buffer_size % modulus != 0 or buffer_size < modulus:
        return 0
    padding_value = buffer[buffer_size-1]
    if padding_value < 1 or padding_value > modulus:
        return 0
    if buffer_size < padding_value + 1:
        return 0
    count = 1
    buffer_size -= 1
    for i in range(count, padding_value):
        buffer_size -= 1
        if buffer[buffer_size] != padding_value:
            return 0
    return buffer_size

def pkcs7_padding_pad_buffer(buffer: bytearray, data_length: int, buffer_size: int, modulus: int) -> int:
    pad_byte = modulus - (data_length % modulus)
    if data_length + pad_byte > buffer_size:
        return -pad_byte
    for i in range(pad_byte):
        buffer[data_length+i] = pad_byte
    return pad_byte

def padding_size(size: int) -> int:
    mod = size % 16
    if mod > 0:
        return size + (16 - mod)
    return size
# --- Simon.py ---
from ctypes import c_ulonglong

def get_bit(val, pos):
    return 1 if val & (1 << pos) else 0

def rotate_left(v, n):
    r = (v << n) | (v >> (64 - n))
    return r & 0xffffffffffffffff

def rotate_right(v, n):
    r = (v << (64 - n)) | (v >> n) 
    return r & 0xffffffffffffffff

def key_expansion(key):
    tmp = 0
    for i in range(4, 72):
        tmp = rotate_right(key[i-1], 3)
        tmp = tmp ^ key[i-3]
        tmp = tmp ^ rotate_right(tmp, 1)
        key[i] = c_ulonglong(~key[i-4]).value ^ tmp ^ get_bit(0x3DC94C3A046D678B, (i - 4) % 62) ^ 3
    return key

def simon_dec(ct, k, c=0):
    tmp = 0
    f = 0
    key = [0] * 72

    key[0] = k[0]
    key[1] = k[1]
    key[2] = k[2]
    key[3] = k[3]

    key = key_expansion(key)

    x_i = ct[0]
    x_i1 = ct[1]

    for i in range(72-1, -1, -1):
        tmp = x_i
        f = rotate_left(x_i, 1) if c == 1 else rotate_left(x_i, 1) & rotate_left(x_i, 8)
        x_i = x_i1 ^ f ^ rotate_left(x_i, 2) ^ key[i]
        x_i1 = tmp

    pt = [x_i, x_i1]
    return pt

def simon_enc(pt, k, c=0):
    tmp = 0
    f = 0
    key = [0] * 72
    key[0] = k[0]
    key[1] = k[1]
    key[2] = k[2]
    key[3] = k[3]

    key = key_expansion(key)

    x_i = pt[0]
    x_i1 = pt[1]

    for i in range(72):
        tmp = x_i1
        f = rotate_left(x_i1, 1) if c == 1 else rotate_left(x_i1, 1) & rotate_left(x_i1, 8)
        x_i1 = x_i ^ f ^ rotate_left(x_i1, 2) ^ key[i]
        x_i = tmp

    ct = [x_i, x_i1]
    return ct


# --- Sm3.py ---
class SM3:
    def __init__(self) -> None:
        self.IV = [1937774191, 1226093241, 388252375, 3666478592, 2842636476, 372324522, 3817729613, 2969243214]
        self.TJ = [2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2043430169, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042, 2055708042]
    
    def __rotate_left(self, a: int, k: int) -> int:
        k = k % 32

        return ((a << k) & 0xFFFFFFFF) | ((a & 0xFFFFFFFF) >> (32 - k))

    def __FFJ(self, X: int, Y: int, Z: int, j: int) -> int:

        if 0 <= j and j < 16:
            ret = X ^ Y ^ Z
        elif 16 <= j and j < 64:
            ret = (X & Y) | (X & Z) | (Y & Z)

        return ret

    def __GGJ(self, X: int, Y: int, Z: int, j: int) -> int:

        if 0 <= j and j < 16:
            ret = X ^ Y ^ Z
        elif 16 <= j and j < 64:
            ret = (X & Y) | ((~X) & Z)

        return ret

    def __P_0(self, X: int) -> int:
        return X ^ (self.__rotate_left(X, 9)) ^ (self.__rotate_left(X, 17))

    def __P_1(self, X: int) -> int:
        Z = X ^ (self.__rotate_left(X, 15)) ^ (self.__rotate_left(X, 23))

        return Z

    def __CF(self, V_i: list, B_i: bytearray) -> list:

        W = []
        for i in range(16):
            weight = 0x1000000
            data = 0
            for k in range(i * 4, (i + 1) * 4):
                data = data + B_i[k] * weight
                weight = int(weight / 0x100)
            W.append(data)

        for j in range(16, 68):
            W.append(0)
            W[j] = (
                self.__P_1(W[j - 16] ^ W[j - 9] ^ (self.__rotate_left(W[j - 3], 15)))
                ^ (self.__rotate_left(W[j - 13], 7))
                ^ W[j - 6]
            )

        W_1 = []
        for j in range(0, 64):
            W_1.append(0)
            W_1[j] = W[j] ^ W[j + 4]

        A, B, C, D, E, F, G, H = V_i

        for j in range(0, 64):

            SS1 = self.__rotate_left(
                ((self.__rotate_left(A, 12)) + E + (self.__rotate_left(self.TJ[j], j)))
                & 0xFFFFFFFF,
                7,
            )

            SS2 = SS1 ^ (self.__rotate_left(A, 12))
            TT1 = (self.__FFJ(A, B, C, j) + D + SS2 + W_1[j]) & 0xFFFFFFFF
            TT2 = (self.__GGJ(E, F, G, j) + H + SS1 + W[j]) & 0xFFFFFFFF
            D = C
            C = self.__rotate_left(B, 9)
            B = A
            A = TT1
            H = G
            G = self.__rotate_left(F, 19)
            F = E
            E = self.__P_0(TT2)

        return [
            A & 0xFFFFFFFF ^ V_i[0],
            B & 0xFFFFFFFF ^ V_i[1],
            C & 0xFFFFFFFF ^ V_i[2],
            D & 0xFFFFFFFF ^ V_i[3],
            E & 0xFFFFFFFF ^ V_i[4],
            F & 0xFFFFFFFF ^ V_i[5],
            G & 0xFFFFFFFF ^ V_i[6],
            H & 0xFFFFFFFF ^ V_i[7],
        ]

    def sm3_hash(self, msg: bytes) -> bytes:
        msg = bytearray(msg)
        len1 = len(msg)
        reserve1 = len1 % 64
        msg.append(0x80)
        reserve1 = reserve1 + 1
        # 56-64, add 64 byte
        range_end = 56
        if reserve1 > range_end:
            range_end += 64

        for i in range(reserve1, range_end):
            msg.append(0x00)

        bit_length = (len1) * 8
        bit_length_str = [bit_length % 0x100]
        for i in range(7):
            bit_length = int(bit_length / 0x100)
            bit_length_str.append(bit_length % 0x100)
        for i in range(8):
            msg.append(bit_length_str[7 - i])

        group_count = round(len(msg) / 64)

        B = []
        for i in range(0, group_count):
            B.append(msg[i * 64 : (i + 1) * 64])

        V = []
        V.append(self.IV)
        for i in range(0, group_count):
            V.append(self.__CF(V[i], B[i]))

        y = V[i + 1]
        res = b""

        for i in y:
            res += int(i).to_bytes(4, "big")

        return res
# --- protobuf.py ---
from enum import IntEnum, unique

class ProtoError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return repr(self.msg)


@unique
class ProtoFieldType(IntEnum):
    VARINT = 0
    INT64 = 1
    STRING = 2
    GROUPSTART = 3
    GROUPEND = 4
    INT32 = 5
    ERROR1 = 6
    ERROR2 = 7


class ProtoField:
    def __init__(self, idx, type, val):
        self.idx = idx
        self.type = type
        self.val = val

    def isAsciiStr(self):
        if (type(self.val) != bytes):
            return False

        for b in self.val:
            if b < 0x20 or b > 0x7e:
                return False
        return True

    def __str__(self):
        if ((self.type == ProtoFieldType.INT32) or
            (self.type == ProtoFieldType.INT64) or
                (self.type == ProtoFieldType.VARINT)):
            return '%d(%s): %d' % (self.idx, self.type.name, self.val)
        elif self.type == ProtoFieldType.STRING:
            if self.isAsciiStr():  # self.val.isalnum()
                return '%d(%s): "%s"' % (self.idx, self.type.name, self.val.decode('ascii'))
            else:
                return '%d(%s): h"%s"' % (self.idx, self.type.name, self.val.hex())
        elif ((self.type == ProtoFieldType.GROUPSTART) or (self.type == ProtoFieldType.GROUPEND)):
            return '%d(%s): %s' % (self.idx, self.type.name, self.val)
        else:
            return '%d(%s): %s' % (self.idx, self.type.name, self.val)


class ProtoReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def seek(self, pos):
        self.pos = pos

    def isRemain(self, length):
        return self.pos + length <= len(self.data)

    def read0(self):
        assert (self.isRemain(1))
        ret = self.data[self.pos]
        self.pos += 1
        return ret & 0xFF

    def read(self, length):
        assert (self.isRemain(length))
        ret = self.data[self.pos:self.pos+length]
        self.pos += length
        return ret

    def readInt32(self):
        return int.from_bytes(self.read(4), byteorder='little', signed=False)

    def readInt64(self):
        return int.from_bytes(self.read(8), byteorder='little', signed=False)

    def readVarint(self):
        vint = 0
        n = 0
        while True:
            byte = self.read0()
            vint |= ((byte & 0x7F) << (7 * n))
            if byte < 0x80:
                break
            n += 1

        return vint

    def readString(self):
        len = self.readVarint()
        return self.read(len)


class ProtoWriter:
    def __init__(self):
        self.data = bytearray()

    def write0(self, byte):
        self.data.append(byte & 0xFF)

    def write(self, bytes):
        self.data.extend(bytes)

    def writeInt32(self, int32):
        bs = int32.to_bytes(4, byteorder='little', signed=False)
        self.write(bs)

    def writeInt64(self, int64):
        bs = int64.to_bytes(8, byteorder='little', signed=False)
        self.write(bs)

    def writeVarint(self, vint):
        vint = vint & 0xFFFFFFFF
        while (vint > 0x80):
            self.write0((vint & 0x7F) | 0x80)
            vint >>= 7
        self.write0(vint & 0x7F)

    def writeString(self, bytes):
        self.writeVarint(len(bytes))
        self.write(bytes)

    def toBytes(self):
        return bytes(self.data)


class ProtoBuf:
    def __init__(self, data=None):
        self.fields = []
        if (data != None):
            if (type(data) != bytes and type(data) != dict):
                raise ProtoError(
                    'unsupport type(%s) to protobuf' % (type(data)))

            if (type(data) == bytes) and (len(data) > 0):
                self.__parseBuf(data)
            elif (type(data) == dict) and (len(data) > 0):
                self.__parseDict(data)

    def __getitem__(self, idx):
        pf = self.get(int(idx))
        if (pf == None):
            return None
        if (pf.type != ProtoFieldType.STRING):
            return pf.val
        if (type(idx) != int):
            return pf.val
        if (pf.val == None):
            return None
        if (pf.isAsciiStr()):
            return pf.val.decode('utf-8')
        return ProtoBuf(pf.val)

    def __parseBuf(self, bytes):
        reader = ProtoReader(bytes)
        while reader.isRemain(1):
            key = reader.readVarint()
            field_type = ProtoFieldType(key & 0x7)
            field_idx = key >> 3
            if (field_idx == 0):
                break
            if (field_type == ProtoFieldType.INT32):
                self.put(ProtoField(field_idx, field_type, reader.readInt32()))
            elif (field_type == ProtoFieldType.INT64):
                self.put(ProtoField(field_idx, field_type, reader.readInt64()))
            elif (field_type == ProtoFieldType.VARINT):
                self.put(ProtoField(field_idx, field_type, reader.readVarint()))
            elif (field_type == ProtoFieldType.STRING):
                self.put(ProtoField(field_idx, field_type, reader.readString()))
            else:
                raise ProtoError(
                    'parse protobuf error, unexpected field type: %s' % (field_type.name))

    def toBuf(self):
        writer = ProtoWriter()
        for field in self.fields:
            key = (field.idx << 3) | (field.type & 7)
            writer.writeVarint(key)
            if field.type == ProtoFieldType.INT32:
                writer.writeInt32(field.val)
            elif field.type == ProtoFieldType.INT64:
                writer.writeInt64(field.val)
            elif field.type == ProtoFieldType.VARINT:
                writer.writeVarint(field.val)
            elif field.type == ProtoFieldType.STRING:
                writer.writeString(field.val)
            else:
                raise ProtoError(
                    'encode to protobuf error, unexpected field type: %s' % (field.type.name))
        return writer.toBytes()

    def dump(self):
        for field in self.fields:
            print(field)

    def getList(self, idx):
        return [field for field in self.fields if field.idx == idx]

    def get(self, idx):
        for field in self.fields:
            if field.idx == idx:
                return field
        return None

    def getInt(self, idx):
        pf = self.get(idx)
        if (pf == None):
            return 0
        if ((pf.type == ProtoFieldType.INT32) or (pf.type == ProtoFieldType.INT64) or (pf.type == ProtoFieldType.VARINT)):
            return pf.val
        raise ProtoError("getInt(%d) -> %s" % (idx, pf.type))

    def getBytes(self, idx):
        pf = self.get(idx)
        if (pf == None):
            return None
        if (pf.type == ProtoFieldType.STRING):
            return pf.val
        raise ProtoError("getBytes(%d) -> %s" % (idx, pf.type))

    def getUtf8(self, idx):
        bs = self.getBytes(idx)
        if (bs == None):
            return None
        return bs.decode('utf-8')

    def getProtoBuf(self, idx):
        bs = self.getBytes(idx)
        if (bs == None):
            return None
        return ProtoBuf(bs)

    def put(self, field: ProtoField):
        self.fields.append(field)

    def putInt32(self, idx, int32):
        self.put(ProtoField(idx, ProtoFieldType.INT32, int32))

    def putInt64(self, idx, int64):
        self.put(ProtoField(idx, ProtoFieldType.INT64, int64))

    def putVarint(self, idx, vint):
        self.put(ProtoField(idx, ProtoFieldType.VARINT, vint))

    def putBytes(self, idx, data):
        self.put(ProtoField(idx, ProtoFieldType.STRING, data))

    def putUtf8(self, idx, data):
        self.put(ProtoField(idx, ProtoFieldType.STRING, data.encode('utf-8')))

    def putProtoBuf(self, idx, data):
        self.put(ProtoField(idx, ProtoFieldType.STRING, data.toBuf()))

    def __parseDict(self, data):
        for k, v in data.items():
            if (isinstance(v, int)):
                self.putVarint(k, v)
            elif (isinstance(v, str)):
                self.putUtf8(k, v)
            elif (isinstance(v, bytes)):
                self.putBytes(k, v)
            elif (isinstance(v, dict)):
                self.putProtoBuf(k, ProtoBuf(v))
            else:
                raise ProtoError('unsupport type(%s) to protobuf' % (type(v)))

    def toDict(self, out):
        for k, v in out.items():
            if (isinstance(v, int)):
                out[k] = self.getInt(k)
            elif (isinstance(v, str)):
                out[k] = self.getUtf8(k)
            elif (isinstance(v, bytes)):
                out[k] = self.getBytes(k)
            elif (isinstance(v, dict)):
                out[k] = self.getProtoBuf(k).toDict(v)
            else:
                raise ProtoError('unsupport type(%s) to protobuf' % (type(v)))
        return out

# --- Gorgon.py ---
import hashlib
import json
import time
class Gorgon:
    def __init__(self, params: str, unix: int, x_ss_stub: str = None, cookies: str = None) -> None:
        self.unix = unix
        self.params = params
        self.x_ss_stub = x_ss_stub if x_ss_stub else "0" * 32
        self.cookies = cookies

    def hash(self, data: str) -> str:
        return hashlib.md5(data.encode()).hexdigest()

    def get_base_string(self) -> str:
        base_str = self.hash(self.params)
        base_str = (
            base_str + self.x_ss_stub
        )
        base_str = (
            base_str + self.hash(self.cookies)
            if self.cookies
            else base_str + str("0" * 32)
        )
        return base_str

    def get_value(self) -> json:
        return self.encrypt(self.get_base_string())

    def encrypt(self, data: str) -> json:
        len = 0x14
        key = [
            0xDF,
            0x77,
            0xB9,
            0x40,
            0xB9,
            0x9B,
            0x84,
            0x83,
            0xD1,
            0xB9,
            0xCB,
            0xD1,
            0xF7,
            0xC2,
            0xB9,
            0x85,
            0xC3,
            0xD0,
            0xFB,
            0xC3,
        ]
        param_list = []
        for i in range(0, 12, 4):
            temp = data[8 * i : 8 * (i + 1)]
            for j in range(4):
                H = int(temp[j * 2 : (j + 1) * 2], 16)
                param_list.append(H)
        param_list.extend([0x0, 0x6, 0xB, 0x1C])
        H = int(hex(int(self.unix)), 16)
        param_list.append((H & 0xFF000000) >> 24)
        param_list.append((H & 0x00FF0000) >> 16)
        param_list.append((H & 0x0000FF00) >> 8)
        param_list.append((H & 0x000000FF) >> 0)
        eor_result_list = []
        for A, B in zip(param_list, key):
            eor_result_list.append(A ^ B)
        for i in range(len):
            C = self.reverse(eor_result_list[i])
            D = eor_result_list[(i + 1) % len]
            E = C ^ D
            F = self.rbit_algorithm(E)
            H = ((F ^ 0xFFFFFFFF) ^ len) & 0xFF
            eor_result_list[i] = H
        result = ""
        
        for param in eor_result_list:
            result += self.hex_string(param)
            
        return {
            "x-ss-req-ticket": str(int(self.unix * 1000)),
            "x-khronos"      : str(int(self.unix)),
            "x-gorgon"       : f"0404b0d30000{result}"
        }

    def rbit_algorithm(self, num):
        result = ""
        tmp_string = bin(num)[2:]
        while len(tmp_string) < 8:
            tmp_string = "0" + tmp_string
        for i in range(0, 8):
            result = result + tmp_string[7 - i]
        return int(result, 2)

    def hex_string(self, num):
        tmp_string = hex(num)[2:]
        if len(tmp_string) < 2:
            tmp_string = "0" + tmp_string
        return tmp_string

    def reverse(self, num):
        tmp_string = self.hex_string(num)
        return int(tmp_string[1:] + tmp_string[:1], 16)
# --- Ladon.py ---
# pkcs7 import pkcs7_padding_pad_buffer, padding_size
import base64
import hashlib
import ctypes
from os import urandom
def md5bytes(data: bytes) -> str:
    m = hashlib.md5()
    m.update(data)
    return m.hexdigest()


def get_type_data(ptr, index, data_type):
    if data_type == "uint64_t":
        return int.from_bytes(ptr[index * 8 : (index + 1) * 8], "little")
    else:
        raise ValueError("Invalid data type")


def set_type_data(ptr, index, data, data_type):
    if data_type == "uint64_t":
        ptr[index * 8 : (index + 1) * 8] = data.to_bytes(8, "little")
    else:
        raise ValueError("Invalid data type")


def validate(num):
    return num & 0xFFFFFFFFFFFFFFFF


def __ROR__(value: ctypes.c_ulonglong, count: int) -> ctypes.c_ulonglong:
    nbits = ctypes.sizeof(value) * 8
    count %= nbits
    low = ctypes.c_ulonglong(value.value << (nbits - count)).value
    value = ctypes.c_ulonglong(value.value >> count).value
    value = value | low
    return value


def encrypt_ladon_input(hash_table, input_data):
    data0 = int.from_bytes(input_data[:8], byteorder="little")
    data1 = int.from_bytes(input_data[8:], byteorder="little")

    for i in range(0x22):
        hash = int.from_bytes(hash_table[i * 8 : (i + 1) * 8], byteorder="little")
        data1 = validate(hash ^ (data0 + ((data1 >> 8) | (data1 << (64 - 8)))))
        data0 = validate(data1 ^ ((data0 >> 0x3D) | (data0 << (64 - 0x3D))))

    output_data = bytearray(26)
    output_data[:8] = data0.to_bytes(8, byteorder="little")
    output_data[8:] = data1.to_bytes(8, byteorder="little")

    return bytes(output_data)


def encrypt_ladon(md5hex: bytes, data: bytes, size: int):
    hash_table = bytearray(272 + 16)
    hash_table[:32] = md5hex

    temp = []
    for i in range(4):
        temp.append(int.from_bytes(hash_table[i * 8 : (i + 1) * 8], byteorder="little"))

    buffer_b0 = temp[0]
    buffer_b8 = temp[1]
    temp.pop(0)
    temp.pop(0)

    for i in range(0, 0x22):
        x9 = buffer_b0
        x8 = buffer_b8
        x8 = validate(__ROR__(ctypes.c_ulonglong(x8), 8))
        x8 = validate(x8 + x9)
        x8 = validate(x8 ^ i)
        temp.append(x8)
        x8 = validate(x8 ^ __ROR__(ctypes.c_ulonglong(x9), 61))
        set_type_data(hash_table, i + 1, x8, "uint64_t")
        buffer_b0 = x8
        buffer_b8 = temp[0]
        temp.pop(0)

    new_size = padding_size(size)

    input = bytearray(new_size)
    input[:size] = data
    pkcs7_padding_pad_buffer(input, size, new_size, 16)

    output = bytearray(new_size)
    for i in range(new_size // 16):
        output[i * 16 : (i + 1) * 16] = encrypt_ladon_input(
            hash_table, input[i * 16 : (i + 1) * 16]
        )

    return output


def ladon_encrypt(
    khronos      : int,
    lc_id        : int   = 1611921764,
    aid          : int   = 1233,
    random_bytes : bytes = urandom(4)) -> str:
    
    data       = f"{khronos}-{lc_id}-{aid}"

    keygen     = random_bytes + str(aid).encode()
    md5hex     = md5bytes(keygen)

    size       = len(data)
    new_size   = padding_size(size)

    output     = bytearray(new_size + 4)
    output[:4] = random_bytes

    output[4:] = encrypt_ladon(md5hex.encode(), data.encode(), size)

    return base64.b64encode(bytes(output)).decode()


class Ladon:
    @staticmethod
    def encrypt(x_khronos: int, lc_id: str, aid: int) -> str:
        return ladon_encrypt(x_khronos, lc_id, aid)


# --- Argus.py ---
from random import randint
from time import time
from struct import unpack
from base64 import b64encode
from hashlib import md5
from urllib.parse import parse_qs

from Crypto.Util.Padding import pad
# sm3 import SM3
# simon import simon_enc
# pb import ProtoBuf



class Argus:
    def encrypt_enc_pb(data, l):
        data = list(data)
        xor_array = data[:8]

        for i in range(8, l):
            data[i] ^= xor_array[i % 8]

        return bytes(data[::-1])

    @staticmethod
    def get_bodyhash(stub: (str | None) = None) -> bytes:
        return (
            SM3().sm3_hash(bytes(16))[0:6]
            if stub == None or len(stub) == 0
            else SM3().sm3_hash(bytes.fromhex(stub))[0:6]
        )

    @staticmethod
    def get_queryhash(query: str) -> bytes:
        return (
            SM3().sm3_hash(bytes(16))[0:6]
            if query == None or len(query) == 0
            else SM3().sm3_hash(query.encode())[0:6]
        )

    @staticmethod
    def encrypt(xargus_bean: dict):
        protobuf = pad(bytes.fromhex(ProtoBuf(xargus_bean).toBuf().hex()), AES.block_size)
        new_len = len(protobuf)
        sign_key = b"\xac\x1a\xda\xae\x95\xa7\xaf\x94\xa5\x11J\xb3\xb3\xa9}\xd8\x00P\xaa\n91L@R\x8c\xae\xc9RV\xc2\x8c"
        sm3_output = b"\xfcx\xe0\xa9ez\x0ct\x8c\xe5\x15Y\x90<\xcf\x03Q\x0eQ\xd3\xcf\xf22\xd7\x13C\xe8\x8a2\x1cS\x04"  # sm3_hash(sign_key + b'\xf2\x81ao' + sign_key)

        key = sm3_output[:32]
        key_list = []
        enc_pb = bytearray(new_len)

        for _ in range(2):
            key_list = key_list + list(unpack("<QQ", key[_ * 16 : _ * 16 + 16]))

        for _ in range(int(new_len / 16)):
            pt = list(unpack("<QQ", protobuf[_ * 16 : _ * 16 + 16]))
            ct = simon_enc(pt, key_list)
            enc_pb[_ * 16 : _ * 16 + 8] = ct[0].to_bytes(8, byteorder="little")
            enc_pb[_ * 16 + 8 : _ * 16 + 16] = ct[1].to_bytes(8, byteorder="little")

        b_buffer = Argus.encrypt_enc_pb(
            (b"\xf2\xf7\xfc\xff\xf2\xf7\xfc\xff" + enc_pb), new_len + 8
        )
        b_buffer = b"\xa6n\xad\x9fw\x01\xd0\x0c\x18" + b_buffer + b"ao"

        cipher = aes_new(md5(sign_key[:16]).digest(), MODE_CBC, md5(sign_key[16:]).digest())

        return b64encode(
            b"\xf2\x81" + cipher.encrypt(pad(b_buffer, AES.block_size))
        ).decode()

    @staticmethod
    def get_sign(
        queryhash: (None | str) = None,
        data: (None | str) = None,
        timestamp: int = int(time()),
        aid: int = 1233,
        license_id: int = 1611921764,
        platform: int = 0,
        sec_device_id: str = "",
        sdk_version: str = "v04.04.05-ov-android",
        sdk_version_int: int = 134744640,
    ) -> dict:
        params_dict = parse_qs(queryhash)

        return Argus.encrypt(
            {
                1: 0x20200929 << 1,  # magic
                2: 2,  # version
                3: randint(0, 0x7FFFFFFF),  # rand
                4: str(aid),  # msAppID
                5: params_dict["device_id"][0],  # deviceID
                6: str(license_id),  # licenseID
                7: params_dict["version_name"][0],  # appVersion
                8: sdk_version,  # sdkVersionStr
                9: sdk_version_int,  # sdkVersion
                10: bytes(8),  # envcode -> jailbreak Detection
                11: platform,  # platform (ios = 1)
                12: timestamp << 1,  # createTime
                13: Argus.get_bodyhash(data),  # bodyHash
                14: Argus.get_queryhash(queryhash),  # queryHash
                15: {
                    1: 1,  # signCount
                    2: 1,  # reportCount
                    3: 1,  # settingCount
                    7: 3348294860,
                },
                16: sec_device_id,  # secDeviceToken
                # 17: timestamp,                     # isAppLicense
                20: "none",  # pskVersion
                21: 738,  # callType
                23: {1: "NX551J", 2: 8196, 4: 2162219008},
                25: 2,
            }
        )


def sign4(
    params,
    x_ss_stub: str | None = None,
    sec_device_id: str = "",
    cookie: str | None = None,
    aid: int = AID,
    license_id: int = LICENSE_ID,
    sdk_version_str: str = SDK_VER_STR,
    sdk_version: int = SDK_VER_INT,
    platform: int = 0,
    unix: int | None = None,
) -> dict:
    if not unix:
        unix = int(time.time())
    return Gorgon(params, unix, x_ss_stub, cookie).get_value() | {
        "x-ladon": Ladon.encrypt(unix, license_id, aid),
        "x-argus": Argus.get_sign(
            params,
            x_ss_stub,
            unix,
            platform=platform,
            aid=aid,
            license_id=license_id,
            sec_device_id=sec_device_id,
            sdk_version=sdk_version_str,
            sdk_version_int=sdk_version,
        ),
    }


def _aid_from_query(query: str) -> int:
    m = re.search(r"(?:^|&)aid=(\d+)", query)
    return int(m.group(1)) if m else AID


def sign_headers(query, *, cookie=None, body=None, unix=None, aid: int | None = None):
    from hashlib import md5
    import time as _t
    unix = unix or int(_t.time())
    stub = md5(body).hexdigest() if body else None
    use_aid = aid if aid is not None else _aid_from_query(query)
    return sign4(query, stub, "", cookie or "", use_aid, LICENSE_ID, SDK_VER_STR, SDK_VER_INT, 0, unix)


import time  # Argus 内 from time import time 会遮蔽，此处恢复

# ---------------------------------------------------------------------------
# CM 正文解密
# ---------------------------------------------------------------------------
class CM:
    def __init__(self):
        self.big_c = 2410312426921032588552076022197566074856950548502459942654116941958108831682612228890093858261341614673227141477904012196503648957050582631942730706805009223062734745341073406696246014589361659774041027169249453200378729434170325843778659198143763193776859869524088940195577346119843545301547043747207749969763750084308926339295559968882457872412993810129130294592999947926365264059284647209730384947211681434464714438488520940127459844288859336526896320919633919
        self.big_b = 2
        self.arry_d = base64.b64decode("rCXGfd2POMGzeiNIgo4iLg==")
        self.thisd = None

    def client_handshake(self) -> str:
        x = bytes_to_long(get_random_bytes(32)) % (self.big_c - 1)
        y = pow(self.big_b, x, self.big_c)
        self.thisd = (x, y)
        iv = get_random_bytes(16)
        pub = long_to_bytes(y)
        enc = AES.new(self.arry_d, AES.MODE_CBC, iv).encrypt(pad(pub, AES.block_size))
        return base64.b64encode(iv + enc).decode()

    def decrypt(self, mode: int, server_key_b64: str, content_b64: str) -> str:
        if mode != 1 or not server_key_b64:
            return content_b64
        raw = base64.b64decode(content_b64)
        iv, data = raw[:16], raw[16:]
        peer = base64.b64decode(server_key_b64)
        x, _ = self.thisd
        shared = pow(bytes_to_long(peer), x, self.big_c)
        key = long_to_bytes(shared)[:32]
        plain = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(data), AES.block_size)
        return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# 设备注册
# ---------------------------------------------------------------------------
def _gen_register_body() -> dict:
    os_ver, os_api = random.choice(ANDROID_VERSIONS)
    brand = random.choice(DEVICE_BRANDS)
    now = int(time.time() * 1000)
    rom = "".join(random.choices(string.ascii_uppercase + string.digits, k=14))
    return {
        "magic_tag": "ss_app_log",
        "header": {
            "display_name": "番茄免费小说",
            "aid": AID,
            "channel": "43536163a",
            "package": "com.dragon.read",
            "sdk_version": "3.7.0-rc.25-fanqie-xiaoshuo",
            "sdk_target_version": 29,
            "git_hash": "711d1a7",
            "density_dpi": 480,
            "display_density": "mdpi",
            "resolution": "1080*1920",
            "language": "zh",
            "timezone": 8,
            "access": "wifi",
            "not_request_sender": 0,
            "carrier": "CHINA MOBILE",
            "mcc_mnc": "46000",
            "region": "CN",
            "tz_name": "Asia/Shanghai",
            "tz_offset": 28800,
            "sim_region": "cn",
            "device_platform": "android",
            "custom": {"host_bit": 32, "dragon_device_type": "pad"},
            "sdk_flavor": "china",
            "guest_mode": 0,
            "os": "Android",
            "os_version": os_ver,
            "os_api": os_api,
            "device_model": random.choice(DEVICE_MODELS),
            "device_brand": brand,
            "device_manufacturer": brand,
            "cpu_abi": "arm64-v8a",
            "sig_hash": "a4a27c2633195374c15651ffc3c4a497",
            "cdid": str(uuid.uuid4()),
            "openudid": f"{random.getrandbits(80):020x}",
            "clientudid": str(uuid.uuid4()),
            "req_id": str(uuid.uuid4()),
            "rom": rom,
            "rom_version": f"PQ3A.190705.10241111+release-keys",
        },
        "_gen_time": now,
    }


def register_device() -> tuple[str, str, dict[str, str], str, str]:
    """返回 device_id, install_id, cookies, device_token, cdid"""
    body = _gen_register_body()
    cdid = body["header"]["cdid"]
    enc = tt_encrypt(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    resp = requests.post(
        REGISTER_URL,
        data=enc,
        headers={"user-agent": "okhttp/4.10.0", "content-type": "application/octet-stream; tt-data=a"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("device_id"):
        raise RuntimeError(f"设备注册失败: {data}")
    cookies: dict[str, str] = {}
    raw = resp.headers.get("Set-Cookie")
    if raw:
        sc = SimpleCookie()
        sc.load(raw)
        for k, m in sc.items():
            cookies[k] = m.value
    for c in resp.cookies:
        cookies[c.name] = c.value
    did = str(data.get("device_id_str") or data["device_id"])
    iid = str(data.get("install_id_str") or data["install_id"])
    return did, iid, cookies, str(data.get("device_token") or ""), cdid


def cookies_to_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def load_capture_identity() -> tuple[str, str, dict[str, str], str] | None:
    """从上级「参数」抓包读取 device_id / iid / Cookie（文件可选）。"""
    cap = WORKSPACE_ROOT / "参数"
    if not cap.is_file():
        return None
    lines = cap.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].strip().startswith("http"):
        return None
    qs = dict(parse_qsl(urlparse(lines[0].strip()).query, keep_blank_values=True))
    did, iid = qs.get("device_id"), qs.get("iid")
    if not did or not iid:
        return None
    cookies: dict[str, str] = {}
    for line in lines[2:]:
        if line.lower().startswith("cookie:"):
            for part in line.split(":", 1)[1].strip().split(";"):
                part = part.strip()
                if "=" in part:
                    k, _, v = part.partition("=")
                    cookies[k.strip()] = v.strip()
            break
    cdid = qs.get("cdid") or str(uuid.uuid4())
    return str(did), str(iid), cookies, cdid


def merge_response_cookies(cookies: dict[str, str], response: requests.Response) -> dict[str, str]:
    """合并响应 Set-Cookie（搜索等 fqnovel 接口会下发 odin_tt）。"""
    out = dict(cookies)
    raw = response.headers.get("Set-Cookie")
    if raw:
        sc = SimpleCookie()
        sc.load(raw)
        for k, m in sc.items():
            out[k] = m.value
    for c in response.cookies:
        out[c.name] = c.value
    return out


DEVICE_IDENTITY_PATH = (
    Path(os.environ.get("CONFIG_DIR", "")).expanduser() / "fanqie_tingshu_device.json"
    if os.environ.get("CONFIG_DIR")
    else Path.home() / ".audioflow" / "fanqie_tingshu_device.json"
)


def save_device_identity(client: "FanqieClient") -> None:
    """保存设备信息与 Cookie（覆盖旧文件）。"""
    try:
        DEVICE_IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "device_id": client.device_id,
            "install_id": client.install_id,
            "device_token": client.device_token,
            "cdid": client.cdid,
            "cookies": dict(client.cookies),
            "normal_session_id": client.common.get("normal_session_id", ""),
            "cold_start_session_id": client.common.get("cold_start_session_id", ""),
        }
        tmp = DEVICE_IDENTITY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DEVICE_IDENTITY_PATH)
    except Exception as e:
        print(f"  ⚠️ 保存番茄听书设备身份失败: {e}")


def load_saved_device_identity() -> tuple[str, str, dict[str, str], str, str, dict[str, str]] | None:
    """读取已保存的设备身份；无效则返回 None。"""
    if not DEVICE_IDENTITY_PATH.is_file():
        return None
    try:
        data = json.loads(DEVICE_IDENTITY_PATH.read_text(encoding="utf-8"))
        did = str(data.get("device_id") or "").strip()
        iid = str(data.get("install_id") or "").strip()
        if not did or not iid:
            return None
        cookies = data.get("cookies") or {}
        if not isinstance(cookies, dict):
            cookies = {}
        session = {
            "normal_session_id": str(data.get("normal_session_id") or ""),
            "cold_start_session_id": str(data.get("cold_start_session_id") or ""),
        }
        return (
            did,
            iid,
            {str(k): str(v) for k, v in cookies.items()},
            str(data.get("device_token") or ""),
            str(data.get("cdid") or str(uuid.uuid4())),
            session,
        )
    except Exception as e:
        print(f"  ⚠️ 读取番茄听书设备身份失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 听书 CENC 解密与下载
# ---------------------------------------------------------------------------
def find_ffmpeg() -> str | None:
    candidates: list[Path] = [
        WORKSPACE_ROOT / "ffmpeg.exe",
        SCRIPT_DIR / "ffmpeg.exe",
        SCRIPT_DIR / "ffmpeg-8.0-essentials_build" / "bin" / "ffmpeg.exe",
        WORKSPACE_ROOT / "ffmpeg-8.0-essentials_build" / "bin" / "ffmpeg.exe",
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        candidates.extend([
            root / "ffmpeg-8.0-essentials_build" / "bin" / "ffmpeg.exe",
            root / "ffmpeg.exe",
        ])
    try:
        from core.app_paths import ffmpeg_exe_path
        candidates.insert(0, ffmpeg_exe_path())
    except Exception:
        pass
    for p in candidates:
        if p.is_file():
            return str(p)
    return shutil.which("ffmpeg")


def _popcount8(x: int) -> int:
    return bin(x & 0xFF).count("1")


def _swap_byte_pairs(buf: bytearray) -> None:
    n = len(buf)
    if n <= 1:
        return
    limit = n - 2
    i = 0
    while i < n:
        if i == limit:
            break
        buf[i], buf[i + 1] = buf[i + 1], buf[i]
        i += 2


def _spade_transform(body: bytearray, drm_flag: int = 0) -> None:
    v21, v22 = 85, -6
    for i in range(len(body)):
        pc = _popcount8(i)
        v24 = v21 if (i & 1) else v22
        if i & 1:
            v21 = body[i]
        else:
            v22 = body[i]
        v25 = v24 ^ body[i]
        v26 = (-21 - pc) if not drm_flag else (pc + 21)
        body[i] = (v26 + v25) & 0xFF


def _spade_transform_v2(body: bytearray, drm_flag: int = 0) -> None:
    v32, v33 = 85, -6
    for i in range(len(body)):
        pc = _popcount8(i)
        v35 = v32 if (i & 1) else v33
        v36 = v33 if (i & 1) else v32
        if i & 1:
            v32 = body[i]
        else:
            v33 = body[i]
        v37 = v35 ^ body[i]
        v38 = (-21 - pc) if not drm_flag else (pc + 21)
        body[i] = (v37 - v36 + v38) & 0xFF


def derive_cenc_key_hex(spade_b64: str, *, drm_flag: int = 0) -> str:
    """从 video_model.spade_a 派生 CENC 密钥（与 libttmplayer 一致）。"""
    raw = base64.b64decode(spade_b64 + "===")
    a2 = len(raw)
    if a2 < 4:
        raise ValueError("spade_a 过短")
    v6 = raw[1] ^ raw[0] ^ raw[2]
    v7, v8 = v6 - 48, a2 - v6 + 47
    if v6 - 48 < 1 or v8 < 1:
        raise ValueError("spade_a 头部长度非法")
    v17 = raw[a2 - v7 - 1] ^ raw[a2 - v7 - 2]
    suffix = bytes(v17 ^ raw[a2 - v7 + i] for i in range(v7))
    body = bytearray(raw[1 : 1 + v8])
    if suffix in (b"app_v2", b"web_v2"):
        _swap_byte_pairs(body)
        _spade_transform_v2(body, drm_flag)
        _swap_byte_pairs(body)
    else:
        _spade_transform(body, drm_flag)
    v43 = body[0]
    if 0 <= v43 - 48 < 10:
        v44 = v43 - 48
    elif 0 <= v43 - 97 < 26:
        v44 = v43 - 87
    else:
        raise ValueError("spade_a 变换后首字节非法")
    if len(body) - v44 < 2:
        raise ValueError("spade_a 密钥段过短")
    key = bytearray()
    stop = a2 - v6 - v44 + 46
    for i in range(len(body) - v44 - 1):
        key.append(body[i + 1])
        if stop == i + 1:
            break
    return bytes(key).decode("ascii")


def spade_and_kid_from_play(play: dict) -> tuple[str, str]:
    spade, kid = "", ""
    vm = play.get("video_model") or ""
    if isinstance(vm, dict):
        obj = vm
    elif isinstance(vm, str) and vm.strip():
        try:
            obj = json.loads(vm)
        except json.JSONDecodeError:
            obj = None
    else:
        obj = None
    if isinstance(obj, dict):
        vl = obj.get("video_list") or []
        entries = vl.values() if isinstance(vl, dict) else (vl if isinstance(vl, list) else ())
        for entry in entries:
            if isinstance(entry, dict):
                ei = entry.get("encrypt_info") or {}
                spade = spade or (ei.get("spade_a") or entry.get("spade_a") or "")
                kid = kid or (ei.get("kid") or entry.get("kid") or "")
    if not spade:
        spade = play.get("encryption_key") or ""
    return spade, kid


def file_has_cenc(data: bytes) -> bool:
    return b"tenc" in data or b"senc" in data


def spade_from_play(play: dict) -> str:
    return spade_and_kid_from_play(play)[0]


def cenc_key_hex(play: dict, *, drm_flag: int = 0) -> str:
    spade, _ = spade_and_kid_from_play(play)
    if spade:
        try:
            return derive_cenc_key_hex(spade, drm_flag=drm_flag)
        except ValueError:
            pass
    raw = base64.b64decode((play.get("encryption_key") or "") + "===")
    if len(raw) < 16:
        raise ValueError("encryption_key 长度不足")
    return raw[-16:].hex()


def find_mp4decrypt() -> str | None:
    env = os.environ.get("MP4DECRYPT_PATH", "").strip()
    if env and Path(env).is_file():
        return str(Path(env).resolve())
    for p in (
        SCRIPT_DIR / "tools_mp4decrypt.exe",
        WORKSPACE_ROOT / "tools_mp4decrypt.exe",
        WORKSPACE_ROOT / "mp4decrypt.exe",
    ):
        if p.is_file():
            return str(p.resolve())
    return shutil.which("mp4decrypt")


def _subprocess_hide_kw() -> dict:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"startupinfo": si, "creationflags": flag}


def _ffmpeg_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_subprocess_hide_kw(),
    )


def _cdn_headers(http_headers: dict[str, str] | None) -> dict[str, str]:
    hdrs = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Referer": "https://reading.snssdk.com/",
    }
    if http_headers:
        for k, v in http_headers.items():
            if not v:
                continue
            kl = k.lower()
            if kl == "user-agent":
                hdrs["User-Agent"] = v
            elif kl == "cookie":
                hdrs["Cookie"] = v
            elif kl not in ("host", "content-length"):
                hdrs[k] = v
    return hdrs


def download_cdn(url: str, headers: dict, timeout: int = 180) -> bytes:
    hdrs = _cdn_headers(headers)
    last: Exception | None = None
    for attempt in range(4):
        try:
            with requests.get(url, headers=hdrs, timeout=(30, timeout), stream=True) as r:
                r.raise_for_status()
                parts = [c for c in r.iter_content(262144) if c]
            data = b"".join(parts)
            if len(data) < 1024:
                raise OSError(f"过短 {len(data)}B")
            return data
        except (ReqConnectionError, ChunkedEncodingError, requests.Timeout, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore


def download_play_bytes(play: dict, http_headers: dict) -> tuple[bytes, str]:
    tried: list[str] = []
    errors: list[str] = []
    for label, key in (("main", "main_url"), ("backup", "backup_url")):
        url = (play.get(key) or "").strip()
        if not url or url in tried:
            continue
        tried.append(url)
        try:
            return download_cdn(url, http_headers), label
        except Exception as e:
            errors.append(f"{label}: {e}")
    raise RuntimeError("CDN 下载失败: " + "; ".join(errors) if errors else "无可用 URL")


def _mime_type_from_play(play: dict) -> str:
    url = (play.get("main_url") or "") + (play.get("backup_url") or "")
    for token in ("mime_type=video_mp4", "mime_type=audio_mp4"):
        if token in url:
            return token.split("=", 1)[1]
    return ""


def _encrypted_temp_suffix(play: dict) -> str:
    return ".mp4" if _mime_type_from_play(play) == "video_mp4" else ".m4a"


def _ffprobe_audio_codec(path: Path) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file():
        return ""
    proc = _ffmpeg_run(
        [ffprobe, "-hide_banner", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    )
    return (proc.stdout or "").strip().lower() if proc.returncode == 0 else ""


def _ffprobe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = _ffmpeg_run(
        [ffprobe, "-hide_banner", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return 0.0


def extract_cenc_kid(data: bytes) -> str:
    kids: list[str] = []
    i = 0
    while True:
        j = data.find(b"tenc", i)
        if j < 0:
            break
        kid = data[j + 12 : j + 28]
        if len(kid) == 16:
            kids.append(kid.hex())
        i = j + 4
    return kids[0] if kids else ""


def _decode_stderr_ok(stderr: str) -> bool:
    if not stderr:
        return True
    bad = (
        "Error parsing the packet header",
        "Invalid data found when processing input",
        "channel element",
        "Prediction is not allowed in AAC-LC",
        "decode_pce",
        "Decoding error",
    )
    low = stderr.lower()
    return not any(x.lower() in low for x in bad)


def verify_decrypted_audio(path: Path, *, ffmpeg: str | None = None, sample_seconds: float = 30.0) -> tuple[float, float, bool]:
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg or not path.is_file():
        return 0.0, 0.0, False
    container = _ffprobe_duration(path)
    if container <= 0:
        return 0.0, 0.0, False
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = Path(tmp.name)
    try:
        probe_len = min(sample_seconds, container)
        proc = _ffmpeg_run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-t", str(probe_len), "-f", "wav", str(wav)]
        )
        stderr = proc.stderr or ""
        if proc.returncode != 0 or not wav.is_file() or not _decode_stderr_ok(stderr):
            return container, 0.0, False
        decoded = _ffprobe_duration(wav)
        ok = decoded >= probe_len * 0.85 and decoded >= 1.0
        return container, decoded, ok
    finally:
        wav.unlink(missing_ok=True)


def _transcode_audio(src: Path, dst: Path, *, ffmpeg: str, codec: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = ["-vn", "-c:a", "libmp3lame", "-q:a", "2"] if codec == "mp3" else ["-vn", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"]
    proc = _ffmpeg_run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *args, str(dst)])
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size < 1024:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-800:])


def _finalize_decrypted(decrypted: Path, out_path: Path, *, ffmpeg: str, audio_codec: str) -> None:
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target = out_path.suffix.lower()
    if audio_codec == "opus" or target == ".mp3":
        _transcode_audio(decrypted, out_path, ffmpeg=ffmpeg, codec="mp3" if target == ".mp3" else "m4a")
        return
    if decrypted.suffix.lower() == target:
        if decrypted != out_path:
            shutil.move(str(decrypted), str(out_path))
        return
    proc = _ffmpeg_run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(decrypted), "-vn", "-c:a", "copy", str(out_path)])
    if proc.returncode != 0:
        _transcode_audio(decrypted, out_path, ffmpeg=ffmpeg, codec="m4a")


def _decrypt_with_ffmpeg(enc_path: Path, out_path: Path, play: dict) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法解密听书")
    key_hex = cenc_key_hex(play)
    work_ext = _encrypted_temp_suffix(play)
    with tempfile.NamedTemporaryFile(suffix=work_ext, delete=False) as tmp:
        decrypted = Path(tmp.name)
    try:
        proc = _ffmpeg_run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-decryption_key", key_hex, "-i", str(enc_path), "-c", "copy", str(decrypted)]
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-800:])
        codec = _ffprobe_audio_codec(decrypted)
        _finalize_decrypted(decrypted, out_path, ffmpeg=ffmpeg, audio_codec=codec)
    finally:
        decrypted.unlink(missing_ok=True)


def decrypt_cenc_file(enc_path: Path, out_path: Path, play: dict) -> str:
    """解密 CENC 并校验可播；失败抛错，避免写出杂音文件。"""
    _decrypt_with_ffmpeg(enc_path, out_path, play)
    ffmpeg = find_ffmpeg()
    container, decoded, ok = verify_decrypted_audio(out_path, ffmpeg=ffmpeg)
    if ok:
        return "ffmpeg"
    mp4d = find_mp4decrypt()
    work_ext = _encrypted_temp_suffix(play)
    enc_bytes = enc_path.read_bytes()
    _, kid_vm = spade_and_kid_from_play(play)
    kid = kid_vm or extract_cenc_kid(enc_bytes)
    if mp4d:
        tmp_out = Path(tempfile.mktemp(suffix=work_ext))
        try:
            proc = _ffmpeg_run([mp4d, "--key", f"{kid}:{cenc_key_hex(play)}", str(enc_path), str(tmp_out)])
            if proc.returncode == 0 and tmp_out.is_file() and tmp_out.stat().st_size > 0:
                codec = _ffprobe_audio_codec(tmp_out)
                with tempfile.NamedTemporaryFile(suffix=out_path.suffix, delete=False) as fin:
                    final_tmp = Path(fin.name)
                try:
                    _finalize_decrypted(tmp_out, final_tmp, ffmpeg=ffmpeg or "", audio_codec=codec)
                    _, _, ok2 = verify_decrypted_audio(final_tmp, ffmpeg=ffmpeg)
                    if ok2:
                        shutil.move(str(final_tmp), str(out_path))
                        return "mp4decrypt"
                finally:
                    final_tmp.unlink(missing_ok=True)
        finally:
            tmp_out.unlink(missing_ok=True)
    raise RuntimeError(
        f"CENC 解密校验失败：容器约 {container:.0f}s，可解码约 {decoded:.1f}s。"
        "请确认 play 含 video_model.spade_a，并使用项目根目录带 -decryption_key 补丁的 ffmpeg.exe。"
    )


def download_chapter_audio(play: dict, out_path: Path, http_headers: dict) -> Path:
    if not (play.get("main_url") or play.get("backup_url")):
        raise RuntimeError("无播放地址")
    out_path = out_path.resolve()
    if out_path.suffix.lower() not in {".m4a", ".mp4", ".mp3"}:
        out_path = out_path.with_suffix(".m4a")
    data, src = download_play_bytes(play, http_headers)
    if src == "backup":
        print("  提示: 主 CDN 失败，已用 backup_url")
    need_decrypt = bool(
        play.get("is_encrypt")
        or file_has_cenc(data)
        or spade_from_play(play)
    )
    if need_decrypt:
        if not spade_from_play(play):
            raise RuntimeError("检测到 CENC 加密流但无 spade_a，无法解密")
        enc_suffix = _encrypted_temp_suffix(play)
        with tempfile.NamedTemporaryFile(suffix=enc_suffix + ".enc", delete=False) as t:
            enc = Path(t.name)
        try:
            enc.write_bytes(data)
            decrypt_cenc_file(enc, out_path, play)
        finally:
            enc.unlink(missing_ok=True)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
    return out_path


# ---------------------------------------------------------------------------
# API 客户端
# ---------------------------------------------------------------------------
def _rticket() -> str:
    return str(int(time.time() * 1000))  # noqa: after time restore


def _common_query(device_id: str, install_id: str, cdid: str) -> dict[str, str]:
    return {
        "aid": str(AID),
        "app_name": "novelapp",
        "version_code": "69532",
        "version_name": "6.9.5.32",
        "device_platform": "android",
        "os": "android",
        "ssmix": "a",
        "device_type": DEVICE_MODELS[0],
        "device_brand": "Xiaomi",
        "language": "zh",
        "os_api": "28",
        "os_version": "9",
        "manifest_version_code": "69532",
        "resolution": "1080*1920",
        "dpi": "480",
        "update_version_code": "69532",
        "channel": "43536163a",
        "ac": "wifi",
        "device_id": device_id,
        "iid": install_id,
        "cdid": cdid,
        "pv_player": "69532",
        "normal_session_id": str(uuid.uuid4()),
        "cold_start_session_id": str(uuid.uuid4()),
        **SEARCH_EXTRA,
    }


class FanqieClient:
    def __init__(self, *, use_capture: bool = False) -> None:
        """默认懒初始化：优先加载已保存身份，首次 API 调用时才注册设备。"""
        self._use_capture = use_capture
        self._api_lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._initialized = False
        self.device_id = ""
        self.install_id = ""
        self.device_token = ""
        self.cdid = ""
        self.cookies: dict[str, str] = {}
        self.common: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self.cookie_header = ""

        if use_capture:
            cap = load_capture_identity()
            if cap:
                did, iid, cookies, cdid = cap
                print("使用抓包「参数」设备身份")
                self._apply_device_identity(did, iid, cookies, "", cdid, log_init=True)
                self._initialized = True
                return

        saved = load_saved_device_identity()
        if saved:
            did, iid, cookies, token, cdid, session = saved
            self._apply_device_identity(
                did, iid, cookies, token, cdid,
                session_ids=session, log_init=False,
            )
            self._initialized = True
            print(f"📂 已加载保存的番茄听书设备 device_id={did}")

    def _ensure_initialized(self) -> None:
        """首次 API 访问前注册设备（若尚无已保存身份）。"""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            saved = load_saved_device_identity()
            if saved:
                did, iid, cookies, token, cdid, session = saved
                self._apply_device_identity(
                    did, iid, cookies, token, cdid,
                    session_ids=session, log_init=False,
                )
                self._initialized = True
                print(f"📂 已加载保存的番茄听书设备 device_id={did}")
                return
            print("正在注册设备并获取 Cookie…")
            self._apply_device_identity(
                *self._register_identity(self._use_capture), log_init=True,
            )
            save_device_identity(self)
            self._initialized = True

    def _register_identity(
        self, use_capture: bool,
    ) -> tuple[str, str, dict[str, str], str, str]:
        cap = load_capture_identity() if use_capture else None
        if cap:
            did, iid, cookies, cdid = cap
            print("使用抓包「参数」设备身份")
            return did, iid, cookies, "", cdid
        return register_device()

    def _apply_device_identity(
        self,
        did: str,
        iid: str,
        cookies: dict[str, str],
        token: str,
        cdid: str,
        *,
        session_ids: dict[str, str] | None = None,
        log_init: bool = False,
    ) -> None:
        self.device_id = did
        self.install_id = iid
        self.device_token = token
        self.cdid = cdid
        self.cookies = dict(cookies)
        self.common = _common_query(did, iid, cdid)
        if session_ids:
            for key in ("normal_session_id", "cold_start_session_id"):
                val = session_ids.get(key)
                if val:
                    self.common[key] = val
        self.headers = {
            "accept": "application/json; charset=utf-8,application/x-protobuf",
            "accept-encoding": "gzip",
            "x-xs-from-web": "0",
            "x-vc-bdturing-sdk-version": "4.0.3.cn",
            "authorization": "Bearer",
            "lc": "101",
            "sdk-version": "2",
            "passport-sdk-version": "50564",
            "x-tt-store-region": self.cookies.get("store-region", "cn-gd"),
            "x-tt-store-region-src": self.cookies.get("store-region-src", "did"),
            "user-agent": UA,
        }
        self._refresh_cookie_header()
        if log_init:
            print(f"  device_id={did}  install_id={iid}")
            if token:
                print(f"  device_token={token[:48]}…")

    def _refresh_cookie_header(self) -> None:
        self.cookie_header = cookies_to_header(self.cookies)
        self.headers["Cookie"] = self.cookie_header
        if "store-region" in self.cookies:
            self.headers["x-tt-store-region"] = self.cookies["store-region"]
        if "store-region-src" in self.cookies:
            self.headers["x-tt-store-region-src"] = self.cookies["store-region-src"]

    def _recalculate_cookie(self, resp: requests.Response | None = None) -> None:
        """失败时：合并 Set-Cookie 并刷新会话 ID / Cookie 头（不重新注册设备）。"""
        if resp is not None:
            self.cookies = merge_response_cookies(self.cookies, resp)
        self.common["normal_session_id"] = str(uuid.uuid4())
        self.common["cold_start_session_id"] = str(uuid.uuid4())
        self._refresh_cookie_header()
        save_device_identity(self)
        print("  🔄 已重新计算 Cookie（合并响应 + 刷新会话签名）")

    def _reinitialize_device(self) -> None:
        """失败时：重新注册设备并刷新 Cookie，覆盖旧保存。"""
        print("  📱 API 仍不可用，重新初始化设备…")
        self._apply_device_identity(*self._register_identity(self._use_capture))
        self._recalculate_cookie()
        save_device_identity(self)
        print(f"  ✅ 设备已重建 device_id={self.device_id}")

    @staticmethod
    def _json_ok(j: Any) -> bool:
        return isinstance(j, dict) and j.get("code") == 0

    @staticmethod
    def _is_business_error(j: Any) -> bool:
        if not isinstance(j, dict) or j.get("code") == 0:
            return False
        msg = str(j.get("message") or "")
        return any(k in msg for k in ("不存在", "停止合作", "下架", "已删除", "无权限阅读"))

    def _is_retriable_failure(
        self,
        exc: BaseException | None,
        j: Any,
        resp: requests.Response | None,
    ) -> bool:
        if self._is_business_error(j):
            return False
        if exc is not None:
            if isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.HTTPError)):
                return True
            if isinstance(exc, (json.JSONDecodeError, ValueError)):
                return True
            if isinstance(exc, RuntimeError):
                return True
        if resp is not None and resp.status_code in (401, 403, 408, 429, 500, 502, 503, 504):
            return True
        if isinstance(j, dict) and j.get("code") not in (None, 0):
            return True
        return False

    def _url(self, path: str, extra: dict[str, str]) -> str:
        qs = {**self.common, **extra, "_rticket": _rticket()}
        return f"{HOST}{path}?{urlencode(qs)}"

    def _request(self, url: str, *, method: str = "GET", body: bytes | None = None) -> requests.Response:
        h = dict(self.headers)
        if body:
            h["content-type"] = "application/json; charset=utf-8"
        q = urlparse(url).query
        h.update(sign_headers(q, cookie=h.get("Cookie"), body=body, aid=_aid_from_query(q)))
        resp = requests.request(method, url, headers=h, data=body, timeout=60)
        self.cookies = merge_response_cookies(self.cookies, resp)
        self._refresh_cookie_header()
        return resp

    def _request_json_with_recovery(
        self,
        build_url: Any,
        *,
        method: str = "GET",
        body: bytes | None = None,
    ) -> Any:
        """统一 API 恢复：失败 → 重算 Cookie → 仍失败 → 重建设备 → 再重算 Cookie。"""
        last_exc: BaseException | None = None
        last_resp: requests.Response | None = None
        last_j: Any = None

        def _attempt(label: str) -> Any | None:
            nonlocal last_exc, last_resp, last_j
            url = build_url()
            try:
                resp = self._request(url, method=method, body=body)
                last_resp = resp
                if resp.status_code >= 400:
                    resp.raise_for_status()
                j = resp.json()
                last_j = j
                if self._json_ok(j):
                    return j
                if self._is_business_error(j):
                    raise RuntimeError(j.get("message") or f"API 业务错误 code={j.get('code')}")
                last_exc = RuntimeError(j.get("message") or f"API 错误 code={j.get('code')}")
            except Exception as e:
                last_exc = e
            print(f"  ⚠️ API 访问失败 ({label}): {last_exc}")
            return None

        self._ensure_initialized()
        with self._api_lock:
            j = _attempt("首次")
            if j is not None:
                return j
            if not self._is_retriable_failure(last_exc, last_j, last_resp):
                raise last_exc or RuntimeError("API 访问失败")

            self._recalculate_cookie(last_resp)
            j = _attempt("重算Cookie后")
            if j is not None:
                return j
            if not self._is_retriable_failure(last_exc, last_j, last_resp):
                raise last_exc or RuntimeError("API 访问失败")

            self._reinitialize_device()
            j = _attempt("重建设备后")
            if j is not None:
                return j
            raise last_exc or RuntimeError("API 访问失败（已重算 Cookie 并重建设备）")

    def _get_json(self, path: str, extra: dict[str, str]) -> Any:
        """GET JSON API：失败时先重算 Cookie，仍失败则重建设备后再重算 Cookie。"""
        return self._request_json_with_recovery(lambda: self._url(path, extra))

    def _extract_books_from_tab(self, tab: dict) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        tab_title = tab.get("title") or ""
        for cell in tab.get("data") or []:
            if not isinstance(cell, dict):
                continue
            stack = [cell]
            while stack:
                c = stack.pop()
                for b in c.get("book_data") or []:
                    if not isinstance(b, dict):
                        continue
                    bid = str(b.get("book_id") or "")
                    if not bid or bid in seen:
                        continue
                    seen.add(bid)
                    rows.append({
                        "book_id": bid,
                        "book_name": (b.get("book_name") or b.get("title") or "?").strip(),
                        "author": (b.get("author") or b.get("author_name") or "").strip(),
                        "tab_title": tab_title,
                    })
                for sub in c.get("cell_data") or []:
                    if isinstance(sub, dict):
                        stack.append(sub)
        return rows

    def _extract_videos_from_tab(self, tab: dict) -> list[dict]:
        """短剧/漫剧：video_data，series_id 作目录 book_id。"""
        rows: list[dict] = []
        seen: set[str] = set()
        tab_title = tab.get("title") or ""
        for cell in tab.get("data") or []:
            if not isinstance(cell, dict):
                continue
            stack = [cell]
            while stack:
                c = stack.pop()
                for v in c.get("video_data") or []:
                    if not isinstance(v, dict):
                        continue
                    sid = str(v.get("series_id") or v.get("book_id") or "")
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    name = (v.get("title") or v.get("raw_book_name") or "?").strip()
                    rows.append({
                        "book_id": sid,
                        "series_id": sid,
                        "book_name": name,
                        "author": (v.get("sub_title") or "").strip(),
                        "episode_cnt": v.get("episode_cnt"),
                        "tab_title": tab_title,
                    })
                for sub in c.get("cell_data") or []:
                    if isinstance(sub, dict):
                        stack.append(sub)
        return rows

    def _search_tab_paginated(
        self, keyword: str, tab_type: int, *, extract: str, max_pages: int = 30,
    ) -> tuple[list[dict], int]:
        all_rows: list[dict] = []
        seen: set[str] = set()
        offset, passback = 0, 0
        pages = 0
        for _ in range(max_pages):
            extra = {
                **SEARCH_EXTRA,
                "query": keyword,
                "last_search_page_query": keyword,
                "offset": str(offset),
                "passback": str(passback),
                "count": "20",
                "search_source": "1",
                "tab_type": str(tab_type),
                "tab_name": "store",
                "bookstore_tab": "2",
                "user_is_login": "0",
            }
            data = self._get_json("/reading/bookapi/search/tab/v", extra)
            if data.get("code") != 0:
                raise RuntimeError(f"搜索失败: {data.get('message')}")
            tab = next((t for t in data.get("search_tabs") or [] if t.get("tab_type") == tab_type), None)
            if not tab:
                break
            chunk = (
                self._extract_videos_from_tab(tab)
                if extract == "video"
                else self._extract_books_from_tab(tab)
            )
            for row in chunk:
                bid = row["book_id"]
                if bid not in seen:
                    seen.add(bid)
                    all_rows.append(row)
            pages += 1
            if not tab.get("has_more"):
                break
            nxt = tab.get("next_offset")
            pb = tab.get("passback")
            try:
                offset = int(nxt) if nxt is not None else int(pb or 0)
                passback = int(pb) if pb not in (None, "") else offset
            except (TypeError, ValueError):
                break
            if offset == 0 and pages > 1:
                break
        return all_rows, pages

    def search_by_kind(self, keyword: str, kind: str, *, max_pages: int = 30) -> list[dict]:
        """按类型搜索（书籍/听书/短剧/漫画/漫剧），自动翻页拉全。"""
        if kind not in SEARCH_KINDS:
            raise ValueError(f"未知类型 {kind}，可选: {', '.join(SEARCH_KINDS)}")
        meta = SEARCH_KINDS[kind]
        label = meta["label"]
        print(f"  正在搜索【{label}】并加载全部结果…", flush=True)
        rows, pages = self._search_tab_paginated(
            keyword, meta["tab_type"], extract=meta["extract"], max_pages=max_pages,
        )
        for row in rows:
            row["content_kind"] = kind
        unit = "部" if meta["extract"] == "video" else "本"
        print(f"  【{label}】搜索完成：共 {len(rows)} {unit}（{pages} 页）")
        return rows

    def search(self, keyword: str, *, kind: str = "book", max_pages: int = 30) -> list[dict]:
        return self.search_by_kind(keyword, kind, max_pages=max_pages)

    def _post_snssdk_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode()

        def build_url() -> str:
            qs = {**self.common, "_rticket": _rticket()}
            return f"{SNSSDK_HOST}{path}?{urlencode(qs)}"

        return self._request_json_with_recovery(build_url, method="POST", body=body)

    def video_playinfo(self, item_id: str) -> dict:
        """短剧/漫剧单集：novel/player/video_model → 可交给 download_chapter_audio 解密下载。"""
        j = self._post_snssdk_json(
            "/novel/player/video_model/v1/",
            {
                "biz_param": {
                    "device_level": 3,
                    "need_all_video_definition": False,
                    "video_platform": 1024,
                },
                "video_id": str(item_id),
            },
        )
        if j.get("code") != 0:
            raise RuntimeError(f"video_model 失败: {j.get('message')}")
        raw_vm = (j.get("data") or {}).get("video_model") or ""
        vm = json.loads(raw_vm) if isinstance(raw_vm, str) and raw_vm.strip() else (raw_vm or {})
        vl = vm.get("video_list") or []
        if isinstance(vl, dict):
            vl = list(vl.values())
        if not vl:
            raise RuntimeError("video_model 无 video_list")
        entry = vl[0]
        ei = entry.get("encrypt_info") or {}
        main_url = entry.get("main_url") or ""
        if main_url and not main_url.startswith("http"):
            main_url = base64.b64decode(main_url + "===").decode("utf-8", errors="replace")
        backup = entry.get("backup_url") or ""
        if backup and not backup.startswith("http"):
            backup = base64.b64decode(backup + "===").decode("utf-8", errors="replace")
        enc = bool(
            ei.get("encrypt")
            or entry.get("encrypt")
            or entry.get("is_encrypt")
            or (vm.get("fallback_api") or {}).get("fallback_api", "").find("stream_type=encrypt") >= 0
        )
        # 短剧 video_list 在 encrypt_info 内带 spade_a，需写入 video_list 供 cenc_key_hex 解析
        if ei.get("spade_a") and isinstance(vm.get("video_list"), list) and vm["video_list"]:
            vm["video_list"][0].setdefault("encrypt_info", ei)
            if not vm["video_list"][0].get("spade_a"):
                vm["video_list"][0]["spade_a"] = ei["spade_a"]
            if not vm["video_list"][0].get("kid"):
                vm["video_list"][0]["kid"] = ei.get("kid") or ""
        return {
            "item_id": str(item_id),
            "main_url": main_url,
            "backup_url": backup,
            "is_encrypt": enc,
            "encryption_key": ei.get("encryption_key") or entry.get("encryption_key") or ei.get("spade_a") or "",
            "video_model": json.dumps(vm, ensure_ascii=False) if isinstance(vm, dict) else raw_vm,
        }

    def book_detail(self, book_id: str) -> dict:
        j = self._get_json("/reading/bookapi/detail/v", {"book_id": book_id, "without_video": "false"})
        if j.get("code") != 0:
            raise RuntimeError(j.get("message"))
        return j.get("data") or {}

    @staticmethod
    def _parse_directory_items(d: dict) -> list[dict]:
        items: list[dict] = []
        il = d.get("item_data_list")
        if isinstance(il, list):
            for r in il:
                if isinstance(r, dict) and r.get("item_id"):
                    items.append(r)
        if items:
            return items
        for block in d.get("item_list") or []:
            if not isinstance(block, dict):
                continue
            nested = block.get("item_data") or block.get("items") or []
            if isinstance(nested, list):
                for it in nested:
                    if isinstance(it, dict) and it.get("item_id"):
                        items.append(it)
            if block.get("item_id") and not nested:
                items.append(block)
        return items

    def directory(self, book_id: str) -> list[dict]:
        print("  正在加载章节目录…", flush=True)
        j = self._get_json("/reading/bookapi/directory/all_items/v", {"book_id": book_id})
        if j.get("code") != 0:
            raise RuntimeError(j.get("message"))
        items = self._parse_directory_items(j.get("data") or {})
        print(f"  目录加载完成：共 {len(items)} 章")
        return items

    def reader_full(self, book_id: str, item_id: str) -> tuple[requests.Response, dict, CM]:
        cm = CM()
        h = dict(self.headers)
        hs = cm.client_handshake()
        h["y"] = hs
        h["x-reading-request"] = hs
        url = self._url(
            "/reading/reader/full/v",
            {
                "item_id": item_id,
                "book_id": book_id,
                "key_register_ts": "0",
                "req_type": "0",
                "unlock_mode": "0",
            },
        )
        q = urlparse(url).query
        h.update(sign_headers(q, cookie=h.get("Cookie")))
        resp = requests.get(url, headers=h, timeout=60)
        self.cookies = merge_response_cookies(self.cookies, resp)
        self._refresh_cookie_header()
        j = resp.json()
        if j.get("code") != 0:
            raise RuntimeError(j.get("message"))
        return resp, j.get("data") or {}, cm

    def mget_content(self, book_id: str, item_id: str, cm: CM) -> tuple[str, str]:
        hs = cm.client_handshake()
        body = json.dumps({"book_id": book_id, "item_ids": [item_id], "key": hs}, ensure_ascii=False).encode()
        qs = urlencode({
            "aid": "3040",
            "device_id": self.device_id,
            "iid": self.install_id,
            "version_code": "69532",
            "version_name": "6.9.5.32",
            "app_name": "novelapp",
            "device_platform": "android",
            "os": "android",
            "_rticket": _rticket(),
        })
        h = dict(self.headers)
        h["y"] = hs
        h["content-type"] = "application/json; charset=utf-8"
        url = f"{MGET_URL}?{qs}"
        h.update(sign_headers(qs, cookie=h.get("Cookie"), body=body, aid=_aid_from_query(qs)))
        r = requests.post(url, headers=h, data=body, timeout=60)
        self.cookies = merge_response_cookies(self.cookies, r)
        self._refresh_cookie_header()
        j = r.json()
        if j.get("code") != 0:
            msg = j.get("message") or ""
            if "停止合作" in msg or "不存在" in msg:
                raise RuntimeError(
                    f"{msg}（漫画/短剧等不走文字 mget，App 内走独立漫画模块拉图片，非 reader/full 正文）"
                )
            raise RuntimeError(msg)
        info = (j.get("data") or {}).get("item_infos") or {}
        info = info.get(item_id) or info.get(str(item_id)) or {}
        return info.get("key") or "", info.get("content") or ""

    def decrypt_chapter(self, book_id: str, item_id: str, full_data: dict, resp: requests.Response, cm: CM) -> str:
        content = full_data.get("content") or ""
        crypt_status = int(full_data.get("crypt_status") or 0)
        key = full_data.get("key") or resp.headers.get("y") or resp.headers.get("Y") or ""

        def _is_placeholder(s: str) -> bool:
            s = (s or "").strip()
            return not s or s in ("Invalid", "invalid") or len(s) < 32

        if crypt_status in (0, 1) and not _is_placeholder(content):
            try:
                return cm.decrypt(1, key, content)
            except Exception:
                pass

        # crypt_status=2 或 content=Invalid：新设备/部分书正文在 novelfm mget
        if crypt_status == 2 or _is_placeholder(content):
            mkey, mc = self.mget_content(book_id, item_id, cm)
            return cm.decrypt(1, mkey, mc)

        if content:
            return cm.decrypt(1, key, content)
        if not content:
            raise RuntimeError("无正文")
        mkey, mc = self.mget_content(book_id, item_id, cm)
        return cm.decrypt(1, mkey, mc)

    def audio_toneinfo(self, book_id: str) -> dict:
        j = self._get_json("/reading/bookapi/audio/toneinfo/", {
            "book_id": book_id, "is_exempt": "false", "is_local_book": "false",
        })
        if j.get("code") != 0:
            raise RuntimeError(j.get("message"))
        return j.get("data") or {}

    def audio_playinfo(self, item_ids: list[str], tone_id: int) -> list[dict]:
        j = self._get_json("/reading/reader/audio/playinfo/", {
            "tone_id": str(tone_id),
            "item_ids": ",".join(item_ids),
            "pv_player": self.common.get("pv_player", "69532"),
        })
        if j.get("code") != 0:
            raise RuntimeError(j.get("message"))
        data = j.get("data")
        if isinstance(data, list):
            return data
        return [data] if isinstance(data, dict) else []


# ---------------------------------------------------------------------------
# 听书菜单
# ---------------------------------------------------------------------------
@dataclass
class ListenChoice:
    play_book_id: str
    tone_id: int
    kind: Literal["real", "tts"]
    label: str


def _novel_id_for_tts(tone_data: dict, book_id: str) -> str:
    if tone_data.get("req_book_genre_type") in (1, "1", "AUDIOBOOK"):
        rel = tone_data.get("relate_novel_bookid")
        if rel:
            return str(rel)
    return book_id


def build_listen_menu(tone_data: dict, book_id: str) -> list[ListenChoice]:
    menu: list[ListenChoice] = []
    rec = tone_data.get("recommend_tone")
    default_tone = int(rec) if rec is not None else (
        int((tone_data.get("tts_tones") or [{}])[0].get("id", 4)) if tone_data.get("tts_tones") else 4
    )
    for a in tone_data.get("audio_tones") or []:
        if a.get("abook_id") is None:
            continue
        menu.append(ListenChoice(str(a["abook_id"]), default_tone, "real", a.get("title") or "真人演播"))
    novel_id = _novel_id_for_tts(tone_data, book_id)
    for t in tone_data.get("tts_tones") or []:
        tid = int(t["id"])
        menu.append(ListenChoice(novel_id, tid, "tts", f"{t.get('title','?')} (AI id={tid})"))
    return menu


def resolve_listen(
    tone_data: dict, book_id: str, *, tone_id: int | None, voice: bool, pick: int | None,
) -> ListenChoice | None:
    menu = build_listen_menu(tone_data, book_id)
    if not menu:
        return None
    if voice:
        return next((m for m in menu if m.kind == "real"), None)
    if tone_id is not None:
        nid = _novel_id_for_tts(tone_data, book_id)
        for m in menu:
            if m.kind == "tts" and m.tone_id == tone_id and m.play_book_id == nid:
                return m
        return ListenChoice(nid, tone_id, "tts", f"AI tone_id={tone_id}")
    if pick and 1 <= pick <= len(menu):
        return menu[pick - 1]
    return None


# ---------------------------------------------------------------------------
# 工具与交互
# ---------------------------------------------------------------------------
def html_to_plain(html: str) -> str:
    if not html:
        return ""
    parts = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.I | re.S)
    if parts:
        return "\n　　".join(re.sub(r"<[^>]+>", "", p).strip() for p in parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "", html)).strip()


def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s.strip())[:80] or "chapter"


def default_chapter_txt_path(book_name: str, chapter_index: int, title: str) -> Path:
    book_dir = SCRIPT_DIR / safe_name(book_name)
    book_dir.mkdir(parents=True, exist_ok=True)
    return book_dir / f"{chapter_index:04d}_{safe_name(title)}.txt"


def print_indexed_list(
    entries: list,
    *,
    label: str,
    line_fmt,
    list_path: Path | None = None,
    head: int = 40,
    tail: int = 8,
    threshold: int = 100,
) -> None:
    """打印编号列表；条目过多时写入 list_path 并折叠中间行。"""
    lines = [line_fmt(i, e) for i, e in enumerate(entries, 1)]
    if list_path:
        list_path.parent.mkdir(parents=True, exist_ok=True)
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if len(lines) <= threshold:
        for line in lines:
            print(line)
    else:
        for line in lines[:head]:
            print(line)
        omitted = len(lines) - head - tail
        if list_path:
            print(f"  … 省略 {omitted} 条，完整列表: {list_path.resolve()}")
        else:
            print(f"  … 省略 {omitted} 条")
        for line in lines[-tail:]:
            print(line)
    print(f"  （{label}已全部加载，共 {len(entries)} 项）")


def _http_dl_headers(client: FanqieClient) -> dict:
    return {"Cookie": client.cookie_header, "user-agent": UA}


def run_playlet_download(
    client: FanqieClient,
    *,
    series_id: str,
    series_name: str,
    save: Path | None,
    download: bool,
) -> None:
    chapters = client.directory(series_id)
    if not chapters:
        print("目录空")
        return
    ch_catalog = SCRIPT_DIR / safe_name(series_name) / "_章节目录.txt"
    print_indexed_list(
        chapters,
        label="集数",
        line_fmt=lambda i, c: f"{i:4}. {c.get('title', '?')}",
        list_path=ch_catalog,
    )
    ci = pick(f"\n选集 (1–{len(chapters)})> ", len(chapters))
    if not ci:
        return
    ch = chapters[ci - 1]
    item_id, title = str(ch["item_id"]), ch.get("title") or ""
    print(f"\n拉取视频: {title}")
    play = client.video_playinfo(item_id)
    print(f"加密={play.get('is_encrypt')}  url={str(play.get('main_url', ''))[:80]}…")
    if download or save:
        if play.get("is_encrypt") and not find_ffmpeg():
            print("需要 ffmpeg 解密")
            return
        out = save or Path(f"{safe_name(series_name)}") / f"{ci:03d}_{safe_name(title)}.mp4"
        path = download_chapter_audio(play, out, _http_dl_headers(client))
        print(f"已下载 {path} ({path.stat().st_size // 1024} KB)")
    else:
        ans = input("下载 mp4? [y/N]> ").strip().lower()
        if ans in ("y", "yes", "是"):
            out = Path(f"{safe_name(series_name)}") / f"{ci:03d}_{safe_name(title)}.mp4"
            path = download_chapter_audio(play, out, _http_dl_headers(client))
            print(f"已下载 {path}")


def pick(prompt: str, n: int) -> int | None:
    raw = input(prompt).strip()
    if not raw:
        return None
    try:
        i = int(raw)
        if 1 <= i <= n:
            return i
    except ValueError:
        pass
    print(f"请输入 1–{n}")
    return None


def run_read(client: FanqieClient, *, plain: bool, save: Path | None, kind: str = "book") -> None:
    kw = input("\n搜索关键词> ").strip()
    if not kw:
        return
    books = client.search_by_kind(kw, kind)
    if not books:
        print("无结果")
        return
    catalog_path = SCRIPT_DIR / f"{safe_name(kw)}_搜索结果.txt"
    print_indexed_list(
        books,
        label="搜索结果",
        line_fmt=lambda i, b: f"{i:3}. {b['book_name']} — {b.get('author', '')}  id={b['book_id']}",
        list_path=catalog_path,
    )
    bi = pick("\n选书> ", len(books))
    if not bi:
        return
    book = books[bi - 1]
    book_id, book_name = book["book_id"], book["book_name"]
    chapters = client.directory(book_id)
    if not chapters:
        print("目录空")
        return
    ch_catalog = SCRIPT_DIR / safe_name(book_name) / "_章节目录.txt"
    print_indexed_list(
        chapters,
        label="章节",
        line_fmt=lambda i, c: f"{i:4}. {c.get('title', '?')}",
        list_path=ch_catalog,
    )
    ci = pick(f"\n选章 (1–{len(chapters)})> ", len(chapters))
    if not ci:
        return
    ch = chapters[ci - 1]
    item_id, title = str(ch["item_id"]), ch.get("title") or ""
    print(f"\n拉取正文: {title}")
    resp, data, cm = client.reader_full(book_id, item_id)
    body = client.decrypt_chapter(book_id, item_id, data, resp, cm)
    text = html_to_plain(body) if plain else body
    preview = text[:4000] + "\n…" if len(text) > 4000 else text
    print("\n" + "=" * 60 + "\n" + preview)
    out = save or default_chapter_txt_path(book_name, ci, title)
    out.write_text(f"{title}\n\n{html_to_plain(body)}", encoding="utf-8")
    print(f"\n已保存正文 {out.resolve()} ({out.stat().st_size // 1024} KB)")


def run_comic(client: FanqieClient, *, save: Path | None) -> None:
    """漫画：搜索 → 目录 → 章节（部分书需抓包身份或仅 App 可读）。"""
    kw = input("\n搜索关键词> ").strip()
    if not kw:
        return
    books = client.search_by_kind(kw, "comic")
    if not books:
        print("无结果")
        return
    print_indexed_list(
        books,
        label="漫画",
        line_fmt=lambda i, b: f"{i:3}. {b['book_name']} — {b.get('author', '')}  id={b['book_id']}",
        list_path=SCRIPT_DIR / f"{safe_name(kw)}_漫画搜索.txt",
    )
    bi = pick("\n选漫画> ", len(books))
    if not bi:
        return
    book = books[bi - 1]
    book_id, book_name = book["book_id"], book["book_name"]
    chapters = client.directory(book_id)
    if not chapters:
        print("目录空")
        return
    print_indexed_list(
        chapters,
        label="话",
        line_fmt=lambda i, c: f"{i:4}. {c.get('title', '?')}",
        list_path=SCRIPT_DIR / safe_name(book_name) / "_话目录.txt",
    )
    ci = pick(f"\n选话 (1–{len(chapters)})> ", len(chapters))
    if not ci:
        return
    ch = chapters[ci - 1]
    item_id, title = str(ch["item_id"]), ch.get("title") or ""
    print(f"\n拉取: {title}")
    try:
        resp, data, cm = client.reader_full(book_id, item_id)
        body = client.decrypt_chapter(book_id, item_id, data, resp, cm)
    except RuntimeError as e:
        print(f"获取失败: {e}")
        print(
            "原因说明: App 内漫画走 component/comic 独立链路（分页图片 CDN），"
            "不会用 novelfm mget 文字接口。reader/full 对漫画仅返回 Invalid 占位。"
            "当前脚本未实现漫画话图片批量下载；可试 --use-capture 登录态或仅保存封面。"
        )
        try:
            j = client._get_json(
                "/reading/bookapi/directory/all_infos/v",
                {
                    "item_ids": item_id,
                    "with_virtual_directory": "false",
                    "with_chapter_abstract": "false",
                    "without_tone_infos": "false",
                    "ug_lock_mode": "0",
                    "directory_source": "1",
                },
            )
            info = (j.get("data") or [{}])[0]
            thumb = info.get("chapter_thumb_url") or ""
            if thumb:
                ans = input(f"仅下载章节封面? [y/N]> ").strip().lower()
                if ans in ("y", "yes", "是"):
                    out = save or default_chapter_txt_path(book_name, ci, title).with_suffix(".heic")
                    data = download_cdn(thumb, _http_dl_headers(client))
                    out.write_bytes(data)
                    print(f"已保存封面 {out.resolve()}")
        except Exception:
            pass
        return
    text = html_to_plain(body)
    print("\n" + "=" * 60 + "\n" + (text[:2000] + "\n…" if len(text) > 2000 else text))
    out = save or default_chapter_txt_path(book_name, ci, title)
    out.write_text(f"{title}\n\n{text}", encoding="utf-8")
    print(f"\n已保存 {out.resolve()}")


def run_playlet(client: FanqieClient, *, save: Path | None, download: bool) -> None:
    kw = input("\n搜索关键词> ").strip()
    if not kw:
        return
    items = client.search_by_kind(kw, "playlet")
    if not items:
        print("无结果")
        return
    print_indexed_list(
        items,
        label="短剧",
        line_fmt=lambda i, b: f"{i:3}. {b['book_name']}  共{b.get('episode_cnt') or '?'}集  id={b['book_id']}",
        list_path=SCRIPT_DIR / f"{safe_name(kw)}_短剧搜索.txt",
    )
    bi = pick("\n选短剧> ", len(items))
    if not bi:
        return
    it = items[bi - 1]
    run_playlet_download(
        client,
        series_id=it["book_id"],
        series_name=it["book_name"],
        save=save,
        download=download,
    )


def run_manju(client: FanqieClient, *, save: Path | None, download: bool) -> None:
    kw = input("\n搜索关键词> ").strip()
    if not kw:
        return
    items = client.search_by_kind(kw, "manju")
    if not items:
        print("无漫剧结果，可换关键词或改搜「短剧」")
        return
    print_indexed_list(
        items,
        label="漫剧",
        line_fmt=lambda i, b: f"{i:3}. {b['book_name']}  id={b['book_id']}",
        list_path=SCRIPT_DIR / f"{safe_name(kw)}_漫剧搜索.txt",
    )
    bi = pick("\n选漫剧> ", len(items))
    if not bi:
        return
    it = items[bi - 1]
    run_playlet_download(
        client,
        series_id=it["book_id"],
        series_name=it["book_name"],
        save=save,
        download=download,
    )


def run_interactive(client: FanqieClient, *, plain: bool, save: Path | None, download: bool) -> None:
    print("1=书籍  2=听书  3=短剧  4=漫画  5=漫剧")
    raw = input("> ").strip()
    if raw == "2":
        run_audio(client, save=save, download=download)
    elif raw == "3":
        run_playlet(client, save=save, download=download)
    elif raw == "4":
        run_comic(client, save=save)
    elif raw == "5":
        run_manju(client, save=save, download=download)
    else:
        run_read(client, plain=plain, save=save, kind="book" if raw != "1" else "book")


def run_audio(
    client: FanqieClient,
    *,
    tone_id: int | None,
    voice: bool,
    save: Path | None,
    download: bool,
) -> None:
    kw = input("\n搜索关键词> ").strip()
    if not kw:
        return
    books = client.search_by_kind(kw, "audio")
    if not books:
        print("无结果")
        return
    print_indexed_list(
        books,
        label="听书",
        line_fmt=lambda i, b: f"{i:3}. {b['book_name']}  id={b['book_id']}",
        list_path=SCRIPT_DIR / f"{safe_name(kw)}_听书搜索.txt",
    )
    bi = pick("\n选书> ", len(books))
    if not bi:
        return
    book_id = books[bi - 1]["book_id"]
    tone_data = client.audio_toneinfo(book_id)
    menu = build_listen_menu(tone_data, book_id)
    if not menu:
        print("无听书音色")
        return
    for i, m in enumerate(menu, 1):
        tag = "真人" if m.kind == "real" else "AI"
        print(f"{i:3}. [{tag}] {m.label}")
    listen = resolve_listen(tone_data, book_id, tone_id=tone_id, voice=voice, pick=None)
    if listen is None or (tone_id is None and not voice):
        pi = pick("\n选音色> ", len(menu))
        if not pi:
            return
        listen = menu[pi - 1]
    print(f"\n使用: {listen.label}")
    chapters = client.directory(listen.play_book_id)
    if not chapters:
        print("目录空")
        return
    print_indexed_list(
        chapters,
        label="章节",
        line_fmt=lambda i, c: f"{i:4}. {c.get('title', '?')}",
        list_path=SCRIPT_DIR / safe_name(listen.label) / "_章节目录.txt",
    )
    ci = pick(f"\n选章 (1–{len(chapters)})> ", len(chapters))
    if not ci:
        return
    ch = chapters[ci - 1]
    item_id, title = str(ch["item_id"]), ch.get("title") or ""
    plays = client.audio_playinfo([item_id], listen.tone_id)
    if not plays:
        print("无 playinfo")
        return
    play = plays[0]
    print(f"加密={play.get('is_encrypt')}  url={str(play.get('main_url',''))[:80]}…")
    if download or save:
        if play.get("is_encrypt") and not find_ffmpeg():
            print("需要 ffmpeg 解密")
            return
        out = save or Path(f"{safe_name(title)}.m4a")
        path = download_chapter_audio(play, out, _http_dl_headers(client))
        print(f"已下载 {path} ({path.stat().st_size // 1024} KB)")
    else:
        ans = input("下载 m4a? [y/N]> ").strip().lower()
        if ans in ("y", "yes", "是"):
            out = Path(f"{safe_name(title)}.m4a")
            path = download_chapter_audio(play, out, _http_dl_headers(client))
            print(f"已下载 {path}")


def run_batch_read(
    client: FanqieClient, q: str, book_i: int, ch_i: int, plain: bool, save: Path | None, *, kind: str = "book",
) -> None:
    books = client.search_by_kind(q, kind)
    book = books[book_i - 1]
    book_id, book_name = book["book_id"], book["book_name"]
    chapters = client.directory(book_id)
    ch = chapters[ch_i - 1]
    title = ch.get("title") or ""
    resp, data, cm = client.reader_full(book_id, str(ch["item_id"]))
    body = client.decrypt_chapter(book_id, str(ch["item_id"]), data, resp, cm)
    text = html_to_plain(body) if plain else body
    print(text[:3000])
    out = save or default_chapter_txt_path(book_name, ch_i, title)
    out.write_text(f"{title}\n\n{html_to_plain(body)}", encoding="utf-8")
    print(f"已保存 {out.resolve()}")


def run_batch_playlet(
    client: FanqieClient, q: str, book_i: int, ch_i: int, save: Path | None, download: bool,
) -> None:
    items = client.search_by_kind(q, "playlet")
    it = items[book_i - 1]
    chapters = client.directory(it["book_id"])
    ch = chapters[ch_i - 1]
    play = client.video_playinfo(str(ch["item_id"]))
    out = save or Path(safe_name(it["book_name"])) / f"{ch_i:03d}_{safe_name(ch.get('title') or 'ep')}.mp4"
    download_chapter_audio(play, out, _http_dl_headers(client))
    print(f"已保存 {out.resolve()}")


def run_batch_audio(
    client: FanqieClient, q: str, book_i: int, ch_i: int,
    tone_id: int | None, voice: bool, save: Path | None, download: bool,
) -> None:
    books = client.search_by_kind(q, "audio")
    book_id = books[book_i - 1]["book_id"]
    tone_data = client.audio_toneinfo(book_id)
    listen = resolve_listen(tone_data, book_id, tone_id=tone_id, voice=voice, pick=1)
    if not listen:
        menu = build_listen_menu(tone_data, book_id)
        listen = menu[0] if menu else None
    if not listen:
        raise SystemExit("无音色")
    ch = client.directory(listen.play_book_id)[ch_i - 1]
    plays = client.audio_playinfo([str(ch["item_id"])], listen.tone_id)
    out = save or Path(f"{safe_name(ch.get('title') or 'ch')}.m4a")
    download_chapter_audio(plays[0], out, _http_dl_headers(client))
    print(f"已保存 {out.resolve()}")


def main() -> None:
    ap = argparse.ArgumentParser(description="番茄完整版：分类搜索 + 书籍/听书/短剧/漫画下载")
    ap.add_argument(
        "--kind",
        choices=tuple(SEARCH_KINDS.keys()),
        default="book",
        help="搜索类型: book/audio/playlet/comic/manju",
    )
    ap.add_argument("--mode", choices=("read", "audio"), default="read", help="书籍/漫画=read，听书=audio")
    ap.add_argument("-q", "--query")
    ap.add_argument("--book", type=int, default=1)
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--tone", type=int, help="AI 音色 id")
    ap.add_argument("--voice", action="store_true", help="真人演播")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--plain", action="store_true")
    ap.add_argument("--save", type=Path)
    ap.add_argument(
        "--use-capture",
        action="store_true",
        help="使用上级「参数」抓包中的 device_id/iid/Cookie（默认仅自动注册）",
    )
    args = ap.parse_args()

    client = FanqieClient(use_capture=args.use_capture)
    if args.query:
        k = args.kind
        if k in ("playlet", "manju"):
            run_batch_playlet(client, args.query, args.book, args.chapter, args.save, args.download)
        elif k == "audio" or args.mode == "audio":
            run_batch_audio(client, args.query, args.book, args.chapter, args.tone, args.voice, args.save, args.download)
        else:
            run_batch_read(client, args.query, args.book, args.chapter, args.plain, args.save, kind=k)
    else:
        run_interactive(client, plain=args.plain, save=args.save, download=args.download)


if __name__ == "__main__":
    main()

