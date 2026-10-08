import {useEffect, useMemo} from 'react';
import {Icon} from './Icons.jsx';

const DEFAULT_ARTWORK = [
  {src: '/assets/branding/logos/audioflow-mark.svg', sizes: '512x512', type: 'image/svg+xml'},
  {src: '/pwa/icon-192.png', sizes: '192x192', type: 'image/png'},
  {src: '/pwa/icon-512.png', sizes: '512x512', type: 'image/png'},
];

function playerArtwork(cover) {
  if (!cover) return DEFAULT_ARTWORK;
  return [
    {src: cover, sizes: '512x512', type: 'image/png'},
    ...DEFAULT_ARTWORK,
  ];
}

function safeHandler(action, handler) {
  try {
    navigator.mediaSession.setActionHandler(action, handler);
  } catch {
    // Some iOS/Safari versions expose Media Session partially.
  }
}

function useMediaSession(app) {
  const {player, audioRef, actions} = app;
  const metadata = useMemo(() => ({
    title: player.title || 'AudioFlow',
    artist: player.artist || player.author || player.sub || 'AudioFlow',
    album: player.album || player.sub || '有声书',
    artwork: playerArtwork(player.cover),
  }), [player.album, player.artist, player.author, player.cover, player.sub, player.title]);

  useEffect(() => {
    if (!('mediaSession' in navigator) || !player.show) return undefined;
    navigator.mediaSession.metadata = new MediaMetadata(metadata);
    navigator.mediaSession.playbackState = player.playing ? 'playing' : 'paused';
    const audio = audioRef.current;
    const seekBy = (seconds) => {
      if (!audio || !Number.isFinite(audio.duration)) return;
      audio.currentTime = Math.max(0, Math.min(audio.duration, audio.currentTime + seconds));
    };
    safeHandler('play', () => audio?.play?.().catch((error) => actions.showToast?.(`播放失败：${error.message || '浏览器拒绝播放'}`, 'err')));
    safeHandler('pause', () => audio?.pause?.());
    safeHandler('previoustrack', () => actions.playAdjacentChapter?.(-1));
    safeHandler('nexttrack', () => actions.playAdjacentChapter?.(1));
    safeHandler('seekbackward', (details) => seekBy(-(details.seekOffset || 15)));
    safeHandler('seekforward', (details) => seekBy(details.seekOffset || 30));
    safeHandler('seekto', (details) => {
      if (audio && typeof details.seekTime === 'number') audio.currentTime = details.seekTime;
    });
    return () => {
      for (const action of ['play', 'pause', 'previoustrack', 'nexttrack', 'seekbackward', 'seekforward', 'seekto']) {
        safeHandler(action, null);
      }
    };
  }, [actions, audioRef, metadata, player.playing, player.show]);

  useEffect(() => {
    if ('mediaSession' in navigator && player.show) {
      navigator.mediaSession.playbackState = player.playing ? 'playing' : 'paused';
    }
  }, [player.playing, player.show]);
}

export function MiniPlayer({app, mobile = false}) {
  const {player, setPlayer, audioRef} = app;
  useMediaSession(app);

  // 播放条固定在视口右下/底部：展开时给页面留出底部空间，避免遮住
  // 章节列表最后一行的操作按钮（桌面端此前缺失该补偿）。
  useEffect(() => {
    const root = document.body;
    if (player.show) root.classList.add('player-open');
    else root.classList.remove('player-open');
    return () => root.classList.remove('player-open');
  }, [player.show]);

  const togglePlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (player.playing) audio.pause();
    else audio.play?.().catch((error) => app.actions.showToast?.(`播放失败：${error.message || '浏览器拒绝播放'}`, 'err'));
  };
  const close = () => {
    audioRef.current?.pause?.();
    setPlayer((prev) => ({...prev, show: false, playing: false}));
  };

  // ⚠ <audio> 只保留一份且始终挂载：此前 mobile/desktop 各写一份，
  //   切换外壳（桌面↔移动）会重建元素并中断正在播放的音频。
  return (
    <>
      <audio ref={audioRef} src={player.url || undefined} preload="metadata" playsInline />
      {player.show && (
        <div className={mobile ? 'mini-player show' : 'mini-player show mini-player-desktop'}>
          <div className={mobile ? 'mp-cover' : 'mini-cover'} style={player.cover ? {backgroundImage: `url("${player.cover}")`} : undefined} />
          <div className={mobile ? 'mp-info' : 'mini-info'}>
            <div className={mobile ? 'mp-title' : 'mini-title'}>{player.title}</div>
            <div className={mobile ? 'mp-sub' : 'mini-sub'}>{player.sub}</div>
          </div>
          <button className={`${mobile ? 'mp-btn' : 'mini-btn'} prev`} onClick={() => app.actions.playAdjacentChapter?.(-1)} title="上一章" aria-label="上一章"><Icon id="i-arrow-left" /></button>
          <button className={`${mobile ? 'mp-btn' : 'mini-btn'} primary`} onClick={togglePlayback} title={player.playing ? '暂停' : '播放'} aria-label={player.playing ? '暂停' : '播放'}><Icon id={player.playing ? 'i-pause' : 'i-play'} /></button>
          <button className={`${mobile ? 'mp-btn' : 'mini-btn'}`} onClick={() => app.actions.playAdjacentChapter?.(1)} title="下一章" aria-label="下一章"><Icon id="i-arrow-right" /></button>
          <button className={`${mobile ? 'mp-btn' : 'mini-btn'} close`} onClick={close} aria-label="关闭播放器" title="关闭播放器"><Icon id="i-close" /></button>
        </div>
      )}
    </>
  );
}
