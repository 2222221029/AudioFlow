// LibraryPage.jsx — 书库管理（书库扫描 + 刮削写标签 + 智能重命名 统一入口）
import {useEffect, useRef, useState} from 'react';
import {api} from '../services/api.js';
import {Icon} from './Icons.jsx';

// ─── 常量 ────────────────────────────────────────────────────────────────────

const TABS = [
  ['rename',    'i-edit',     '章节重命名'],
  ['scrape',    'i-tag',      '刮削写标签'],
  ['templates', 'i-file',     '模板管理'],
  ['history',   'i-clock',    '历史回滚'],
  ['settings',  'i-settings', '系统设置'],
];

const S = {
  input: {width:'100%',background:'var(--panel-hi)',border:'1px solid var(--border)',borderRadius:9,padding:'0 12px',height:34,color:'var(--text)',fontSize:13,outline:'none',boxSizing:'border-box',fontFamily:'inherit'},
  select: {width:'100%',background:'var(--panel-hi)',border:'1px solid var(--border)',borderRadius:9,padding:'0 12px',height:34,color:'var(--text)',fontSize:13,outline:'none',fontFamily:'inherit'},
  textarea: {width:'100%',background:'var(--panel-hi)',border:'1px solid var(--border)',borderRadius:9,padding:'8px 12px',color:'var(--text)',fontSize:12.5,outline:'none',boxSizing:'border-box',fontFamily:'Consolas,monospace',resize:'vertical'},
  label: {fontSize:11,color:'var(--text-faint)',marginBottom:5,fontWeight:600,display:'block',textTransform:'uppercase',letterSpacing:'.5px'},
};

function fmtSize(bytes){
  if(!bytes)return'0 B';
  if(bytes>=1024**3)return(bytes/1024**3).toFixed(1)+' GB';
  if(bytes>=1024**2)return(bytes/1024**2).toFixed(1)+' MB';
  if(bytes>=1024)return(bytes/1024).toFixed(1)+' KB';
  return bytes+' B';
}

function applyAdRules(title,rules){
  const defaults=[/www\.\S+\.\S+/gi,/https?:\/\/\S+/gi,/[QqＱＱ]{1,2}[:：]?\d{5,}/gi,/微信[:：]?\s*\S+/gi,/公众号[:：]?\s*\S+/gi,/\(.*?听书.*?\)/gi,/【.*?听书.*?】/gi,/【.*?下载.*?】/gi];
  let s=title;
  for(const p of defaults)s=s.replace(p,'');
  for(const r of(rules||[])){try{s=s.replace(new RegExp(r,'gi'),'');}catch(e){}}
  return s.replace(/\s+/g,' ').trim();
}

function simulateTemplate(tpl,meta,fileName,idx,adRules){
  const stem=fileName.replace(/\.[^.]+$/,'');
  const ext=(fileName.match(/\.([^.]+)$/)||['',''])[1].toLowerCase();
  const i=idx+1;
  const aiTitle=(meta.chapter_titles||{})[fileName];
  let chapterTitle=aiTitle||stem;
  if(!aiTitle){
    // 无AI结果时基础提取：去掉"序号-书名"前缀 + 章节编号
    let s=stem;
    const bt=(meta.book_title||'').trim();
    if(bt) s=s.replace(new RegExp('^\\d+[-\\s]+'+bt.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'[-\\s]*'),'').trim();
    s=s.replace(/^\d+[集章回话期]?\s*/,'').trim();
    chapterTitle=applyAdRules(s||stem, adRules);
  }
  chapterTitle=chapterTitle.replace(/\s+（/g,'（');
  const prefixMatch=stem.match(/^(\d+)/);
  const originalPrefix=prefixMatch?prefixMatch[1]:String(i).padStart(4,'0');
  const seriesVal=(meta.series||'').trim();
  const seriesBlock=seriesVal?`-【${seriesVal}】-`:'';
  const vars={book_title:meta.book_title||'',author:meta.author||'',narrator:meta.narrator||'',
    category:meta.category||'',series:meta.series||'',volume:meta.volume||'',
    original_prefix:originalPrefix,series_block:seriesBlock,
    chapter_index:String(i),chapter_index_2:String(i).padStart(2,'0'),
    chapter_index_3:String(i).padStart(3,'0'),chapter_index_4:String(i).padStart(4,'0'),
    chapter_title:chapterTitle,chapter_full:String(i).padStart(3,'0')+'-'+chapterTitle,
    name:stem,ext,date:new Date().toISOString().slice(0,10).replace(/-/g,'')};
  let r=tpl;
  for(const[k,v]of Object.entries(vars))r=r.replaceAll(`{${k}}`,v);
  return r;
}

// ─── 标签输入组件 ─────────────────────────────────────────────────────────────

function TagInput({tags,onChange}){
  const[input,setInput]=useState('');
  function add(raw){
    const parts=raw.split(/[,，]+/).map(s=>s.trim()).filter(Boolean);
    const next=[...new Set([...tags,...parts])];
    onChange(next);setInput('');
  }
  function remove(t){onChange(tags.filter(x=>x!==t));}
  return(
    <div style={{display:'flex',flexWrap:'wrap',gap:5,padding:'6px 10px',border:'1px solid var(--border)',borderRadius:9,background:'var(--panel-hi)',minHeight:36,alignItems:'center'}}>
      {tags.map(t=>(
        <span key={t} style={{display:'inline-flex',alignItems:'center',gap:3,background:'rgba(99,102,241,.15)',color:'var(--primary)',fontSize:12,padding:'2px 8px',borderRadius:99,whiteSpace:'nowrap'}}>
          {t}
          <button onClick={()=>remove(t)} style={{background:'none',border:'none',color:'inherit',cursor:'pointer',padding:'0 0 0 2px',lineHeight:1,fontSize:13}}>×</button>
        </span>
      ))}
      <input value={input} onChange={e=>setInput(e.target.value)}
        onKeyDown={e=>{if(e.key==='Enter'||e.key===','){e.preventDefault();if(input.trim())add(input);}
                       if(e.key==='Backspace'&&!input&&tags.length)onChange(tags.slice(0,-1));}}
        onBlur={()=>{if(input.trim())add(input);}}
        placeholder={tags.length?'':'输入标签后按 Enter…'}
        style={{border:'none',outline:'none',background:'transparent',color:'var(--text)',fontSize:12.5,minWidth:80,flex:1}}/>
    </div>
  );
}

// ─── 共用文件夹浏览器 Modal ──────────────────────────────────────────────────

function FileBrowserModal({data,onNav,onSelect,onClose}){
  return(
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',backdropFilter:'blur(6px)',WebkitBackdropFilter:'blur(6px)',zIndex:1000,display:'flex',alignItems:'center',justifyContent:'center',padding:'16px'}}
      onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div style={{background:'var(--panel-2)',borderRadius:'var(--radius-card)',width:'100%',maxWidth:560,maxHeight:'min(72vh,580px)',display:'flex',flexDirection:'column',overflow:'hidden',boxShadow:'0 24px 60px rgba(0,0,0,.55)',border:'1px solid var(--border-hi)'}}>
        <div style={{padding:'16px 18px',borderBottom:'1px solid var(--border)',display:'flex',alignItems:'center',justifyContent:'space-between',flexShrink:0}}>
          <span style={{fontWeight:700,fontSize:15,display:'flex',alignItems:'center',gap:8}}>
            <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)'}}/>
            选择专辑文件夹
          </span>
          <button className="btn-icon sm" onClick={onClose}><Icon id="i-close" className="icon icon-sm"/></button>
        </div>
        {data?(
          <>
            <div style={{padding:'7px 18px',fontSize:11,color:'var(--text-faint)',borderBottom:'1px solid var(--border)',fontFamily:'Consolas,monospace',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flexShrink:0,background:'var(--panel)'}}>
              {data.current}
            </div>
            <div style={{flex:1,overflow:'auto',padding:6}}>
              {(data.items||[]).map(item=>(
                <button key={item.path}
                  style={{width:'100%',display:'flex',alignItems:'center',gap:9,padding:'9px 12px',borderRadius:10,cursor:'pointer',fontSize:13,
                    color:item.has_audio?'var(--text)':'var(--text-mute)',background:'none',border:'none',textAlign:'left'}}
                  onMouseEnter={e=>e.currentTarget.style.background='var(--panel-hi)'}
                  onMouseLeave={e=>e.currentTarget.style.background='none'}
                  onClick={()=>onNav(item.path)}>
                  <Icon id="i-folder" className="icon icon-sm" style={{color:item.has_audio?'var(--primary)':'var(--text-faint)',flexShrink:0}}/>
                  <span style={{flex:1,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{item.name}</span>
                  {item.has_audio&&<span style={{fontSize:10,color:'var(--success)',background:'rgba(52,211,153,.15)',padding:'2px 8px',borderRadius:99,flexShrink:0,fontWeight:600}}>有音频</span>}
                  <Icon id="i-arrow-right" className="icon icon-sm" style={{opacity:.3,flexShrink:0}}/>
                </button>
              ))}
              {(data.items||[]).length===0&&<div style={{padding:36,textAlign:'center',color:'var(--text-mute)',fontSize:13}}>此目录下没有子文件夹</div>}
            </div>
            <div style={{padding:'12px 18px',borderTop:'1px solid var(--border)',display:'flex',gap:8,justifyContent:'flex-end',flexShrink:0,background:'var(--panel)'}}>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
              <button className="btn btn-primary btn-sm" onClick={()=>onSelect(data.current)}>
                <Icon id="i-check" className="icon icon-sm"/>选择此目录
              </button>
            </div>
          </>
        ):(
          <div style={{padding:56,textAlign:'center',color:'var(--text-faint)',display:'flex',flexDirection:'column',alignItems:'center',gap:10}}>
            <span className="loading" style={{width:20,height:20}}/>
            <span style={{fontSize:13}}>加载中...</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── 主组件 ──────────────────────────────────────────────────────────────────

export function LibraryPage(){
  const[tab,setTab]=useState('rename');
  const[folder,setFolder]=useState('');       // 当前选中专辑文件夹（共享）
  const[browserOpen,setBrowserOpen]=useState(false);
  const[browserData,setBrowserData]=useState(null);

  async function openBrowser(path=''){
    setBrowserData(null);
    setBrowserOpen(true);
    const r=await api(`/api/meta/browse?path=${encodeURIComponent(path||'')}`);
    if(r.ok)setBrowserData(r.browser);
  }

  async function navBrowser(path){
    setBrowserData(null);
    const r=await api(`/api/meta/browse?path=${encodeURIComponent(path)}`);
    if(r.ok)setBrowserData(r.browser);
  }

  function selectFolder(path){
    setFolder(path);
    setBrowserOpen(false);
  }

  return(
    <div style={{display:'flex',flexDirection:'column',height:'100%',gap:0}}>
      {/* 已选文件夹 Bar */}
      <div style={{display:'flex',alignItems:'center',gap:8,padding:'9px 14px',borderBottom:'1px solid var(--border)',background:'var(--panel)',flexShrink:0,minHeight:50}}>
        <div style={{width:30,height:30,borderRadius:8,background:'var(--primary-soft,rgba(124,182,255,.14))',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0}}>
          <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)'}}/>
        </div>
        <span style={{flex:1,fontSize:12.5,color:folder?'var(--text)':'var(--text-faint)',fontFamily:folder?'Consolas,monospace':'inherit',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {folder||'未选择专辑文件夹 — 点击右侧「浏览」按钮'}
        </span>
        {folder&&<button className="btn btn-ghost btn-sm" onClick={()=>setFolder('')} style={{flexShrink:0,fontSize:12}}>清除</button>}
        <button className="btn btn-primary btn-sm" onClick={()=>openBrowser(folder)} style={{flexShrink:0}}>
          <Icon id="i-folder" className="icon icon-sm"/>浏览
        </button>
      </div>

      {/* Tab 栏 */}
      <div style={{display:'flex',gap:0,borderBottom:'1px solid var(--border)',flexShrink:0,overflowX:'auto',background:'var(--panel)',scrollbarWidth:'none'}}>
        {TABS.map(([id,icon,label])=>(
          <button key={id} onClick={()=>setTab(id)}
            style={{display:'flex',alignItems:'center',gap:5,padding:'11px 15px',background:'none',border:'none',
              borderBottom:tab===id?'2px solid var(--primary)':'2px solid transparent',
              color:tab===id?'var(--primary)':'var(--text-mute)',
              fontWeight:tab===id?600:400,fontSize:13,whiteSpace:'nowrap',cursor:'pointer',transition:'color .15s'}}>
            <Icon id={icon} className="icon icon-sm"/>{label}
          </button>
        ))}
      </div>

      {/* 内容区 */}
      <div style={{flex:1,overflow:'auto',padding:'16px'}}>
        {tab==='rename'    && <RenameTab selectedFolder={folder} onBrowse={()=>openBrowser(folder)} onFolderChange={setFolder} onGotoHistory={()=>setTab('history')} onGotoScrape={()=>setTab('scrape')}/>}
        {tab==='scrape'    && <ScrapeTab selectedFolder={folder} onBrowse={()=>openBrowser(folder)} onFolderChange={setFolder}/>}
        {tab==='templates' && <TemplatesTab/>}
        {tab==='history'   && <HistoryTab/>}
        {tab==='settings'  && <SettingsTab/>}
      </div>

      {browserOpen&&<FileBrowserModal data={browserData} onNav={navBrowser} onSelect={selectFolder} onClose={()=>setBrowserOpen(false)}/>}
    </div>
  );
}

// ─── Tab 1：刮削写标签 ────────────────────────────────────────────────────────

function ScrapeTab({selectedFolder,onBrowse,onFolderChange}){
  const[subTab,setSubTab]=useState('meta');  // meta | params | queue | logs
  const[options,setOptions]=useState(null);
  const[params,setParams]=useState(null);
  const[metaTab,setMetaTab]=useState('id');
  const[apiSource,setApiSource]=useState('喜马拉雅');
  const[apiId,setApiId]=useState('');
  const[linkPlatform,setLinkPlatform]=useState('起点听书');
  const[linkUrl,setLinkUrl]=useState('');
  const[fetchedMeta,setFetchedMeta]=useState(null);
  const[fetchBusy,setFetchBusy]=useState(false);
  const[fetchError,setFetchError]=useState('');
  const[metaStatus,setMetaStatus]=useState(null);
  const[paramsBusy,setParamsBusy]=useState(false);
  const[paramsMsg,setParamsMsg]=useState('');
  const[coverPreview,setCoverPreview]=useState(null); // {url, w, h}
  const evtRef=useRef(null);

  // 加载选项和配置
  useEffect(()=>{
    api('/api/meta/options').then(r=>r.ok&&setOptions(r.options)).catch(()=>{});
    api('/api/meta/config').then(r=>r.ok&&setParams(r.params)).catch(()=>{});
  },[]);

  // selectedFolder 变化时：重新加载默认 params、重置元数据输入、读取 source.json
  useEffect(()=>{
    if(!selectedFolder){
      setApiId('');setLinkUrl('');setFetchedMeta(null);setCoverPreview(null);
      return;
    }
    // 重置元数据输入区
    setApiId('');setLinkUrl('');setFetchedMeta(null);setCoverPreview(null);setFetchError('');
    // 重新从服务器拉取默认 params，并覆盖 input_folder
    api('/api/meta/config').then(r=>{
      if(r.ok) setParams({...(r.params||{}),input_folder:selectedFolder});
    }).catch(()=>{});
    // 尝试读取 source.json
    api(`/api/meta/read-source?path=${encodeURIComponent(selectedFolder)}`).then(r=>{
      if(!r.ok)return;
      const s=r.source||{};
      const src=s.api_source||s.platform||'';
      const id=String(s.album_id||s.id||'');
      if(src) setApiSource(src);
      if(id){setMetaTab('id');setApiId(id);}
      if(s.cover) _saveCoverPreview({cover_url:s.cover});
    }).catch(()=>{});
  },[selectedFolder]);

  // SSE 或轮询获取状态
  useEffect(()=>{
    const poll=setInterval(()=>{
      api('/api/meta/status').then(r=>r.ok&&setMetaStatus(r.status)).catch(()=>{});
    },4000);
    api('/api/meta/status').then(r=>r.ok&&setMetaStatus(r.status)).catch(()=>{});
    return()=>clearInterval(poll);
  },[]);

  function setParam(k,v){setParams(p=>({...p,[k]:v}));}

  function _saveCoverPreview(meta){
    if(meta?.cover_url){
      const img=new Image();
      img.onload=()=>setCoverPreview({url:meta.cover_url,w:img.naturalWidth,h:img.naturalHeight});
      img.onerror=()=>setCoverPreview({url:meta.cover_url,w:0,h:0});
      img.src=meta.cover_url;
    }
  }

  async function doFetchById(){
    if(!apiSource||!apiId.trim())return;
    setFetchBusy(true);setFetchError('');setFetchedMeta(null);
    try{
      const r=await api('/api/meta/fetch-metadata',{method:'POST',body:JSON.stringify({api_source:apiSource,api_id:apiId.trim()})});
      if(r.ok){setFetchedMeta(r.metadata);_saveCoverPreview(r.metadata);}
      else setFetchError(r.error||'获取失败');
    }catch(e){setFetchError(String(e));}
    setFetchBusy(false);
  }

  async function doFetchByLink(){
    if(!linkPlatform||!linkUrl.trim())return;
    setFetchBusy(true);setFetchError('');setFetchedMeta(null);
    try{
      const r=await api('/api/meta/fetch-link',{method:'POST',body:JSON.stringify({platform:linkPlatform,url:linkUrl.trim()})});
      if(r.ok){setFetchedMeta(r.metadata);_saveCoverPreview(r.metadata);}
      else setFetchError(r.error||'获取失败');
    }catch(e){setFetchError(String(e));}
    setFetchBusy(false);
  }

  function applyMetaToParams(){
    if(!fetchedMeta)return;
    setParams(p=>({...p,
      title:fetchedMeta.title||p.title,
      subtitle:fetchedMeta.subtitle||p.subtitle,
      author:fetchedMeta.author||p.author,
      anchor:fetchedMeta.anchor||p.anchor,
      year:fetchedMeta.year||p.year,
      finished:fetchedMeta.finished||p.finished,
      category:fetchedMeta.category||p.category,
      api_source:apiSource,api_id:apiId,
      manual_desc:fetchedMeta.desc||p.manual_desc,
      fetched_metadata:fetchedMeta.raw||{},
      album_tags:fetchedMeta.tags?.length?fetchedMeta.tags:p.album_tags,
    }));
    setSubTab('params');
  }

  async function doRun(){
    if(!params)return;
    setParamsBusy(true);setParamsMsg('');
    try{
      const r=await api('/api/meta/run',{method:'POST',body:JSON.stringify({params})});
      setParamsMsg(r.ok?'任务已启动，可在日志 Tab 查看进度':(r.error||'启动失败'));
      if(r.ok)setSubTab('logs');
    }catch(e){setParamsMsg(String(e));}
    setParamsBusy(false);
  }

  async function doAddQueue(){
    if(!params)return;
    const r=await api('/api/meta/queue/add',{method:'POST',body:JSON.stringify({params})});
    if(r.ok)setSubTab('queue');
  }

  async function doStop(){await api('/api/meta/stop',{method:'POST'});}
  async function doQueueStart(){await api('/api/meta/queue/start',{method:'POST'});}
  async function doQueueClear(){if(confirm('清除队列？'))await api('/api/meta/queue/clear',{method:'POST'});}
  async function doQueueRetry(){await api('/api/meta/queue/retry-failed',{method:'POST'});}

  const queue=metaStatus?.queue||[];
  const logs=metaStatus?.logs||[];
  const running=metaStatus?.running;
  const progress=metaStatus?.progress||0;

  if(!options||!params)return<div style={{padding:32,color:'var(--text-mute)',textAlign:'center'}}><span className="loading"/>　加载中...</div>;

  return(
    <div style={{display:'flex',flexDirection:'column',gap:0}}>
      {/* 子 Tab 栏 */}
      <div style={{display:'flex',gap:0,borderBottom:'1px solid var(--border)',marginBottom:14,alignItems:'center',overflowX:'auto',scrollbarWidth:'none'}}>
        {[['meta','i-search','元数据获取'],['params','i-settings','处理参数'],['queue','i-download','任务队列'],['logs','i-file','处理日志']].map(([id,icon,label])=>(
          <button key={id} onClick={()=>setSubTab(id)}
            style={{background:'none',border:'none',borderBottom:subTab===id?'2px solid var(--primary)':'2px solid transparent',
              color:subTab===id?'var(--primary)':'var(--text-mute)',padding:'9px 14px',
              fontWeight:subTab===id?600:400,fontSize:12.5,cursor:'pointer',display:'flex',alignItems:'center',gap:5,whiteSpace:'nowrap',flexShrink:0}}>
            <Icon id={icon} className="icon icon-sm"/>{label}
          </button>
        ))}
        {running&&(
          <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:8,fontSize:12,color:'var(--primary)'}}>
            <span className="loading" style={{width:14,height:14}}/>{Math.round(progress)}% · {metaStatus?.message}
            <button className="btn btn-danger btn-sm" style={{padding:'2px 8px',minHeight:24}} onClick={doStop}>停止</button>
          </div>
        )}
      </div>

      {running&&<div style={{height:3,background:'var(--border)',marginBottom:14,borderRadius:2}}>
        <div style={{height:'100%',width:`${progress}%`,background:'var(--primary)',transition:'width .3s',borderRadius:2}}/>
      </div>}

      {/* 元数据获取 */}
      {subTab==='meta'&&(
        <div style={{display:'flex',flexDirection:'column',gap:14}}>
          <div className="glass" style={{padding:16}}>
            <div style={{display:'flex',gap:8,marginBottom:12}}>
              <button onClick={()=>setMetaTab('id')} className={metaTab==='id'?'btn btn-primary btn-sm':'btn btn-ghost btn-sm'}>通过 ID 获取</button>
              <button onClick={()=>setMetaTab('link')} className={metaTab==='link'?'btn btn-primary btn-sm':'btn btn-ghost btn-sm'}>通过分享链接</button>
            </div>
            {metaTab==='id'&&(
              <div style={{display:'flex',flexDirection:'column',gap:10}}>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <label style={{...S.label,marginBottom:0,minWidth:42}}>平台</label>
                  <select style={{...S.select,flex:1}} value={apiSource} onChange={e=>setApiSource(e.target.value)}>
                    {(options.api_sources||[]).map(s=><option key={s}>{s}</option>)}
                  </select>
                </div>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <label style={{...S.label,marginBottom:0,minWidth:42}}>专辑 ID</label>
                  <input style={{...S.input,flex:1}} value={apiId} placeholder="输入专辑 ID..."
                    onChange={e=>setApiId(e.target.value)} onKeyDown={e=>e.key==='Enter'&&doFetchById()}/>
                </div>
                <button className="btn btn-primary" disabled={fetchBusy||!apiId.trim()} onClick={doFetchById} style={{alignSelf:'flex-start'}}>
                  {fetchBusy?<span className="loading"/>:<Icon id="i-search" className="icon icon-sm"/>}获取元数据
                </button>
              </div>
            )}
            {metaTab==='link'&&(
              <div style={{display:'flex',flexDirection:'column',gap:10}}>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <label style={{...S.label,marginBottom:0,minWidth:42}}>平台</label>
                  <select style={{...S.select,flex:1}} value={linkPlatform} onChange={e=>setLinkPlatform(e.target.value)}>
                    {(options.link_platforms||[]).map(s=><option key={s}>{s}</option>)}
                  </select>
                </div>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <label style={{...S.label,marginBottom:0,minWidth:42}}>链接</label>
                  <input style={{...S.input,flex:1}} value={linkUrl} placeholder="粘贴分享链接..."
                    onChange={e=>setLinkUrl(e.target.value)} onKeyDown={e=>e.key==='Enter'&&doFetchByLink()}/>
                </div>
                <button className="btn btn-primary" disabled={fetchBusy||!linkUrl.trim()} onClick={doFetchByLink} style={{alignSelf:'flex-start'}}>
                  {fetchBusy?<span className="loading"/>:<Icon id="i-search" className="icon icon-sm"/>}解析链接
                </button>
              </div>
            )}
            {fetchError&&<div style={{marginTop:10,color:'var(--danger)',fontSize:12}}>{fetchError}</div>}
          </div>

          {fetchedMeta&&(
            <div className="glass" style={{padding:16}}>
              <div style={{display:'flex',gap:14,alignItems:'flex-start'}}>
                {fetchedMeta.cover_url&&<img src={fetchedMeta.cover_url} alt="封面"
                  style={{width:96,height:96,objectFit:'cover',borderRadius:8,flexShrink:0}}
                  onError={e=>e.target.style.display='none'}/>}
                <div style={{flex:1,minWidth:0}}>
                  <div style={{fontWeight:700,fontSize:15,marginBottom:5}}>{fetchedMeta.title||'（无标题）'}</div>
                  {fetchedMeta.author&&<div style={{fontSize:13,color:'var(--text-mute)'}}>作者：{fetchedMeta.author}</div>}
                  {fetchedMeta.anchor&&<div style={{fontSize:13,color:'var(--text-mute)'}}>演播：{fetchedMeta.anchor}</div>}
                  {fetchedMeta.year&&<div style={{fontSize:13,color:'var(--text-mute)'}}>年份：{fetchedMeta.year}</div>}
                  {fetchedMeta.category_text&&<div style={{fontSize:13,color:'var(--text-mute)'}}>分类：{fetchedMeta.category_text}</div>}
                  {fetchedMeta.tags?.length>0&&(
                    <div style={{display:'flex',flexWrap:'wrap',gap:5,marginTop:7}}>
                      {fetchedMeta.tags.slice(0,12).map(t=>(
                        <span key={t} style={{background:'rgba(99,102,241,.15)',color:'var(--primary)',fontSize:11,padding:'2px 8px',borderRadius:99,fontWeight:500}}>{t}</span>
                      ))}
                    </div>
                  )}
                  {fetchedMeta.desc&&<div style={{marginTop:8,fontSize:12,color:'var(--text-faint)',lineHeight:1.7,maxHeight:72,overflow:'hidden'}}>{fetchedMeta.desc}</div>}
                </div>
              </div>
              <div style={{marginTop:12}}>
                <button className="btn btn-primary btn-sm" onClick={applyMetaToParams}>
                  <Icon id="i-check" className="icon icon-sm"/>应用到处理参数
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 处理参数 */}
      {subTab==='params'&&(
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          {/* 当前目录提示 */}
          {params.input_folder&&(
            <div style={{fontSize:11.5,color:'var(--text-mute)',padding:'7px 12px',background:'var(--panel-hi)',borderRadius:9,border:'1px solid var(--border)',fontFamily:'Consolas,monospace',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:6}}>
              <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)',flexShrink:0}}/>
              {params.input_folder}
            </div>
          )}
          {/* 基本信息 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>基本信息</div>
            <div style={{display:'flex',gap:14,alignItems:'flex-start'}}>
              {/* 封面预览 */}
              <div style={{flexShrink:0,display:'flex',flexDirection:'column',alignItems:'center',gap:4}}>
                {coverPreview?.url?(
                  <>
                    <img src={coverPreview.url} alt="封面预览"
                      style={{width:120,height:120,objectFit:'cover',borderRadius:8,border:'1px solid var(--border)',display:'block'}}
                      onError={e=>{e.target.style.display='none';}}/>
                    {coverPreview.w>0&&(
                      <span style={{fontSize:10,color:'var(--text-mute)',textAlign:'center',lineHeight:1.4}}>
                        {coverPreview.w}×{coverPreview.h}px<br/>
                        <span style={{color:coverPreview.w>=500?'var(--success)':'var(--warning)'}}>
                          {coverPreview.w>=500?'✓ 高清':'⚠ 低分辨率'}
                        </span>
                      </span>
                    )}
                  </>
                ):(
                  <div style={{width:120,height:120,borderRadius:8,border:'2px dashed var(--border)',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:6,color:'var(--text-faint)',fontSize:11}}>
                    <Icon id="i-folder" className="icon icon-sm" style={{opacity:.4}}/>
                    <span style={{textAlign:'center',lineHeight:1.4}}>刮削后<br/>自动获取</span>
                  </div>
                )}
              </div>
              {/* 字段网格 */}
              <div style={{flex:1,display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:10}}>
                {[['title','专辑标题'],['subtitle','副标题'],['author','原著作者'],['anchor','演播艺术家']].map(([k,l])=>(
                  <div key={k}><label style={S.label}>{l}</label><input style={S.input} value={params[k]||''} onChange={e=>setParam(k,e.target.value)}/></div>
                ))}
                <div><label style={S.label}>发布平台</label>
                  <select style={S.select} value={params.platform} onChange={e=>setParam('platform',e.target.value)}>
                    {(options.platforms||[]).map(p=><option key={p}>{p}</option>)}
                  </select>
                </div>
                <div><label style={S.label}>发布年份</label><input style={S.input} value={params.year||''} onChange={e=>setParam('year',e.target.value)}/></div>
                <div><label style={S.label}>专辑分类</label>
                  <select style={S.select} value={params.category} onChange={e=>setParam('category',e.target.value)}>
                    {(options.categories||[]).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </div>
                <div><label style={S.label}>完结状态</label>
                  <select style={S.select} value={params.finished} onChange={e=>setParam('finished',e.target.value)}>
                    {(options.finished||[]).map(f=><option key={f}>{f}</option>)}
                  </select>
                </div>
              </div>
            </div>
          </div>
          {/* 扩展信息 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>扩展信息</div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10}}>
              <div><label style={S.label}>系列名称</label><input style={S.input} value={params.series_name||''} placeholder="例：斗罗大陆" onChange={e=>setParam('series_name',e.target.value)}/></div>
              <div><label style={S.label}>系列序号</label><input style={S.input} value={params.series_number||''} placeholder="例：1（多部用逗号分隔）" onChange={e=>setParam('series_number',e.target.value)}/></div>
              <div style={{gridColumn:'1/-1'}}>
                <label style={S.label}>专辑标签（按 Enter 或逗号添加）</label>
                <TagInput tags={params.album_tags||[]} onChange={v=>setParam('album_tags',v)}/>
              </div>
              <div><label style={S.label}>团队标识</label><input style={S.input} value={params.team||''} placeholder="例：RL" onChange={e=>setParam('team',e.target.value)}/></div>
              <div><label style={S.label}>封面图片路径（留空自动获取）</label><input style={S.input} value={params.manual_cover_path||''} placeholder="/path/to/cover.jpg" onChange={e=>setParam('manual_cover_path',e.target.value)}/></div>
              <div style={{gridColumn:'1/-1'}}>
                <label style={S.label}>手动简介（留空自动获取）</label>
                <textarea style={{...S.textarea,minHeight:72,fontFamily:'inherit'}} value={params.manual_desc||''} placeholder="留空则自动从 API 或已抓取元数据中提取..." onChange={e=>setParam('manual_desc',e.target.value)}/>
              </div>
            </div>
          </div>
          {/* 音频格式 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>音频处理</div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:10}}>
              <div><label style={S.label}>目标格式</label>
                <select style={S.select} value={params.target_format} onChange={e=>setParam('target_format',e.target.value)}>
                  {(options.target_formats||[]).map(f=><option key={f}>{f}</option>)}
                </select>
              </div>
              <div><label style={S.label}>码率</label>
                <select style={S.select} value={params.bitrate} onChange={e=>setParam('bitrate',e.target.value)}>
                  {(options.bitrates||[]).map(b=><option key={b}>{b}</option>)}
                </select>
              </div>
            </div>
            <div style={{display:'flex',gap:16,marginTop:10,flexWrap:'wrap'}}>
              {[['check_codec','校验音频编码'],['rename_ext','规范文件扩展名'],['debug','详细日志']].map(([k,l])=>(
                <label key={k} style={{display:'flex',alignItems:'center',gap:6,fontSize:13,cursor:'pointer'}}>
                  <input type="checkbox" checked={!!params[k]} onChange={e=>setParam(k,e.target.checked)}/>{l}
                </label>
              ))}
            </div>
          </div>
          {/* 操作 */}
          <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
            <button className="btn btn-primary" onClick={doRun} disabled={paramsBusy||running}>
              {paramsBusy?<span className="loading"/>:<Icon id="i-download" className="icon icon-sm"/>}立即处理
            </button>
            <button className="btn btn-ghost" onClick={doAddQueue} disabled={paramsBusy||running}>
              加入队列
            </button>
            {running&&<button className="btn btn-danger" onClick={doStop}>停止</button>}
            {paramsMsg&&<span style={{fontSize:12.5,color:paramsMsg.includes('失败')?'var(--danger)':'var(--success)'}}>{paramsMsg}</span>}
          </div>
        </div>
      )}

      {/* 任务队列 */}
      {subTab==='queue'&&(
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
            <button className="btn btn-primary btn-sm" onClick={doQueueStart} disabled={running||queue.length===0}>开始队列</button>
            <button className="btn btn-ghost btn-sm" onClick={doQueueRetry}>重试失败</button>
            <button className="btn btn-ghost btn-sm" onClick={doQueueClear}>清空队列</button>
            {running&&<button className="btn btn-danger btn-sm" onClick={doStop}>停止</button>}
          </div>
          {queue.length===0?<div style={{color:'var(--text-mute)',fontSize:13,padding:20,textAlign:'center'}}>队列为空</div>:
            queue.map(item=>{
              const stMap={done:{bg:'rgba(52,211,153,.15)',c:'var(--success)',t:'完成'},failed:{bg:'rgba(239,68,68,.15)',c:'var(--danger)',t:'失败'},processing:{bg:'rgba(99,102,241,.15)',c:'var(--primary)',t:'处理中'}};
              const st=stMap[item.status]||{bg:'var(--panel-hi)',c:'var(--text-mute)',t:item.status||'等待'};
              return(
                <div key={item.id} className="glass" style={{padding:'12px 16px',display:'flex',alignItems:'center',gap:12}}>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{fontWeight:600,fontSize:13.5,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{item.title}</div>
                    <div style={{fontSize:12,color:'var(--text-mute)',marginTop:3}}>{item.author}{item.source&&` · ${item.source}`}</div>
                  </div>
                  <span style={{fontSize:11,padding:'3px 10px',borderRadius:99,background:st.bg,color:st.c,fontWeight:600,flexShrink:0}}>{st.t}</span>
                </div>
              );
            })
          }
        </div>
      )}

      {/* 处理日志 */}
      {subTab==='logs'&&(
        <div style={{display:'flex',flexDirection:'column',gap:10}}>
          <div style={{display:'flex',alignItems:'center',gap:8}}>
            <span style={{fontSize:13,color:'var(--text-mute)'}}>{metaStatus?.message||'等待就绪'}</span>
            {metaStatus?.finished_at&&<span style={{fontSize:12,color:'var(--text-faint)'}}>完成于 {metaStatus.finished_at}</span>}
          </div>
          <div style={{background:'var(--bg-0)',borderRadius:8,border:'1px solid var(--border)',maxHeight:460,overflow:'auto',fontFamily:'monospace',fontSize:12,padding:12}}>
            {logs.length===0?<div style={{color:'var(--text-mute)',padding:8}}>暂无日志</div>:
              [...logs].reverse().map(l=>(
                <div key={l.seq} style={{color:l.level==='error'?'var(--danger)':l.level==='warning'?'var(--warning)':'var(--text-mute)',marginBottom:2,lineHeight:1.5}}>
                  {l.message}
                </div>
              ))
            }
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Tab 2：章节重命名 ────────────────────────────────────────────────────────

// 虚拟列表常量
const ROW_H=40,VLIST_H=460,VBUF=12;

function RenameTab({selectedFolder,onBrowse,onFolderChange,onGotoHistory,onGotoScrape}){
  const[step,setStep]=useState(1);
  const[folderFiles,setFolderFiles]=useState([]);
  const[bookMeta,setBookMeta]=useState({book_title:'',author:'',narrator:'',category:'',series:'',volume:''});
  const[template,setTemplate]=useState('{chapter_index_3}-{chapter_title}.{ext}');
  const[templates,setTemplates]=useState([]);
  const[previews,setPreviews]=useState([]);
  const[overrides,setOverrides]=useState({});  // idx -> 手动改后的文件名
  const[editIdx,setEditIdx]=useState(null);
  const[editVal,setEditVal]=useState('');
  const[scrollTop,setScrollTop]=useState(0);
  const vScrollRef=useRef(null);
  const[note,setNote]=useState('');
  const[loading,setLoading]=useState(false);
  const[error,setError]=useState('');
  const[localStats,setLocalStats]=useState(null);  // {confidence, needsAi:N}
  const[aiLoading,setAiLoading]=useState(false);
  const[aiNormStats,setAiNormStats]=useState(null);
  const[aiProgress,setAiProgress]=useState(null);
  const[fmConfig,setFmConfig]=useState({custom_ad_rules:[]});
  const[scrapeOpen,setScrapeOpen]=useState(false);
  const[scrapeInput,setScrapeInput]=useState({api_source:'喜马拉雅',api_id:'',link_url:'',link_platform:'起点听书'});

  function startEdit(i,name){setEditIdx(i);setEditVal(name);}
  function commitEdit(i){if(editVal.trim())setOverrides(o=>({...o,[i]:editVal.trim()}));setEditIdx(null);}

  // selectedFolder 变化时自动重新加载文件列表，重置到步骤1
  useEffect(()=>{
    if(!selectedFolder){setFolderFiles([]);setStep(1);return;}
    setStep(1);setFolderFiles([]);setError('');setApplyResult(null);setOverrides({});
    setLocalStats(null);setAiNormStats(null);
    loadFolderFiles(selectedFolder);
  },[selectedFolder]);

  useEffect(()=>{
    api('/api/file-manager/templates').then(r=>r.ok&&setTemplates(r.templates)).catch(()=>{});
    api('/api/file-manager/config').then(r=>r.ok&&setFmConfig(r.config||{})).catch(()=>{});
  },[]);

  async function loadFolderFiles(path){
    const target=path||selectedFolder;
    if(!target)return;
    setLoading(true);setError('');
    try{
      const r=await api(`/api/file-manager/scan?root=${encodeURIComponent(target)}`);
      if(!r.ok)throw new Error(r.error||'扫描失败');
      const allFiles=r.books.flatMap(b=>b.files);
      setFolderFiles(allFiles);
      setStep(2);
      // 扫描完成后立即触发本地智能分析（无需 AI，秒级完成）
      doLocalAnalyze(target);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  async function doLocalAnalyze(folderPath){
    try{
      const r=await api('/api/file-manager/local-analyze',{method:'POST',
        body:JSON.stringify({folder_path:folderPath||selectedFolder})});
      if(!r.ok)return;
      const{book_title,chapter_titles,needs_ai,confidence}=r;
      setBookMeta(p=>({...p,
        book_title:book_title||p.book_title,
        chapter_titles:chapter_titles||{},
      }));
      setLocalStats({confidence,needsAi:(needs_ai||[]).length,total:Object.keys(chapter_titles||{}).length});
    }catch(e){}
  }

  async function doAiDeepClean(){
    // AI 深度清理：只处理本地提取置信度低的文件，节省 token
    const allNames=folderFiles.map(f=>f.name);
    // 找出需要 AI 清理的文件：本地未提取到标题、或含广告特征的
    const currentTitles=bookMeta.chapter_titles||{};
    const AD_SUSPECT=/QQ|qq|微信|公众号|http|www\.|听书|下载/;
    const targetNames=allNames.filter(n=>{
      const t=currentTitles[n]||'';
      return !t||t===n.replace(/\.[^.]+$/,'')||AD_SUSPECT.test(t);
    });
    if(!targetNames.length){setError('所有章节标题已提取完整，无需 AI 清理');return;}
    const BATCH=80;
    const batches=[];
    for(let i=0;i<targetNames.length;i+=BATCH)batches.push(targetNames.slice(i,i+BATCH));
    const total=batches.length;
    setAiLoading(true);setError('');setAiNormStats(null);setAiProgress({cur:0,total,samples:[]});
    const chapter_titles={...currentTitles};
    let adCount=0,normCount=0;
    let firstMeta=null;
    try{
      for(let i=0;i<batches.length;i++){
        const r=await api('/api/file-manager/ai-analyze-batch',{method:'POST',
          body:JSON.stringify({file_names:batches[i],is_first_batch:i===0&&!firstMeta})});
        if(!r.ok)throw new Error(r.error);
        const res=r.result||{};
        if(i===0&&!firstMeta)firstMeta=res;
        const samples=[];
        (res.items||[]).forEach(item=>{
          if(!item.original)return;
          if(item.is_ad){adCount++;return;}
          if(item.chapter_title){
            chapter_titles[item.original]=item.chapter_title;
            normCount++;
            if(samples.length<3)samples.push({from:item.original,to:item.chapter_title});
          }
        });
        setAiProgress(p=>({...p,cur:i+1,samples}));
      }
      setBookMeta(p=>({...p,
        ...(firstMeta&&{
          book_title:firstMeta.book_title||p.book_title,
          author:firstMeta.author||p.author,
          narrator:firstMeta.narrator||p.narrator,
          category:firstMeta.category||p.category,
          series:firstMeta.series||p.series,
          volume:firstMeta.volume||p.volume,
        }),
        chapter_titles,
      }));
      setAiNormStats({normalized:normCount,ads:adCount,total:targetNames.length});
    }catch(e){setError('AI 清理失败: '+e.message);}
    finally{setAiLoading(false);setAiProgress(null);}
  }

  async function doScrape(){
    setLoading(true);setError('');
    try{
      const r=await api('/api/file-manager/scrape',{method:'POST',body:JSON.stringify(scrapeInput)});
      if(!r.ok)throw new Error(r.error);
      const m=r.metadata||{};
      setBookMeta(p=>({...p,book_title:m.title||m.book_title||p.book_title,author:m.author||p.author,narrator:m.narrator||m.anchor||p.narrator,category:m.category||p.category}));
      setScrapeOpen(false);
    }catch(e){setError('刮削失败: '+e.message);}
    finally{setLoading(false);}
  }

  async function doPreview(){
    setLoading(true);setError('');
    try{
      const r=await api('/api/file-manager/rename-preview',{method:'POST',body:JSON.stringify({folder_path:selectedFolder,template,book_meta:bookMeta})});
      if(!r.ok)throw new Error(r.error);
      setPreviews(r.previews);setStep(4);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  const[applyResult,setApplyResult]=useState(null);

  async function doApply(){
    setLoading(true);setError('');setApplyResult(null);
    try{
      const sep=previews[0]?.original_path?.includes('\\')?'\\':'/';
      const merged=previews.map((p,i)=>{
        const ov=overrides[i];
        if(!ov)return p;
        const dir=p.original_path.substring(0,p.original_path.lastIndexOf(sep)+1);
        return{...p,new_name:ov,new_path:dir+ov,conflict:false};
      });
      const r=await api('/api/file-manager/rename-apply',{method:'POST',body:JSON.stringify({previews:merged,note})});
      if(!r.ok)throw new Error(r.error);
      setApplyResult({success:r.success,failed:r.failed});
      onFolderChange(selectedFolder);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  const livePreview=folderFiles.slice(0,3).map((f,i)=>simulateTemplate(template,bookMeta,f.name,i,fmConfig.custom_ad_rules));

  if(!selectedFolder){
    return(
      <div className="glass glass-pad" style={{color:'var(--text-mute)',textAlign:'center',padding:'60px 20px',fontSize:14,display:'flex',flexDirection:'column',alignItems:'center',gap:14}}>
        <div style={{width:64,height:64,borderRadius:18,background:'var(--panel-hi)',display:'flex',alignItems:'center',justifyContent:'center'}}>
          <Icon id="i-folder" style={{width:32,height:32,opacity:.4,color:'var(--primary)'}}/>
        </div>
        <div>
          <div style={{fontWeight:600,marginBottom:4}}>尚未选择文件夹</div>
          <div style={{fontSize:13,color:'var(--text-faint)'}}>请点击顶部「浏览」按钮选择专辑文件夹</div>
        </div>
      </div>
    );
  }

  return(
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      {/* 步骤条 */}
      <div style={{display:'flex',alignItems:'center',gap:0,flexWrap:'nowrap',overflowX:'auto',scrollbarWidth:'none'}}>
        {['填写元数据','选择模板','预览确认'].map((label,i)=>{
          const stp=i+2;
          const isActive=step===stp;
          const isDone=stp<step;
          return(
            <div key={i} style={{display:'flex',alignItems:'center',flexShrink:0}}>
              <button onClick={()=>stp<=step&&setStep(stp)}
                style={{display:'flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:999,fontSize:12.5,
                  cursor:stp<=step?'pointer':'default',border:'none',
                  background:isActive?'linear-gradient(135deg,var(--aurora-a),var(--aurora-b))':isDone?'var(--panel-hi)':'transparent',
                  color:isActive?'#fff':isDone?'var(--text)':'var(--text-faint)',
                  boxShadow:isActive?'0 4px 14px -4px rgba(99,102,241,.55)':'none'}}>
                <span style={{width:18,height:18,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',flexShrink:0,
                  background:isActive?'rgba(255,255,255,.25)':isDone?'var(--success)':'var(--border)',
                  color:isDone?'#fff':isActive?'#fff':'var(--text-faint)',fontSize:10,fontWeight:700}}>
                  {isDone?'✓':i+1}
                </span>
                <span style={{whiteSpace:'nowrap'}}>{label}</span>
              </button>
              {i<2&&<div style={{width:20,height:1,background:'var(--border)',flexShrink:0}}/>}
            </div>
          );
        })}
        {loading&&<span style={{marginLeft:10,fontSize:12,color:'var(--text-mute)',display:'flex',alignItems:'center',gap:5,flexShrink:0}}><span className="loading" style={{width:12,height:12}}/>扫描中...</span>}
      </div>

      {error&&<Err>{error}</Err>}

      {/* 加载中/无文件 */}
      {step===1&&!loading&&(
        <div style={{color:'var(--text-mute)',fontSize:13,padding:20,textAlign:'center'}}>
          正在扫描文件夹... 若未自动加载，<button className="btn btn-ghost btn-sm" onClick={()=>loadFolderFiles()}>点击重试</button>
        </div>
      )}

      {/* 步骤2+3 */}
      {step>=2&&step<=3&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            <div style={{fontWeight:600,fontSize:14}}>填写书籍元数据</div>
            <div style={{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
              {localStats&&!aiLoading&&(
                <span style={{fontSize:11,padding:'2px 8px',borderRadius:10,
                  background:localStats.confidence>=0.8?'rgba(34,197,94,.15)':'rgba(234,179,8,.15)',
                  color:localStats.confidence>=0.8?'var(--success)':'#ca8a04'}}>
                  {localStats.confidence>=0.8?'✓':'~'} 本地提取 {localStats.total-localStats.needsAi}/{localStats.total}
                  {localStats.needsAi>0&&<span style={{opacity:.7}}> · {localStats.needsAi}个待清理</span>}
                </span>
              )}
              <button className="btn btn-ghost btn-sm" onClick={doAiDeepClean} disabled={aiLoading}
                title={localStats&&localStats.needsAi>0?`对 ${localStats.needsAi} 个疑似广告/空标题文件调用 AI 清理`:'对含广告特征章节调用 AI 清理'}>
                {aiLoading?<span className="loading"/>:<Icon id="i-bolt" className="icon icon-sm"/>}
                {aiLoading?`AI 清理中 ${aiProgress?aiProgress.cur:0}/${aiProgress?aiProgress.total:1} 批`:'AI 深度清理'}
              </button>
              {aiNormStats&&!aiLoading&&<span style={{fontSize:11,color:'var(--success)'}}>
                ✓ 清理 {aiNormStats.normalized}/{aiNormStats.total}{aiNormStats.ads>0&&`，跳过广告 ${aiNormStats.ads}`}
              </span>}
              {aiProgress&&aiLoading&&(
                <div style={{fontSize:11,color:'var(--text-mute)',display:'flex',flexDirection:'column',gap:3,marginLeft:4}}>
                  <div style={{display:'flex',alignItems:'center',gap:6}}>
                    <div style={{width:100,height:4,background:'var(--bg-1)',borderRadius:2,overflow:'hidden'}}>
                      <div style={{width:`${aiProgress.total?Math.round(aiProgress.cur/aiProgress.total*100):0}%`,height:'100%',background:'var(--primary)',transition:'width .3s'}}/>
                    </div>
                    <span>{aiProgress.total?Math.round(aiProgress.cur/aiProgress.total*100):0}%</span>
                  </div>
                  {aiProgress.samples&&aiProgress.samples.map((s,i)=>(
                    <div key={i} style={{fontSize:10.5,color:'var(--text-faint)',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',maxWidth:340}}>
                      <span style={{color:'var(--text-mute)'}}>{s.from.replace(/\.[^.]+$/,'')}</span>
                      <span style={{margin:'0 4px',color:'var(--primary)'}}>→</span>
                      <span style={{color:'var(--success)'}}>{s.to}</span>
                    </div>
                  ))}
                </div>
              )}
              <button className="btn btn-ghost btn-sm" onClick={()=>setScrapeOpen(!scrapeOpen)}>
                <Icon id="i-search" className="icon icon-sm"/>从刮削获取
              </button>
            </div>
          </div>
          {scrapeOpen&&(
            <div style={{background:'var(--bg-1)',borderRadius:8,padding:12,display:'flex',flexDirection:'column',gap:8}}>
              <div style={{fontSize:13,fontWeight:500}}>刮削元数据</div>
              <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
                <input placeholder="平台（如：喜马拉雅）" value={scrapeInput.api_source}
                  onChange={e=>setScrapeInput(p=>({...p,api_source:e.target.value}))}
                  style={{flex:1,minWidth:120,...S.input,width:'auto'}}/>
                <input placeholder="专辑 ID" value={scrapeInput.api_id}
                  onChange={e=>setScrapeInput(p=>({...p,api_id:e.target.value}))}
                  style={{flex:1,minWidth:120,...S.input,width:'auto'}}/>
              </div>
              <input placeholder="或直接粘贴分享链接 URL" value={scrapeInput.link_url}
                onChange={e=>setScrapeInput(p=>({...p,link_url:e.target.value}))}
                style={S.input}/>
              <button className="btn btn-primary btn-sm" onClick={doScrape} disabled={loading} style={{alignSelf:'flex-start'}}>
                {loading?<span className="loading"/>:'获取元数据'}
              </button>
            </div>
          )}
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10}}>
            {[['book_title','书名'],['author','作者'],['narrator','主播'],['category','分类'],['series','系列'],['volume','卷号']].map(([k,l])=>(
              <div key={k}>
                <label style={S.label}>{l}</label>
                <input value={bookMeta[k]} onChange={e=>setBookMeta(p=>({...p,[k]:e.target.value}))}
                  placeholder={l} style={S.input}/>
              </div>
            ))}
          </div>
          {step===2&&<button className="btn btn-primary" onClick={()=>setStep(3)} style={{alignSelf:'flex-start'}}>
            下一步：选择模板 →
          </button>}
        </div>
      )}

      {/* 步骤3 */}
      {step===3&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{fontWeight:600,fontSize:14}}>选择重命名模板</div>
          <div>
            <label style={S.label}>选择预设模板</label>
            <select style={S.select} value={template} onChange={e=>setTemplate(e.target.value)}>
              {templates.map(t=><option key={t.id} value={t.template}>{t.name} — {t.template}</option>)}
            </select>
          </div>
          <div>
            <label style={S.label}>自定义模板</label>
            <input value={template} onChange={e=>setTemplate(e.target.value)}
              placeholder="{chapter_index_3}-{chapter_title}.{ext}" style={{...S.input,fontFamily:'monospace'}}/>
          </div>
          <div style={{background:'var(--panel-hi)',borderRadius:9,padding:'10px 12px',border:'1px solid var(--border)'}}>
            <div style={{fontSize:11,color:'var(--text-faint)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:6}}>可用变量</div>
            <div style={{display:'flex',flexWrap:'wrap',gap:5}}>
              {['{original_prefix}','{book_title}','{series_block}','{author}','{narrator}','{chapter_index_3}','{chapter_title}','{ext}','{date}'].map(v=>(
                <code key={v} style={{fontSize:11.5,padding:'2px 7px',borderRadius:6,background:'var(--pre-bg)',border:'1px solid var(--border)',fontFamily:'Consolas,monospace',color:'var(--primary)',cursor:'pointer'}}
                  onClick={()=>setTemplate(t=>t+v)}>{v}</code>
              ))}
            </div>
            <div style={{fontSize:11,color:'var(--text-faint)',marginTop:6,lineHeight:1.6}}>
              {'{original_prefix}'} = 原文件前缀数字（0001）· {'{series_block}'} = 有系列时输出 -【名称】- 否则为空 · 点击变量可插入
            </div>
          </div>
          {folderFiles.length>0&&(
            <div style={{background:'var(--panel-hi)',borderRadius:9,padding:12,border:'1px solid var(--border)'}}>
              <div style={{fontSize:11,color:'var(--text-faint)',fontWeight:700,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:8}}>实时预览（前3个文件）</div>
              {livePreview.map((name,i)=>(
                <div key={i} style={{display:'grid',gridTemplateColumns:'1fr auto 1fr',gap:8,alignItems:'center',fontSize:12,marginBottom:i<2?6:0}}>
                  <span style={{color:'var(--text-mute)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{folderFiles[i]?.name}</span>
                  <span style={{color:'var(--text-faint)',fontSize:10,flexShrink:0}}>→</span>
                  <span style={{color:'var(--primary)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{name}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-ghost" onClick={()=>setStep(2)}>上一步</button>
            <button className="btn btn-primary" onClick={doPreview} disabled={loading||!template}>
              {loading?<span className="loading"/>:'生成完整预览'}
            </button>
          </div>
        </div>
      )}

      {/* 步骤4 */}
      {step===4&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:6}}>
            <div style={{fontWeight:600,fontSize:14}}>预览确认（共 {previews.length} 个文件{Object.keys(overrides).length>0&&`，已手动编辑 ${Object.keys(overrides).length} 个`}）</div>
            <span style={{fontSize:11,color:'var(--text-mute)'}}>点击新文件名可手动编辑 · Enter 确认 · Esc 取消</span>
          </div>
          {/* 虚拟列表 */}
          {(()=>{
            const vStart=Math.max(0,Math.floor(scrollTop/ROW_H)-VBUF);
            const vEnd=Math.min(previews.length,Math.ceil((scrollTop+VLIST_H)/ROW_H)+VBUF);
            return(
              <div style={{border:'1px solid var(--border)',borderRadius:6,overflow:'hidden'}}>
                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 60px',background:'var(--panel-hi)',padding:'6px 10px',fontSize:11,color:'var(--text-faint)',fontWeight:700,borderBottom:'1px solid var(--border)',textTransform:'uppercase',letterSpacing:'.5px'}}>
                  <span>原文件名</span><span>新文件名（点击编辑）</span><span>状态</span>
                </div>
                <div ref={vScrollRef} style={{height:Math.min(previews.length*ROW_H,VLIST_H),overflowY:'auto'}}
                  onScroll={e=>setScrollTop(e.currentTarget.scrollTop)}>
                  <div style={{height:previews.length*ROW_H,position:'relative'}}>
                    <div style={{position:'absolute',top:vStart*ROW_H,left:0,right:0}}>
                      {previews.slice(vStart,vEnd).map((p,offset)=>{
                        const i=vStart+offset;
                        const ov=overrides[i];
                        const displayName=ov||p.new_name;
                        const isEditing=editIdx===i;
                        const conflict=!ov&&p.conflict;
                        const unchanged=p.original_name===displayName;
                        const aiTagged=(()=>{const stem=p.original_name.replace(/\.[^.]+$/,'');const nt=(bookMeta.chapter_titles||{})[p.original_name];return nt&&nt!==stem;})();
                        return(
                          <div key={i} style={{display:'grid',gridTemplateColumns:'1fr 1fr 60px',
                            height:ROW_H,alignItems:'center',padding:'0 10px',gap:6,
                            borderBottom:'1px solid var(--border)',
                            background:conflict?'rgba(239,68,68,.06)':i%2===0?'transparent':'rgba(0,0,0,.02)'}}>
                            <div style={{fontSize:12,color:'var(--text-mute)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'flex',alignItems:'center',gap:4}}>
                              <span style={{overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.original_name}</span>
                              {aiTagged&&<span style={{flexShrink:0,fontSize:9,background:'rgba(99,102,241,.18)',color:'var(--primary)',padding:'1px 5px',borderRadius:99,fontWeight:700}}>AI</span>}
                            </div>
                            {isEditing?(
                              <input autoFocus value={editVal}
                                onChange={e=>setEditVal(e.target.value)}
                                onBlur={()=>commitEdit(i)}
                                onKeyDown={e=>{if(e.key==='Enter')commitEdit(i);if(e.key==='Escape')setEditIdx(null);}}
                                style={{...S.input,fontSize:12,padding:'3px 7px',height:28}}/>
                            ):(
                              <div onClick={()=>startEdit(i,displayName)}
                                title="点击编辑"
                                style={{fontSize:12,color:conflict?'var(--danger)':ov?'var(--warning)':'var(--primary)',
                                  cursor:'text',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',
                                  borderRadius:4,padding:'3px 6px',border:'1px solid transparent'}}
                                onMouseEnter={e=>{e.currentTarget.style.borderColor='var(--border)';e.currentTarget.style.background='var(--bg-0)';}}
                                onMouseLeave={e=>{e.currentTarget.style.borderColor='transparent';e.currentTarget.style.background='';}}>
                                {displayName}
                                {ov&&<span style={{fontSize:10,marginLeft:5,opacity:.7}}>✎</span>}
                              </div>
                            )}
                            <div style={{fontSize:11}}>
                              {conflict?<Tag c="danger">冲突</Tag>:unchanged?<Tag c="mute">未变</Tag>:<Tag c="success">正常</Tag>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}
          <div>
            <label style={S.label}>操作备注（可选）</label>
            <input value={note} onChange={e=>setNote(e.target.value)} placeholder="记录此次操作目的..." style={S.input}/>
          </div>
          {applyResult?(
            <div style={{background:'rgba(52,211,153,.08)',border:'1px solid rgba(52,211,153,.3)',borderRadius:12,padding:'16px 18px'}}>
              <div style={{fontWeight:700,color:'var(--success)',fontSize:15,marginBottom:6,display:'flex',alignItems:'center',gap:7}}>
                <span style={{width:24,height:24,borderRadius:'50%',background:'var(--success)',display:'flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:13,flexShrink:0}}>✓</span>
                重命名完成 — 成功 {applyResult.success} 个{applyResult.failed>0&&`，失败 ${applyResult.failed} 个`}
              </div>
              <div style={{fontSize:12.5,color:'var(--text-mute)',marginBottom:14,marginLeft:31}}>
                章节文件已规范化，现在可以进行元数据刮削写标签操作。
              </div>
              <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                <button className="btn btn-primary" onClick={onGotoScrape}>
                  <Icon id="i-tag" className="icon icon-sm"/>前往刮削写标签
                </button>
                <button className="btn btn-ghost btn-sm" onClick={onGotoHistory}>查看历史记录</button>
                <button className="btn btn-ghost btn-sm" onClick={()=>{setStep(1);setApplyResult(null);}}>重新操作</button>
              </div>
            </div>
          ):(
            <div style={{display:'flex',gap:8}}>
              <button className="btn btn-ghost" onClick={()=>setStep(3)}>上一步</button>
              <button className="btn btn-primary" onClick={doApply} disabled={loading}>
                {loading?<span className="loading"/>:<Icon id="i-check" className="icon icon-sm"/>}执行重命名
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Tab 4：模板管理 ──────────────────────────────────────────────────────────

const DEFAULT_TEMPLATES=[
  {id:'t1',name:'原序号-《书名》第N集 章节名',        template:'{original_prefix}-《{book_title}》第{chapter_index_3}集 {chapter_title}.{ext}'},
  {id:'t2',name:'原序号-《书名》[系列]-第N集 章节名', template:'{original_prefix}-《{book_title}》{series_block}第{chapter_index_3}集 {chapter_title}.{ext}'},
  {id:'t3',name:'序号-章节名',                       template:'{chapter_index_3}-{chapter_title}.{ext}'},
  {id:'t4',name:'书名-序号-章节名',                  template:'{book_title}-{chapter_index_3}-{chapter_title}.{ext}'},
  {id:'t5',name:'作者-书名-序号',                    template:'[{author}]{book_title}-{chapter_index_3}.{ext}'},
  {id:'t6',name:'纯序号',                            template:'{chapter_index_4}.{ext}'},
  {id:'t7',name:'第N章 章节名',                      template:'第{chapter_index_3}章 {chapter_title}.{ext}'},
];

function TemplatesTab(){
  const[templates,setTemplates]=useState([]);
  const[editItem,setEditItem]=useState(null);
  const[deleteId,setDeleteId]=useState(null);
  const[importJson,setImportJson]=useState('');
  const[showImport,setShowImport]=useState(false);
  const[error,setError]=useState('');

  useEffect(()=>{api('/api/file-manager/templates').then(r=>r.ok&&setTemplates(r.templates)).catch(()=>{});}, []);

  async function save(tpls){
    try{
      const r=await api('/api/file-manager/templates',{method:'POST',body:JSON.stringify({templates:tpls})});
      if(!r.ok)throw new Error(r.error);
      setTemplates(r.templates);
    }catch(e){setError(e.message);}
  }

  async function commit(){
    if(!editItem?.name||!editItem?.template)return;
    const exists=templates.find(t=>t.id===editItem.id);
    await save(exists?templates.map(t=>t.id===editItem.id?editItem:t):[...templates,editItem]);
    setEditItem(null);
  }

  return(
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      {error&&<Err>{error}</Err>}
      <div className="glass glass-pad">
        <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:14}}>
          <span style={{fontWeight:600,fontSize:14,flex:1}}>模板列表</span>
          <button className="btn btn-ghost btn-sm" onClick={()=>setShowImport(!showImport)}>导入</button>
          <button className="btn btn-ghost btn-sm" onClick={()=>navigator.clipboard.writeText(JSON.stringify(templates,null,2)).then(()=>alert('已复制'))}>导出</button>
          <button className="btn btn-ghost btn-sm" onClick={()=>{if(confirm('重置默认模板？'))save(DEFAULT_TEMPLATES);}}>重置默认</button>
          <button className="btn btn-primary btn-sm" onClick={()=>setEditItem({id:'new-'+Date.now(),name:'',template:''})}>
            <Icon id="i-plus" className="icon icon-sm"/>新建
          </button>
        </div>
        {showImport&&(
          <div style={{background:'var(--bg-1)',borderRadius:8,padding:12,marginBottom:12,display:'flex',flexDirection:'column',gap:8}}>
            <textarea value={importJson} onChange={e=>setImportJson(e.target.value)} rows={5}
              placeholder='[{"id":"t1","name":"模板名","template":"..."}]'
              style={{...S.textarea,minHeight:90}}/>
            <div style={{display:'flex',gap:8}}>
              <button className="btn btn-ghost btn-sm" onClick={()=>setShowImport(false)}>取消</button>
              <button className="btn btn-primary btn-sm" onClick={()=>{try{const p=JSON.parse(importJson);if(!Array.isArray(p))throw 0;save(p);setShowImport(false);setImportJson('');}catch{setError('导入失败：JSON 格式错误');}}}>导入</button>
            </div>
          </div>
        )}
        <div style={{display:'flex',flexDirection:'column',gap:6}}>
          {templates.map(t=>(
            <div key={t.id} style={{display:'flex',alignItems:'center',gap:10,padding:'10px 12px',background:'var(--bg-0)',borderRadius:7,border:'1px solid var(--border)'}}>
              <div style={{flex:1}}>
                <div style={{fontWeight:500,fontSize:13.5}}>{t.name}</div>
                <div style={{fontFamily:'monospace',fontSize:12,color:'var(--text-mute)',marginTop:2}}>{t.template}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={()=>setEditItem({...t})}>编辑</button>
              <button className="btn btn-ghost btn-sm" style={{color:'var(--danger)'}} onClick={()=>setDeleteId(t.id)}>删除</button>
            </div>
          ))}
          {templates.length===0&&<div style={{color:'var(--text-mute)',textAlign:'center',padding:20,fontSize:13}}>暂无模板，点击"新建"添加</div>}
        </div>
      </div>

      {editItem&&(
        <Modal onClose={()=>setEditItem(null)}>
          <div style={{fontWeight:600,fontSize:15,marginBottom:14}}>{templates.find(t=>t.id===editItem.id)?'编辑':'新建'}模板</div>
          <div style={{display:'flex',flexDirection:'column',gap:10}}>
            <div><label style={S.label}>模板名称</label><input style={S.input} value={editItem.name} onChange={e=>setEditItem(p=>({...p,name:e.target.value}))}/></div>
            <div><label style={S.label}>模板字符串</label><input style={{...S.input,fontFamily:'monospace'}} value={editItem.template} onChange={e=>setEditItem(p=>({...p,template:e.target.value}))}/></div>
            <div style={{fontSize:11.5,color:'var(--text-mute)',lineHeight:1.8}}>
              变量：<code>{'{book_title}'}</code> <code>{'{author}'}</code> <code>{'{chapter_index_3}'}</code> <code>{'{chapter_title}'}</code> <code>{'{ext}'}</code>
            </div>
            <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
              <button className="btn btn-ghost" onClick={()=>setEditItem(null)}>取消</button>
              <button className="btn btn-primary" onClick={commit} disabled={!editItem.name||!editItem.template}>保存</button>
            </div>
          </div>
        </Modal>
      )}

      {deleteId&&(
        <Modal onClose={()=>setDeleteId(null)}>
          <div style={{fontWeight:600,fontSize:15,marginBottom:10}}>确认删除</div>
          <div style={{fontSize:13,color:'var(--text-mute)',marginBottom:16}}>确定删除此模板？此操作不可撤销。</div>
          <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
            <button className="btn btn-ghost" onClick={()=>setDeleteId(null)}>取消</button>
            <button className="btn btn-primary" style={{background:'var(--danger)'}} onClick={()=>{save(templates.filter(t=>t.id!==deleteId));setDeleteId(null);}}>删除</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ─── Tab 5：历史回滚 ──────────────────────────────────────────────────────────

function HistoryTab(){
  const[history,setHistory]=useState([]);
  const[loading,setLoading]=useState(false);
  const[error,setError]=useState('');
  const[expanded,setExpanded]=useState({});
  const[rollbackId,setRollbackId]=useState(null);
  const[rolling,setRolling]=useState(false);

  useEffect(()=>{load();}, []);

  async function load(){
    setLoading(true);
    try{const r=await api('/api/file-manager/history');if(r.ok)setHistory(r.history);}
    catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  async function doRollback(){
    if(!rollbackId)return;
    setRolling(true);
    try{
      const r=await api('/api/file-manager/rollback',{method:'POST',body:JSON.stringify({history_id:rollbackId})});
      if(!r.ok)throw new Error(r.error);
      alert(`回滚完成：成功 ${r.success} 个，失败 ${r.failed} 个`);
      setRollbackId(null);load();
    }catch(e){setError('回滚失败：'+e.message);}
    finally{setRolling(false);}
  }

  return(
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      {error&&<Err>{error}</Err>}
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <span style={{fontSize:13,color:'var(--text-mute)'}}>共 {history.length} 条操作记录</span>
        <button className="btn btn-ghost btn-sm" onClick={load} disabled={loading}><Icon id="i-refresh" className="icon icon-sm"/>刷新</button>
      </div>
      {loading&&<div style={{textAlign:'center',padding:40}}><span className="loading"/></div>}
      {!loading&&history.length===0&&<div className="glass glass-pad" style={{color:'var(--text-mute)',textAlign:'center',padding:40,fontSize:13}}>暂无操作历史</div>}
      {history.map(item=>{
        const sc=(item.ops||[]).filter(op=>op.status==='success').length;
        const isRollback=(item.note||'').startsWith('[回滚]');
        return(
          <div key={item.history_id} className="glass" style={{borderRadius:8,overflow:'hidden'}}>
            <div style={{display:'flex',alignItems:'center',gap:10,padding:'12px 14px',cursor:'pointer'}}
              onClick={()=>setExpanded(p=>({...p,[item.history_id]:!p[item.history_id]}))}>
              <Icon id="i-clock" className="icon icon-sm" style={{color:isRollback?'var(--warning)':'var(--primary)',flexShrink:0}}/>
              <div style={{flex:1}}>
                <div style={{fontSize:13.5,fontWeight:500}}>{item.note||'（无备注）'}</div>
                <div style={{fontSize:12,color:'var(--text-mute)',marginTop:2}}>
                  {item.timestamp?item.timestamp.slice(0,19).replace('T',' '):''} · 成功 {sc} 个
                </div>
              </div>
              {!isRollback&&<button className="btn btn-ghost btn-sm" style={{flexShrink:0}}
                onClick={e=>{e.stopPropagation();setRollbackId(item.history_id);}}>回滚</button>}
              <Icon id={expanded[item.history_id]?'i-arrow-left':'i-arrow-right'} className="icon icon-sm" style={{opacity:.4}}/>
            </div>
            {expanded[item.history_id]&&(
              <div style={{borderTop:'1px solid var(--border)',overflowX:'auto'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:480}}>
                  <thead><tr style={{background:'var(--panel-hi)',color:'var(--text-faint)'}}>
                    {['原文件名','新文件名','状态'].map(h=><th key={h} style={{padding:'6px 12px',textAlign:'left',fontWeight:700,fontSize:11,textTransform:'uppercase',letterSpacing:'.4px'}}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {(item.ops||[]).map((op,i)=>(
                      <tr key={i} style={{borderTop:'1px solid var(--border)',background:i%2===0?'transparent':'rgba(0,0,0,.015)'}}>
                        <td style={{padding:'6px 12px',color:'var(--text-mute)',wordBreak:'break-word',maxWidth:220}}>{op.original_path.split(/[\\/]/).pop()}</td>
                        <td style={{padding:'6px 12px',wordBreak:'break-word',maxWidth:220}}>{op.new_path.split(/[\\/]/).pop()}</td>
                        <td style={{padding:'6px 12px',whiteSpace:'nowrap'}}>
                          <span style={{color:op.status==='success'?'var(--success)':'var(--danger)',fontWeight:600,fontSize:11}}>{op.status==='success'?'✓ 成功':'✗ 失败'}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
      {rollbackId&&(
        <Modal onClose={()=>setRollbackId(null)}>
          <div style={{fontWeight:600,fontSize:15,marginBottom:10}}>确认回滚</div>
          <div style={{fontSize:13,color:'var(--text-mute)',marginBottom:16}}>将文件名恢复到操作前的状态，此操作也会被记录。</div>
          <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
            <button className="btn btn-ghost" onClick={()=>setRollbackId(null)} disabled={rolling}>取消</button>
            <button className="btn btn-primary" style={{background:'var(--warning)'}} onClick={doRollback} disabled={rolling}>
              {rolling?<span className="loading"/>:'确认回滚'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ─── Tab 6：系统设置（合并 DeepSeek 配置 + 刮削平台 Cookie）─────────────────

function SettingsTab(){
  const[fmCfg,setFmCfg]=useState(null);
  const[cookies,setCookies]=useState({qidian:'',netease:''});
  const[blacklist,setBlacklist]=useState([]);
  const[blacklistInput,setBlacklistInput]=useState('');
  const[apiKey,setApiKey]=useState('');
  const[customRules,setCustomRules]=useState('');
  const[saving,setSaving]=useState(false);
  const[error,setError]=useState('');
  const[success,setSuccess]=useState('');

  useEffect(()=>{
    api('/api/file-manager/config').then(r=>r.ok&&(setFmCfg(r.config),setCustomRules((r.config.custom_ad_rules||[]).join('\n')))).catch(()=>{});
    api('/api/meta/cookies').then(r=>r.ok&&setCookies(r.cookies||{})).catch(()=>{});
    api('/api/meta/tag-blacklist').then(r=>r.ok&&setBlacklist(r.patterns||[])).catch(()=>{});
  },[]);

  async function saveFmCfg(){
    setSaving(true);setError('');setSuccess('');
    try{
      const p={ai_enabled:fmCfg.ai_enabled,ai_base_url:fmCfg.ai_base_url,ai_model:fmCfg.ai_model,
        custom_ad_rules:customRules.split('\n').map(s=>s.trim()).filter(Boolean)};
      if(apiKey)p.ai_api_key=apiKey;
      const r=await api('/api/file-manager/config',{method:'POST',body:JSON.stringify(p)});
      if(!r.ok)throw new Error(r.error);
      setSuccess('AI 配置已保存');setApiKey('');
      api('/api/file-manager/config').then(r=>r.ok&&setFmCfg(r.config));
    }catch(e){setError(e.message);}
    finally{setSaving(false);}
  }

  async function saveCookies(){
    setSaving(true);setError('');setSuccess('');
    try{
      const r=await api('/api/meta/cookies',{method:'POST',body:JSON.stringify({cookies})});
      if(!r.ok)throw new Error(r.error);
      setSuccess('Cookie 已保存');
    }catch(e){setError(e.message);}
    finally{setSaving(false);}
  }

  async function saveBlacklist(){
    setSaving(true);setError('');setSuccess('');
    try{
      const r=await api('/api/meta/tag-blacklist',{method:'POST',body:JSON.stringify({patterns:blacklist})});
      if(!r.ok)throw new Error(r.error);
      setBlacklist(r.patterns||[]);setSuccess('标签黑名单已保存');
    }catch(e){setError(e.message);}
    finally{setSaving(false);}
  }

  return(
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      {error&&<Err>{error}</Err>}
      {success&&<div style={{color:'var(--success)',padding:'10px 14px',background:'rgba(52,211,153,.08)',borderRadius:10,border:'1px solid rgba(52,211,153,.3)',fontSize:13,display:'flex',alignItems:'center',gap:8}}>
        <Icon id="i-check" className="icon icon-sm" style={{flexShrink:0}}/>{success}
      </div>}

      {/* DeepSeek 配置 */}
      {fmCfg&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{fontWeight:600,fontSize:14}}>DeepSeek AI 配置（用于智能重命名）</div>
          <div style={{display:'flex',alignItems:'center',gap:10}}>
            <span style={{fontSize:13}}>启用 AI 识别</span>
            <div onClick={()=>setFmCfg(p=>({...p,ai_enabled:!p.ai_enabled}))}
              style={{width:40,height:22,borderRadius:11,cursor:'pointer',position:'relative',background:fmCfg.ai_enabled?'var(--primary)':'var(--border)',transition:'background .2s'}}>
              <div style={{position:'absolute',top:2,left:fmCfg.ai_enabled?20:2,width:18,height:18,borderRadius:'50%',background:'#fff',transition:'left .2s'}}/>
            </div>
          </div>
          <div>
            <label style={S.label}>API Key {fmCfg.ai_api_key_masked&&<span>（当前：{fmCfg.ai_api_key_masked}）</span>}</label>
            <input type="password" value={apiKey} onChange={e=>setApiKey(e.target.value)}
              placeholder="输入新 Key 以更新（留空保持不变）" style={S.input}/>
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:10}}>
            <div>
              <label style={S.label}>Base URL</label>
              <input value={fmCfg.ai_base_url} onChange={e=>setFmCfg(p=>({...p,ai_base_url:e.target.value}))} style={S.input}/>
            </div>
            <div>
              <label style={S.label}>模型</label>
              <input value={fmCfg.ai_model} onChange={e=>setFmCfg(p=>({...p,ai_model:e.target.value}))} style={S.input}/>
            </div>
          </div>
          <div>
            <label style={S.label}>自定义广告清理规则（每行一个正则）</label>
            <textarea value={customRules} onChange={e=>setCustomRules(e.target.value)} rows={4}
              placeholder="如：\[.*?广告.*?\]" style={{...S.textarea,minHeight:80}}/>
          </div>
          <button className="btn btn-primary" onClick={saveFmCfg} disabled={saving} style={{alignSelf:'flex-start'}}>
            {saving?<span className="loading"/>:<Icon id="i-check" className="icon icon-sm"/>}保存 AI 配置
          </button>
        </div>
      )}

      {/* 刮削平台 Cookie */}
      <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
        <div style={{fontWeight:600,fontSize:14}}>刮削平台 Cookie</div>
        {[['qidian','起点听书（需登录账号）'],['netease','网易云听书']].map(([k,l])=>(
          <div key={k}>
            <label style={S.label}>{l}</label>
            <textarea value={cookies[k]||''} onChange={e=>setCookies(p=>({...p,[k]:e.target.value}))}
              placeholder="粘贴 Cookie 字符串..." rows={2} style={{...S.textarea,minHeight:60}}/>
          </div>
        ))}
        <button className="btn btn-primary" onClick={saveCookies} disabled={saving} style={{alignSelf:'flex-start'}}>
          {saving?<span className="loading"/>:<Icon id="i-check" className="icon icon-sm"/>}保存 Cookie
        </button>
      </div>

      {/* 标签黑名单 */}
      <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
        <div style={{fontWeight:600,fontSize:14}}>刮削标签黑名单</div>
        <div style={{display:'flex',gap:8}}>
          <input value={blacklistInput} onChange={e=>setBlacklistInput(e.target.value)}
            placeholder="输入要屏蔽的标签..."
            onKeyDown={e=>{if(e.key==='Enter'){const v=blacklistInput.trim();if(v&&!blacklist.includes(v)){setBlacklist(l=>[...l,v]);}setBlacklistInput('');}}}
            style={S.input}/>
          <button className="btn btn-primary btn-sm" onClick={()=>{const v=blacklistInput.trim();if(v&&!blacklist.includes(v)){setBlacklist(l=>[...l,v]);}setBlacklistInput('');}}>添加</button>
        </div>
        <div style={{display:'flex',flexWrap:'wrap',gap:6}}>
          {blacklist.map(p=>(
            <span key={p} style={{display:'inline-flex',alignItems:'center',gap:4,padding:'3px 10px',borderRadius:99,background:'var(--panel-hi)',border:'1px solid var(--border)',fontSize:12,color:'var(--text)'}}>
              {p}<button onClick={()=>setBlacklist(l=>l.filter(x=>x!==p))} style={{background:'none',border:'none',cursor:'pointer',color:'var(--danger)',padding:'0 0 0 2px',fontSize:14,lineHeight:1}}>×</button>
            </span>
          ))}
          {blacklist.length===0&&<span style={{fontSize:12.5,color:'var(--text-mute)'}}>暂无黑名单规则</span>}
        </div>
        <button className="btn btn-primary" onClick={saveBlacklist} disabled={saving} style={{alignSelf:'flex-start'}}>
          {saving?<span className="loading"/>:<Icon id="i-check" className="icon icon-sm"/>}保存黑名单
        </button>
      </div>
    </div>
  );
}

// ─── 小工具组件 ──────────────────────────────────────────────────────────────

function Err({children}){
  return<div style={{color:'var(--danger)',padding:'10px 14px',background:'rgba(239,68,68,.08)',borderRadius:10,border:'1px solid rgba(239,68,68,.3)',fontSize:13,display:'flex',alignItems:'center',gap:8}}>
    <Icon id="i-alert" className="icon icon-sm" style={{flexShrink:0}}/>
    <span>{children}</span>
  </div>;
}

function Tag({c,children}){
  const styles={
    success:{color:'var(--success)',background:'rgba(52,211,153,.12)',border:'1px solid rgba(52,211,153,.2)'},
    danger:{color:'var(--danger)',background:'rgba(239,68,68,.12)',border:'1px solid rgba(239,68,68,.2)'},
    mute:{color:'var(--text-faint)',background:'var(--panel-hi)',border:'1px solid var(--border)'},
  };
  const s=styles[c]||styles.mute;
  return<span style={{fontSize:10.5,padding:'1px 7px',borderRadius:99,...s,fontWeight:600}}>{children}</span>;
}

function Modal({children,onClose}){
  return(
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.55)',backdropFilter:'blur(6px)',WebkitBackdropFilter:'blur(6px)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1000,padding:'16px'}}
      onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div style={{background:'var(--panel-2)',borderRadius:'var(--radius-card)',padding:24,width:480,maxWidth:'100%',boxShadow:'0 20px 60px rgba(0,0,0,.5)',border:'1px solid var(--border-hi)'}}>
        {children}
      </div>
    </div>
  );
}
