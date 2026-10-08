// 喜马拉雅接口与音质档位的唯一来源（Shared.jsx 与 useAudioFlowApp.js 共用）。
// 注意：以下 value 里刻意不出现“无损”字样 —— 后端的
// _is_ximalaya_lossless_quality() 是「文本包含『无损』」判定，PC 的 256K
// 只是客户端标称，并非真无损母带，不能被当成无损档处理。

export const XMLY_MOBILE_INTERFACE = '喜马拉雅移动端接口（自动最高音质）';
export const XMLY_WEB_INTERFACE = '喜马拉雅网页版接口';
export const XMLY_PC_INTERFACE = '喜马拉雅电脑版接口（自动最高音质）';
// 用户主动选择走网页播放器通道（v3/baseInfo），FHQ 无损母带只在那里。
export const XMLY_WEB_LOSSLESS = '网页无损优先（FHQ WAV）';

// 电脑版档位：只需要网页登录态即可取址（设备号与 xm-sign 都在本地生成，
// 不需要 App 票据或 Frida）。
export const XMLY_PC_QUALITIES = ['PC 256K', 'PC 128K', 'PC 64K', 'PC 24K'];

// 默认下载/订阅音质（系统设置里的“默认音质”）。
export const DEFAULT_QUALITY = 'M4A 96K';