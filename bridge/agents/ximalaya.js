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

function currentFragmentActivity() {
    const ActivityThread = Java.use('android.app.ActivityThread');
    const thread = ActivityThread.currentActivityThread();
    const activitiesField = thread.getClass().getDeclaredField('mActivities');
    activitiesField.setAccessible(true);
    const ArrayMap = Java.use('android.util.ArrayMap');
    const activities = Java.cast(activitiesField.get(thread), ArrayMap);
    for (let i = 0; i < activities.size(); i += 1) {
        const record = activities.valueAt(i);
        const pausedField = record.getClass().getDeclaredField('paused');
        pausedField.setAccessible(true);
        if (!pausedField.getBoolean(record)) {
            const activityField = record.getClass().getDeclaredField('activity');
            activityField.setAccessible(true);
            return Java.cast(activityField.get(record), Java.use('androidx.fragment.app.FragmentActivity'));
        }
    }
    throw new Error('喜马拉雅 App 当前没有可用页面，请先打开 App');
}

function loginEnvironment() {
    const LoginManager = Java.use('com.ximalaya.ting.android.loginservice.j');
    const manager = LoginManager.a.overload().call(LoginManager);
    const provider = LoginManager.c.overload().call(manager);
    return {
        request: Java.use('com.ximalaya.ting.android.loginservice.LoginRequest'),
        provider: provider,
        callback: Java.use('com.ximalaya.ting.android.loginservice.base.a')
    };
}

let loginCallbackSequence = 0;
const smsBizKeys = {};
function loginCallback(onSuccess, onError) {
    const env = loginEnvironment();
    loginCallbackSequence += 1;
    const Callback = Java.registerClass({
        name: 'com.audioflow.bridge.LoginCallback' + loginCallbackSequence,
        implements: [env.callback],
        methods: {
            a: [
                {
                    returnType: 'void',
                    argumentTypes: ['java.lang.Object'],
                    implementation: function (value) { onSuccess(value); }
                },
                {
                    returnType: 'void',
                    argumentTypes: ['int', 'java.lang.String'],
                    implementation: function (code, message) { onError(code, asText(message)); }
                }
            ]
        }
    });
    return Callback.$new();
}

function javaMap(values) {
    const HashMap = Java.use('java.util.HashMap');
    const map = HashMap.$new();
    Object.keys(values).forEach(function (key) { map.put(key, String(values[key])); });
    return map;
}

function appSendSms(phone) {
    return new Promise(function (resolve, reject) {
        Java.perform(function () {
            try {
                const env = loginEnvironment();
                const callback = loginCallback(
                    function (value) {
                        const bizKey = asText(value);
                        if (!bizKey) throw new Error('验证码已发送但未返回业务密钥');
                        smsBizKeys[phone] = bizKey;
                        resolve({ok: true, message: '验证码已发送'});
                    },
                    function (code, message) { reject(new Error(message || ('发送验证码失败：' + code))); }
                );
                env.request.a.overload(
                    'androidx.fragment.app.FragmentActivity', 'int',
                    'com.ximalaya.ting.android.loginservice.base.d', 'java.util.Map',
                    'com.ximalaya.ting.android.loginservice.base.a'
                ).call(env.request, currentFragmentActivity(), 5, env.provider,
                    javaMap({mobile: phone, sendType: 1}), callback);
            } catch (error) { reject(error); }
        });
    });
}

function appSmsLogin(phone, code) {
    return new Promise(function (resolve, reject) {
        Java.perform(function () {
            try {
                captured = {};
                const env = loginEnvironment();
                const verifyCallback = loginCallback(function (response) {
                    try {
                        const VerifySmsResponse = Java.use('com.ximalaya.ting.android.loginservice.model.VerifySmsResponse');
                        const verified = Java.cast(response, VerifySmsResponse);
                        const smsKey = asText(verified.getBizKey());
                        const bizKey = asText(smsBizKeys[phone]);
                        if (!smsKey || !bizKey) throw new Error('验证码校验成功但未返回登录密钥');
                        const loginCallbackInstance = loginCallback(function () {
                            delete smsBizKeys[phone];
                            resolve({ok: true, message: '移动端登录成功'});
                        }, function (errorCode, message) {
                            reject(new Error(message || ('登录失败：' + errorCode)));
                        });
                        env.request.f.overload(
                            'com.ximalaya.ting.android.loginservice.base.d', 'java.util.Map',
                            'com.ximalaya.ting.android.loginservice.base.a'
                        ).call(env.request, env.provider, javaMap({bizKey: bizKey, smsKey: smsKey}), loginCallbackInstance);
                    } catch (error) { reject(error); }
                }, function (errorCode, message) {
                    reject(new Error(message || ('验证码错误：' + errorCode)));
                });
                env.request.d.overload(
                    'com.ximalaya.ting.android.loginservice.base.d', 'java.util.Map',
                    'com.ximalaya.ting.android.loginservice.base.a'
                ).call(env.request, env.provider, javaMap({mobile: phone, code: code}), verifyCallback);
            } catch (error) { reject(error); }
        });
    });
}

function captureRequest(request) {
    try {
        const url = asText(request.url().toString());
        const headers = request.headers();
        const cookie = asText(headers.get('Cookie'));
        const isBaseInfo = url.indexOf('/mobile-playpage/track/v4/baseInfo/') >= 0;
        const hasLoginCookie = /(?:^|;\s*)1&(?:\*|_)token=[^;]*&[^;]+/i.test(cookie);
        if (!isBaseInfo && !hasLoginCookie) {
            return;
        }
        const parsedDevice = /[?&]device=(android2?|ios)(?:&|$)/i.exec(url);
        captured = {
            cookie: cookie,
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
    },
    smssend: function (phone) {
        if (!ready) return Promise.reject(lastError || '取票模块尚未就绪');
        return appSendSms(asText(phone));
    },
    smslogin: function (phone, code) {
        if (!ready) return Promise.reject(lastError || '取票模块尚未就绪');
        return appSmsLogin(asText(phone), asText(code));
    }
};
