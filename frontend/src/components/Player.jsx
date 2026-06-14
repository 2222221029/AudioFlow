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

export function useMediaSession(app) {
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
  if (!player.show) return null;
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
  if (mobile) {
    return (
      <div className="mini-player show">
        <div className="mp-cover" style={player.cover ? {backgroundImage: `url("${player.cover}")`} : undefined} />
        <div className="mp-info"><div className="mp-title">{player.title}</div><div className="mp-sub">{player.sub}</div></div>
        <button className="mp-btn" onClick={() => app.actions.playAdjacentChapter?.(-1)} title="上一章"><Icon id="i-arrow-left" /></button>
        <button className="mp-btn" onClick={togglePlayback} title={player.playing ? '暂停' : '播放'}><Icon id={player.playing ? 'i-pause' : 'i-play'} /></button>
        <button className="mp-btn" onClick={() => app.actions.playAdjacentChapter?.(1)} title="下一章"><Icon id="i-arrow-right" /></button>
        <button className="mp-btn close" onClick={close}><Icon id="i-close" /></button>
        <audio ref={audioRef} src={player.url} preload="metadata" playsInline />
      </div>
    );
  }
  return (
    <div className="mini-player show">
      <div className="mini-cover" style={player.cover ? {backgroundImage: `url("${player.cover}")`} : undefined} />
      <div className="mini-info"><div className="mini-title">{player.title}</div><div className="mini-sub">{player.sub}</div></div>
      <button className="mini-btn" onClick={() => app.actions.playAdjacentChapter?.(-1)} title="上一章"><Icon id="i-arrow-left" /></button>
      <button className="mini-btn primary" onClick={togglePlayback} title={player.playing ? '暂停' : '播放'}><Icon id={player.playing ? 'i-pause' : 'i-play'} /></button>
      <button className="mini-btn" onClick={() => app.actions.playAdjacentChapter?.(1)} title="下一章"><Icon id="i-arrow-right" /></button>
      <button className="mini-btn close" onClick={close}><Icon id="i-close" /></button>
      <audio ref={audioRef} src={player.url} preload="metadata" playsInline />
    </div>
  );
}
