#!/usr/bin/env node
// 纯代码喜马拉雅 wfp（数美 ATS openId）取号器 —— 不依赖 playwright/chromium。
// 用法: node ximalaya_wfp_node.js [--ats /path/to/ats.js] [--out /path/wfp.txt]
// 依赖: node(>=16) + jsdom（npm i jsdom；或 NODE_PATH 指向已装目录）
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const ARGS = process.argv;
const ARG = (k, d) => { const i = ARGS.indexOf(k); return i >= 0 ? ARGS[i + 1] : d; };
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const PAGE = "https://www.ximalaya.com/album/130326634";
const ATS_PATH = ARG("--ats", path.join(__dirname, "vendor", "ximalaya_ats_sdk.js"));
const OUT = ARG("--out", "/tmp/ximalaya_wfp.txt");
const TIMEOUT_MS = Number(ARG("--timeout", "40000"));

let JSDOM;
try { JSDOM = require("jsdom").JSDOM; } catch (e) {
  console.error("需要 jsdom：npm i jsdom（或设置 NODE_PATH）"); process.exit(5);
}
const dom = new JSDOM("<!DOCTYPE html><html><head></head><body></body></html>", {
  url: PAGE, referrer: "https://www.ximalaya.com/",
  userAgent: UA, pretendToBeVisual: true, storageQuota: 10000000,
});
for (const key of Object.getOwnPropertyNames(dom.window)) {
  if (!(key in global) && key !== "global" && key !== "window") {
    try { global[key] = dom.window[key]; } catch (e) {}
  }
}
global.window = dom.window;
global.document = dom.window.document;
global.location = dom.window.location;
global.navigator = dom.window.navigator;
global.screen = dom.window.screen || { width: 1440, height: 900 };
global.localStorage = dom.window.localStorage;
global.sessionStorage = dom.window.sessionStorage;

// SDK 用 Function("return this")() 取全局事件目标（Node → global）
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.attachEvent = () => {};
global.detachEvent = () => {};

if (!dom.window.matchMedia) {
  dom.window.matchMedia = (q) => ({ matches: false, media: q,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
}
if (!dom.window.document.fonts) {
  dom.window.document.fonts = { add() {}, load() { return Promise.resolve([]); }, size: 0 };
}
dom.window.open = () => ({ closed: true, close() {} });
dom.window.BroadcastChannel = class { postMessage() {} close() {} addEventListener() {} };
if (!dom.window.requestAnimationFrame) dom.window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16);
if (!dom.window.cancelAnimationFrame) dom.window.cancelAnimationFrame = clearTimeout;

if (typeof globalThis.fetch === "function") {
  const nf = globalThis.fetch.bind(globalThis);
  global.fetch = nf;
  dom.window.fetch = nf;
}

// XHR = jsdom 原生结构（SDK 依赖完整形态）；send 接管为 Node 真实请求 + 浏览器式请求头
const JXHR = dom.window.XMLHttpRequest;
{
  const proto = JXHR.prototype;
  const origOpen = proto.open;
  const origSet = proto.setRequestHeader;
  proto.open = function (m, url) { this.__xurl = String(url); this.__xmethod = m; return origOpen.apply(this, arguments); };
  proto.setRequestHeader = function (k, v) { (this.__xh = this.__xh || {})[k] = v; return origSet.apply(this, arguments); };
  proto.send = function (body) {
    const u = new URL(this.__xurl || "");
    const mod = u.protocol === "https:" ? https : http;
    const req = mod.request({
      hostname: u.hostname, port: u.port || (u.protocol === "https:" ? 443 : 80),
      path: u.pathname + u.search, method: this.__xmethod || "GET",
      headers: Object.assign({
        "User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.ximalaya.com", "Referer": PAGE,
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
      }, this.__xh || {}),
      rejectUnauthorized: false,
    }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => {
        try { Object.defineProperty(this, "status", { value: res.statusCode, configurable: true }); } catch (e) {}
        try { Object.defineProperty(this, "responseText", { value: data, configurable: true }); } catch (e) {}
        try { Object.defineProperty(this, "response", { value: data, configurable: true }); } catch (e) {}
        try { Object.defineProperty(this, "readyState", { value: 4, configurable: true }); } catch (e) {}
        try {
          const j = JSON.parse(data);
          const oid = j && j.data && j.data.openid;
          if (oid && String(oid).length > 10) {
            try { fs.writeFileSync(OUT, String(oid)); } catch (e) {}
            console.log("OPENID=" + oid);
            process.exit(0);
          }
        } catch (e) {}
        try { if (this.onreadystatechange) this.onreadystatechange(); } catch (e) {}
        try { if (this.onload) this.onload(); } catch (e) {}
      });
    });
    req.on("error", (e) => { console.error("NETERR:", e.message.slice(0, 100)); process.exit(4); });
    if (body) req.write(body);
    req.end();
  };
}
global.XMLHttpRequest = JXHR;
try { require(ATS_PATH); } catch (e) { console.error("ats SDK 加载失败:", e.message.slice(0, 200)); process.exit(6); }
const ats = dom.window.$ats || global.$ats;
if (!ats || !ats.getOpenId) { console.error("SDK 未挂载 $ats"); process.exit(7); }
try {
  if (ats.init) ats.init({ channelId: "xmweb_www", channelEnable: false, adEnable: false,
                           gameEnable: false, domain: "https://www.ximalaya.com/xuid-web-fireeyes" });
} catch (e) { console.error("init:", e.message.slice(0, 120)); }

const timer = setTimeout(() => { console.error("取号超时"); process.exit(2); }, TIMEOUT_MS);
const p = ats.getOpenId();
Promise.resolve(p).then((openId) => {
  clearTimeout(timer);
  if (openId && String(openId).length > 10) {
    try { fs.writeFileSync(OUT, String(openId)); } catch (e) {}
    console.log("OPENID=" + openId);
    process.exit(0);
  }
  process.exit(3);
}).catch((e) => { clearTimeout(timer); console.error("getOpenId:", String(e).slice(0, 200)); process.exit(1); });
