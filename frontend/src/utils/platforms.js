export const SEARCH_PLATFORMS = [
  {value: 'all', label: '全部平台'},
  {value: '喜马拉雅', label: '喜马拉雅'},
  {value: '懒人听书', label: '懒人听书'},
  {value: '番茄畅听', label: '番茄畅听'},
  {value: '番茄听书', label: '番茄听书'},
  {value: '网易云听书', label: '网易云听书'},
  {value: '荔枝FM', label: '荔枝FM'},
  {value: '七猫听书', label: '七猫听书'},
  {value: '蜻蜓FM', label: '蜻蜓FM'},
  {value: '云听FM', label: '云听FM'},
  {value: '起点听书', label: '起点听书'},
  {value: '酷我听书', label: '酷我听书'},
];

export const COOKIE_PLATFORMS = [
  {key: 'xmly', name: '喜马拉雅', qr: 'ximalaya'},
  {key: 'lrts', name: '懒人听书', qr: 'lrts'},
  {key: 'qidian', name: '起点听书', qr: 'qidian'},
  {key: 'qtfm', name: '蜻蜓FM', qr: 'qtfm'},
  {key: 'netease', name: '网易云听书'},
  {key: 'lizhi', name: '荔枝FM'},
  {key: 'fanqie', name: '番茄畅听'},
  {key: 'fanqie_tingshu', name: '番茄听书'},
  {key: 'qimao', name: '七猫听书'},
  {key: 'yuntu', name: '云听FM'},
  {key: 'kuwo', name: '酷我听书'},
];

export const NO_COOKIE_KEYS = ['fanqie', 'fanqie_tingshu', 'qimao', 'yuntu', 'kuwo', 'lizhi'];

export const PLATFORM_COOKIE_KEY = {
  喜马拉雅: 'xmly',
  懒人听书: 'lrts',
  起点听书: 'qidian',
  蜻蜓FM: 'qtfm',
  番茄畅听: 'fanqie',
  番茄听书: 'fanqie_tingshu',
  七猫听书: 'qimao',
  云听FM: 'yuntu',
  酷我听书: 'kuwo',
  网易云听书: 'netease',
  荔枝FM: 'lizhi',
  xmly: 'xmly',
  lrts: 'lrts',
  qidian: 'qidian',
  qtfm: 'qtfm',
  fanqie: 'fanqie',
  fanqie_tingshu: 'fanqie_tingshu',
  qimao: 'qimao',
  yuntu: 'yuntu',
  kuwo: 'kuwo',
  netease: 'netease',
  lizhi: 'lizhi',
};

export const PLATFORM_LOGOS = {
  xmly: '/platform-logos/xmly.ico',
  lrts: '/platform-logos/lrts.ico',
  qidian: '/platform-logos/qidian.ico',
  qtfm: '/platform-logos/qtfm.ico',
  fanqie: '/platform-logos/fanqie.png',
  fanqie_tingshu: '/platform-logos/fanqie.png',
  qimao: '/platform-logos/qimao.png',
  yuntu: '/platform-logos/yuntu.ico',
  kuwo: '/platform-logos/kuwo.ico',
  netease: '/platform-logos/netease.ico',
  lizhi: '/platform-logos/lizhi.svg',
};

export const PERSONAL_FEATURES = {
  ximalaya: [
    {key: 'history', name: '收听记录', icon: 'i-clock'},
    {key: 'liked', name: '我的喜欢', icon: 'i-heart'},
    {key: 'subscriptions', name: '我的订阅', icon: 'i-bookmark'},
    {key: 'purchased', name: '已购专辑', icon: 'i-download'},
  ],
  lrts: [
    {key: 'history', name: '收听记录', icon: 'i-clock'},
    {key: 'favorites', name: '我的收藏', icon: 'i-bookmark'},
    {key: 'programs', name: '我的节目', icon: 'i-user'},
  ],
  qidian: [{key: 'favorites', name: '我的书架', icon: 'i-bookmark'}],
};

export function platformKey(value) {
  return PLATFORM_COOKIE_KEY[value] || value || '';
}

export function platformLogo(value) {
  return PLATFORM_LOGOS[platformKey(value)];
}
