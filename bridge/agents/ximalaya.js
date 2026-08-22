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
let lastLoginStage = 'idle';

function loginStage(stage, detail) {
    lastLoginStage = stage;
    send({type: 'login-stage', stage: stage, detail: detail ? asText(detail) : ''});
}

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
    let fallbackActivity = null;
    for (let i = 0; i < activities.size(); i += 1) {
        const record = activities.valueAt(i);
        const pausedField = record.getClass().getDeclaredField('paused');
        pausedField.setAccessible(true);
        const activityField = record.getClass().getDeclaredField('activity');
        activityField.setAccessible(true);
        const activity = Java.cast(activityField.get(record), Java.use('androidx.fragment.app.FragmentActivity'));
        if (!pausedField.getBoolean(record)) return activity;
        if (!fallbackActivity) fallbackActivity = activity;
    }
    // Headless ReDroid can keep the task paused even though the Activity is
    // valid and the login SDK only needs it as a lifecycle owner/context.
    if (fallbackActivity) return fallbackActivity;
    // ReDroid may restart without restoring a foreground task. Wake the official
    // app so the next login call has the FragmentActivity required by its SDK.
    const application = ActivityThread.currentApplication();
    if (application) {
        const intent = application.getPackageManager().getLaunchIntentForPackage('com.ximalaya.ting.android');
        if (intent) {
            const Intent = Java.use('android.content.Intent');
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK.value);
            application.startActivity(intent);
            throw new Error('已自动打开喜马拉雅 App，请等待几秒后重试');
        }
    }
    throw new Error('喜马拉雅 App 当前没有可用页面，且无法自动打开 App');
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
function loginTimeout(promise, action, timeoutMessage) {
    return new Promise(function (resolve, reject) {
        let settled = false;
        const timer = setTimeout(function () {
            if (!settled) {
                settled = true;
                reject(new Error(timeoutMessage || (
                    action + '超时，请确认喜马拉雅 App 已停留在可见页面后重试'
                )));
            }
        }, 15000);
        promise.then(function (value) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            resolve(value);
        }, function (error) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            reject(error);
        });
    });
}

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
    Object.keys(values).forEach(function (key) {
        const value = values[key];
        map.put(key, value === null || value === undefined ? null : String(value));
    });
    return map;
}

function appSendSms(phone) {
    return loginTimeout(new Promise(function (resolve, reject) {
        Java.perform(function () {
            try {
                loginStage('sms-environment');
                const env = loginEnvironment();
                const callback = loginCallback(
                    function (value) {
                        try {
                            loginStage('sms-callback-success');
                            // The official send-code endpoint returns BaseResponse
                            // (ret/msg), not the bizKey used by the final login call.
                            // Treating Object.toString() as bizKey corrupts the next
                            // request and can make a valid SMS code look expired.
                            const BaseResponse = Java.use(
                                'com.ximalaya.ting.android.loginservice.BaseResponse'
                            );
                            const sent = Java.cast(value, BaseResponse);
                            const ret = Number(sent.getRet());
                            const message = asText(sent.getMsg());
                            if (ret !== 0) {
                                throw new Error(
                                    (message || '发送验证码失败') + '（SDK 返回码 ' + ret + '）'
                                );
                            }
                            resolve({ok: true, message: '验证码已发送'});
                        } catch (error) { reject(error); }
                    },
                    function (code, message) {
                        loginStage('sms-callback-error', code);
                        reject(new Error(
                            (message ? message + '（SDK 错误码 ' + code + '）' :
                                ('发送验证码失败：' + code))
                        ));
                    }
                );
                Java.scheduleOnMainThread(function () {
                    try {
                        loginStage('sms-official-request-start');
                        const activity = currentFragmentActivity();
                        const params = javaMap({mobile: phone, sendType: 1});
                        // Follow SmsLoginFragment -> BaseLoginFragment exactly.
                        // The Activity overload obtains both the nonce and the
                        // fdsOtp risk token before it sends the SMS.  Bypassing
                        // that step can still deliver a text message, but the
                        // verify endpoint rejects its code as expired (31010).
                        env.request.a.overload(
                            'androidx.fragment.app.FragmentActivity',
                            'int',
                            'com.ximalaya.ting.android.loginservice.base.d',
                            'java.util.Map',
                            'com.ximalaya.ting.android.loginservice.base.a'
                        ).call(
                            env.request, activity, 1, env.provider, params, callback
                        );
                        loginStage('sms-official-request-returned');
                    } catch (error) {
                        loginStage('sms-official-request-exception', error);
                        reject(error);
                    }
                });
            } catch (error) {
                loginStage('sms-exception', error);
                reject(error);
            }
        });
    }), '发送验证码',
    '验证码尚未发送：喜马拉雅 App 正在等待人机验证，请先在 App 中完成验证后重试');
}

function appSmsLogin(phone, code) {
    return loginTimeout(new Promise(function (resolve, reject) {
        Java.perform(function () {
            Java.scheduleOnMainThread(function () {
                try {
                    captured = {};
                    loginStage('sms-verify-start');
                    const env = loginEnvironment();
                    const verifyCallback = loginCallback(function (response) {
                        try {
                            loginStage('sms-verify-success');
                            const VerifySmsResponse = Java.use('com.ximalaya.ting.android.loginservice.model.VerifySmsResponse');
                            const verified = Java.cast(response, VerifySmsResponse);
                            const ret = Number(verified.getRet());
                            const verifyMessage = asText(verified.getMsg());
                            if (ret !== 0) {
                                throw new Error(
                                    (verifyMessage || '验证码校验失败') + '（SDK 返回码 ' + ret + '）'
                                );
                            }
                            const smsKey = asText(verified.getBizKey());
                            if (!smsKey) throw new Error('验证码校验成功但未返回 smsKey');
                            loginStage('sms-final-login-start');
                            const loginCallbackInstance = loginCallback(function () {
                                loginStage('sms-final-login-success');
                                resolve({ok: true, message: '移动端登录成功'});
                            }, function (errorCode, message) {
                                loginStage('sms-final-login-error', errorCode);
                                reject(new Error(
                                    (message ? message + '（SDK 错误码 ' + errorCode + '）' :
                                        ('登录失败：' + errorCode))
                                ));
                            });
                            // The ordinary official phone-login flow supplies a null
                            // verify_bizKey and the smsKey returned by VerifySmsResponse.
                            // HashMap must receive Java null here, not the string "null".
                            env.request.f.overload(
                                'com.ximalaya.ting.android.loginservice.base.d', 'java.util.Map',
                                'com.ximalaya.ting.android.loginservice.base.a'
                            ).call(env.request, env.provider, javaMap({bizKey: null, smsKey: smsKey}), loginCallbackInstance);
                        } catch (error) { reject(error); }
                    }, function (errorCode, message) {
                        loginStage('sms-verify-error', errorCode);
                        reject(new Error(
                            (message ? message + '（SDK 错误码 ' + errorCode + '）' :
                                ('验证码错误：' + errorCode))
                        ));
                    });
                    env.request.d.overload(
                        'com.ximalaya.ting.android.loginservice.base.d', 'java.util.Map',
                        'com.ximalaya.ting.android.loginservice.base.a'
                    ).call(env.request, env.provider, javaMap({mobile: phone, code: code}), verifyCallback);
                    loginStage('sms-verify-request-returned');
                } catch (error) { reject(error); }
            });
        });
    }), '验证码登录');
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
                    shuzilm_guard: shuzilmGuardInstalled,
                    login_stage: lastLoginStage
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
