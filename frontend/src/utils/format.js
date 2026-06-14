export const TASK_STATUS_TEXT = {
  queued: '排队中',
  running: '下载中',
  paused: '已暂停',
  stopping: '停止中',
  stopped: '已停止',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  interrupted: '已中断',
  downloaded: '已下载',
  pending: '等待中',
  missing: '缺失',
  skipped: '已跳过',
};

export function taskStatusText(status) {
  return TASK_STATUS_TEXT[status] || status || '未知';
}

export function chapterId(chapter, fallback = '') {
  return String(
    chapter?.id ||
      chapter?.track_id ||
      chapter?.trackId ||
      chapter?.chapter_id ||
      chapter?.chapterId ||
      chapter?.acid ||
      chapter?.cid ||
      chapter?.audioId ||
      chapter?.audio_id ||
      chapter?.item_id ||
      chapter?.program_id ||
      chapter?.title ||
      chapter?.name ||
      fallback ||
      '',
  );
}

export function chapterTitle(chapter) {
  return chapter?.title || chapter?.name || chapter?.chapter_title || '未知章节';
}

export function coverOf(item) {
  const data = item || {};
  const album = data.album || {};
  return (
    data.cover ||
    album.cover ||
    data.cover_url ||
    album.cover_url ||
    data.coverUrl ||
    album.coverUrl ||
    data.coverPath ||
    album.coverPath ||
    data.albumCover ||
    album.albumCover ||
    data.albumCoverUrl ||
    album.albumCoverUrl ||
    data.pic ||
    album.pic ||
    data.picUrl ||
    album.picUrl ||
    data.image ||
    album.image ||
    data.imageUrl ||
    album.imageUrl ||
    data.img ||
    album.img ||
    data.imgPath ||
    album.imgPath ||
    data.hts_img ||
    album.hts_img ||
    data.albumpic ||
    album.albumpic ||
    data.albumPic ||
    album.albumPic ||
    data.thumb_url ||
    album.thumb_url ||
    ''
  );
}

export function albumEpisodeText(album) {
  const count = Number(
    album?.episodes ||
      album?.chapter_count ||
      album?.track_count ||
      album?.tracks_count ||
      album?.total_chapters ||
      0,
  );
  return count > 0 ? `${count}章` : '章节数待加载';
}

export function fmtDuration(seconds) {
  const value = Number.parseInt(seconds, 10) || 0;
  if (!value) return '';
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const sec = String(value % 60).padStart(2, '0');
  if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${sec}`;
  return `${minutes}:${sec}`;
}
