'use strict';

const EXPECTED_VERSION = '9.4.52.3';
const REQUEST_CLASS = 'com.ximalaya.ting.android.host.manager.request.CommonRequestM';
const PACKAGE_NAME = 'com.ximalaya.ting.android';
const CACHE_FILE_NAME = 'audioflow_bridge_headers.json';
let ready = false;
let initializing = false;
let lastError = '';
let appVersion = '';
let captured = {};
let pangleGuardInstalled = false;
let shuzilmGuardInstalled = false;
const nativeGuardState = {};
let credentialCacheFile = null;

function loadCapturedCache(context) {
    try {
        const File = Java.use('java.io.File');
        const Files = Java.use('java.nio.file.Files');
        const JString = Java.use('java.lang.String');
        credentialCacheFile = File.$new(context.getFilesDir(), CACHE_FILE_NAME);
        if (!credentialCacheFile.exists()) {
            return;
        }
        const content = JString.$new(
            Files.readAllBytes(credentialCacheFile.toPath()),
            'UTF-8'
        ).toString();
        const cached = JSON.parse(content);
        const restored = {};
        ['cookie', 'user_agent', 'accept_language', 'api_device', 'host'].forEach(function (key) {
            if (cached && cached[key]) {
                restored[key] = asText(cached[key]);
            }
        });
        if (restored.cookie) {
            captured = restored;
        }
    } catch (error) {
        // A corrupt or obsolete cache must never prevent the App from starting.
    }
}

function persistCapturedCache() {
    if (!credentialCacheFile || !captured.cookie) {
        return;
    }
    const FileOutputStream = Java.use('java.io.FileOutputStream');
    const JString = Java.use('java.lang.String');
    const output = FileOutputStream.$new(credentialCacheFile, false);
    try {
        const bytes = JString.$new(JSON.stringify(captured)).getBytes('UTF-8');
        output.write.overload('[B').call(output, bytes);
        output.flush();
    } finally {
        output.close();
    }
}

function installPangleCrashGuard() {
    if (pangleGuardInstalled && shuzilmGuardInstalled) {
        return;
    }
    Java.perform(function () {
        const guardClasses = [
            'com.bytedance.sdk.openadsdk.core.cg$1',
            'com.bytedance.sdk.openadsdk.core.cg$2',
            'cn.shuzilm.core.m'
        ];
        guardClasses.forEach(function (className) {
            if (nativeGuardState[className]) {
                return;
            }
            try {
                // These advertising/device-fingerprint workers load ARM64
                // anti-fraud libraries that either abort or permanently block
                // Runtime.loadLibrary on ReDroid x86_64.
                const Initializer = Java.use(className);
                const run = Initializer.run.overload();
                run.implementation = function () {
                    return;
                };
                nativeGuardState[className] = true;
            } catch (error) {
                // The application class loader may not be ready yet.
            }
        });
        pangleGuardInstalled = !!nativeGuardState['com.bytedance.sdk.openadsdk.core.cg$1']
            && !!nativeGuardState['com.bytedance.sdk.openadsdk.core.cg$2'];
        shuzilmGuardInstalled = !!nativeGuardState['cn.shuzilm.core.m'];
    });
}

function asText(value) {
    return value === null || value === undefined ? '' : String(value);
}

function captureRequest(request) {
    try {
        const url = asText(request.url().toString());
        if (url.indexOf('/mobile-playpage/track/v4/baseInfo/') < 0) {
            return;
        }
        const headers = request.headers();
        const parsedDevice = /[?&]device=(android2?|ios)(?:&|$)/i.exec(url);
        captured = {
            cookie: asText(headers.get('Cookie')),
            user_agent: asText(headers.get('User-Agent')),
            accept_language: asText(headers.get('Accept-Language')),
            api_device: parsedDevice ? parsedDevice[1].toLowerCase() : 'android',
            host: asText(request.url().host())
        };
        persistCapturedCache();
    } catch (error) {
        lastError = '捕获 App 请求头失败：' + error;
    }
}

function initialize() {
    if (ready || initializing) {
        return;
    }
    initializing = true;
    Java.perform(function () {
        try {
            installPangleCrashGuard();
            const context = Java.use('android.app.ActivityThread').currentApplication().getApplicationContext();
            loadCapturedCache(context);
            appVersion = asText(context.getPackageManager().getPackageInfo(PACKAGE_NAME, 0).versionName.value);
            if (appVersion !== EXPECTED_VERSION) {
                throw new Error('App 版本 ' + appVersion + ' 不受支持，需要 ' + EXPECTED_VERSION);
            }

            const RequestManager = Java.use(REQUEST_CLASS);
            RequestManager.getTicket.overload('int');

            // Capture the final network request after OkHttp's BridgeInterceptor
            // has added Cookie/User-Agent. Keep a Builder fallback for variants
            // whose internal interceptor has been repackaged.
            let captureHookInstalled = false;
            try {
                const BridgeInterceptor = Java.use('okhttp3.internal.http.BridgeInterceptor');
                const intercept = BridgeInterceptor.intercept.overload('okhttp3.Interceptor$Chain');
                intercept.implementation = function (chain) {
                    const response = intercept.call(this, chain);
                    captureRequest(response.request());
                    return response;
                };
                captureHookInstalled = true;
            } catch (bridgeHookError) {
                // Fall through to the public Request.Builder hook below.
            }
            try {
                const Builder = Java.use('okhttp3.Request$Builder');
                const build = Builder.build.overload();
                build.implementation = function () {
                    const request = build.call(this);
                    captureRequest(request);
                    return request;
                };
                captureHookInstalled = true;
            } catch (hookError) {
                if (!captureHookInstalled) {
                    lastError = '未自动捕获 Cookie（可在 AudioFlow 保存一次 App 请求头）：' + hookError;
                }
            }
            lastError = '';
            ready = true;
        } catch (error) {
            ready = false;
            lastError = asText(error);
        } finally {
            initializing = false;
        }
    });
}

installPangleCrashGuard();
const pangleGuardTimer = setInterval(function () {
    installPangleCrashGuard();
    if (pangleGuardInstalled && shuzilmGuardInstalled) {
        clearInterval(pangleGuardTimer);
    }
}, 100);
setImmediate(initialize);
const initializeTimer = setInterval(function () {
    initialize();
    if (ready) {
        clearInterval(initializeTimer);
    }
}, 200);

rpc.exports = {
    status: function () {
        return new Promise(function (resolve) {
            Java.perform(function () {
                resolve({
                    ready: ready,
                    error: lastError,
                    app_version: appVersion,
                    captured_cookie: !!captured.cookie,
                    pangle_guard: pangleGuardInstalled,
                    shuzilm_guard: shuzilmGuardInstalled
                });
            });
        });
    },
    ticket: function () {
        return new Promise(function (resolve, reject) {
            Java.perform(function () {
                try {
                    if (!ready) {
                        throw new Error(lastError || '取票模块尚未就绪');
                    }
                    const RequestManager = Java.use(REQUEST_CLASS);
                    const ticket = asText(RequestManager.getTicket.overload('int').call(RequestManager, 1));
                    if (!ticket) {
                        throw new Error('getTicket(1) 返回空值，请确认喜马拉雅已登录');
                    }
                    resolve(Object.assign({x_tk: ticket}, captured));
                } catch (error) {
                    reject(asText(error));
                }
            });
        });
    }
};
