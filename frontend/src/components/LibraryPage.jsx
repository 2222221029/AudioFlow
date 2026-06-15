// LibraryPage.jsx — 书库管理（书库扫描 + 刮削写标签 + 智能重命名 统一入口）
import {useEffect, useRef, useState} from 'react';
import {api} from '../services/api.js';
import {Icon} from './Icons.jsx';

// ─── 常量 ────────────────────────────────────────────────────────────────────

const TABS = [
  ['scan',      'i-folder',   '书库扫描'],
  ['scrape',    'i-tag',      '刮削写标签'],
  ['rename',    'i-edit',     '智能重命名'],
  ['templates', 'i-file',     '模板管理'],
  ['history',   'i-clock',    '历史回滚'],
  ['settings',  'i-settings', '系统设置'],
];

const S = {
  input: {width:'100%',background:'var(--bg-0)',border:'1px solid var(--border)',borderRadius:6,padding:'7px 10px',color:'var(--text)',fontSize:13,outline:'none',boxSizing:'border-box'},
  select: {width:'100%',background:'var(--bg-0)',border:'1px solid var(--border)',borderRadius:6,padding:'7px 10px',color:'var(--text)',fontSize:13,outline:'none'},
  label: {fontSize:11,color:'var(--text-mute)',marginBottom:4,fontWeight:600,display:'block'},
};

function fmtSize(bytes){
  if(!bytes)return'0 B';
  if(bytes>=1024**3)return(bytes/1024**3).toFixed(1)+' GB';
  if(bytes>=1024**2)return(bytes/1024**2).toFixed(1)+' MB';
  if(bytes>=1024)return(bytes/1024).toFixed(1)+' KB';
  return bytes+' B';
}

function simulateTemplate(tpl,meta,fileName,idx){
  const stem=fileName.replace(/\.[^.]+$/,'');
  const ext=(fileName.match(/\.([^.]+)$/)||['',''])[1].toLowerCase();
  const i=idx+1;
  const vars={book_title:meta.book_title||'',author:meta.author||'',narrator:meta.narrator||'',
    category:meta.category||'',series:meta.series||'',volume:meta.volume||'',
    chapter_index:String(i),chapter_index_2:String(i).padStart(2,'0'),
    chapter_index_3:String(i).padStart(3,'0'),chapter_index_4:String(i).padStart(4,'0'),
    chapter_title:stem,chapter_full:String(i).padStart(3,'0')+'-'+stem,
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
    <div style={{display:'flex',flexWrap:'wrap',gap:5,padding:'6px 8px',border:'1px solid var(--border)',borderRadius:6,background:'var(--bg-0)',minHeight:36,alignItems:'center'}}>
      {tags.map(t=>(
        <span key={t} style={{display:'flex',alignItems:'center',gap:3,background:'rgba(99,102,241,.15)',color:'var(--primary)',fontSize:12,padding:'2px 8px',borderRadius:99,whiteSpace:'nowrap'}}>
          {t}
          <button onClick={()=>remove(t)} style={{background:'none',border:'none',color:'inherit',cursor:'pointer',padding:0,lineHeight:1,fontSize:13}}>×</button>
        </span>
      ))}
      <input value={input} onChange={e=>setInput(e.target.value)}
        onKeyDown={e=>{if(e.key==='Enter'||e.key===','){e.preventDefault();if(input.trim())add(input);}
                       if(e.key==='Backspace'&&!input&&tags.length)onChange(tags.slice(0,-1));}}
        onBlur={()=>{if(input.trim())add(input);}}
        placeholder={tags.length?'':'输入标签后按 Enter…'}
        style={{border:'none',outline:'none',background:'transparent',color:'var(--text)',fontSize:12.5,minWidth:100,flex:1}}/>
    </div>
  );
}

// ─── 共用文件夹浏览器 Modal ──────────────────────────────────────────────────

function FileBrowserModal({data,onNav,onSelect,onClose}){
  return(
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.6)',zIndex:1000,display:'flex',alignItems:'center',justifyContent:'center'}}
      onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div style={{background:'var(--panel)',borderRadius:12,width:'90%',maxWidth:560,maxHeight:'70vh',display:'flex',flexDirection:'column',overflow:'hidden',boxShadow:'0 8px 40px rgba(0,0,0,.4)'}}>
        <div style={{padding:'14px 16px',borderBottom:'1px solid var(--border)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <span style={{fontWeight:700}}>选择专辑文件夹</span>
          <button className="btn btn-ghost btn-sm" onClick={onClose}><Icon id="i-close" className="icon icon-sm"/></button>
        </div>
        {data?(
          <>
            <div style={{padding:'8px 16px',fontSize:11,color:'var(--text-faint)',borderBottom:'1px solid var(--border)',fontFamily:'monospace'}}>
              {data.current}
            </div>
            <div style={{flex:1,overflow:'auto',padding:8}}>
              {(data.items||[]).map(item=>(
                <div key={item.path}
                  style={{display:'flex',alignItems:'center',gap:8,padding:'8px 10px',borderRadius:6,cursor:'pointer',fontSize:13,color:item.has_audio?'var(--text)':'var(--text-mute)'}}
                  onMouseEnter={e=>e.currentTarget.style.background='var(--bg-1)'}
                  onMouseLeave={e=>e.currentTarget.style.background=''}
                  onClick={()=>onNav(item.path)}>
                  <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)',flexShrink:0}}/>
                  <span style={{flex:1}}>{item.name}</span>
                  {item.has_audio&&<span style={{fontSize:10,color:'var(--success)',background:'rgba(16,185,129,.15)',padding:'1px 7px',borderRadius:99,flexShrink:0}}>有音频</span>}
                </div>
              ))}
              {(data.items||[]).length===0&&<div style={{padding:32,textAlign:'center',color:'var(--text-mute)',fontSize:13}}>此目录下没有子文件夹</div>}
            </div>
            <div style={{padding:'10px 16px',borderTop:'1px solid var(--border)',display:'flex',gap:8,justifyContent:'flex-end'}}>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
              <button className="btn btn-primary btn-sm" onClick={()=>onSelect(data.current)}>选择此目录</button>
            </div>
          </>
        ):(
          <div style={{padding:48,textAlign:'center',color:'var(--text-faint)'}}><span className="loading"/></div>
        )}
      </div>
    </div>
  );
}

// ─── 主组件 ──────────────────────────────────────────────────────────────────

export function LibraryPage(){
  const[tab,setTab]=useState('scan');
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
      <div style={{display:'flex',alignItems:'center',gap:8,padding:'10px 16px',borderBottom:'1px solid var(--border)',background:'var(--bg-1)',flexShrink:0}}>
        <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)',flexShrink:0}}/>
        <span style={{flex:1,fontSize:13,color:folder?'var(--text)':'var(--text-mute)',fontFamily:folder?'monospace':'inherit',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
          {folder||'未选择专辑文件夹 — 在书库扫描中点击专辑，或直接点击右侧"浏览"按钮'}
        </span>
        {folder&&<button className="btn btn-ghost btn-sm" onClick={()=>setFolder('')} style={{flexShrink:0}}>清除</button>}
        <button className="btn btn-primary btn-sm" onClick={()=>openBrowser(folder)} style={{flexShrink:0}}>
          <Icon id="i-folder" className="icon icon-sm"/>浏览
        </button>
      </div>

      {/* Tab 栏 */}
      <div style={{display:'flex',gap:0,borderBottom:'1px solid var(--border)',flexShrink:0,overflowX:'auto',background:'var(--bg-0)'}}>
        {TABS.map(([id,icon,label])=>(
          <button key={id} onClick={()=>setTab(id)}
            style={{display:'flex',alignItems:'center',gap:6,padding:'10px 16px',background:'none',border:'none',
              borderBottom:tab===id?'2px solid var(--primary)':'2px solid transparent',
              color:tab===id?'var(--primary)':'var(--text-mute)',
              fontWeight:tab===id?600:400,fontSize:13.5,whiteSpace:'nowrap',cursor:'pointer'}}>
            <Icon id={icon} className="icon icon-sm"/>{label}
          </button>
        ))}
      </div>

      {/* 内容区 */}
      <div style={{flex:1,overflow:'auto',padding:'16px'}}>
        {tab==='scan'      && <ScanTab selectedFolder={folder} onSelectFolder={f=>{setFolder(f);setTab('scrape');}} onBrowse={openBrowser}/>}
        {tab==='scrape'    && <ScrapeTab selectedFolder={folder} onBrowse={()=>openBrowser(folder)} onFolderChange={setFolder}/>}
        {tab==='rename'    && <RenameTab selectedFolder={folder} onBrowse={()=>openBrowser(folder)} onFolderChange={setFolder} onGotoHistory={()=>setTab('history')}/>}
        {tab==='templates' && <TemplatesTab/>}
        {tab==='history'   && <HistoryTab/>}
        {tab==='settings'  && <SettingsTab/>}
      </div>

      {browserOpen&&<FileBrowserModal data={browserData} onNav={navBrowser} onSelect={selectFolder} onClose={()=>setBrowserOpen(false)}/>}
    </div>
  );
}

// ─── Tab 1：书库扫描 ──────────────────────────────────────────────────────────

function ScanTab({selectedFolder,onSelectFolder,onBrowse}){
  const[result,setResult]=useState(null);
  const[loading,setLoading]=useState(false);
  const[error,setError]=useState('');
  const[search,setSearch]=useState('');
  const[sortBy,setSortBy]=useState('name');
  const[expanded,setExpanded]=useState({});

  async function doScan(){
    setLoading(true);setError('');
    try{
      const url='/api/file-manager/scan'+(selectedFolder?`?root=${encodeURIComponent(selectedFolder)}`:'');
      const r=await api(url);
      if(!r.ok)throw new Error(r.error||'扫描失败');
      setResult(r);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  const books=[...(result?.books||[])];
  const filtered=books
    .filter(b=>!search||b.folder_name.toLowerCase().includes(search.toLowerCase()))
    .sort((a,b)=>{
      if(sortBy==='name')return a.folder_name.localeCompare(b.folder_name);
      if(sortBy==='files')return b.file_count-a.file_count;
      if(sortBy==='size')return b.total_size-a.total_size;
      return 0;
    });

  return(
    <div style={{display:'flex',flexDirection:'column',gap:12}}>
      <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
        <button className="btn btn-primary" onClick={doScan} disabled={loading}>
          {loading?<span className="loading"/>:<Icon id="i-folder" className="icon icon-sm"/>}
          {selectedFolder?'重新扫描所选目录':'扫描下载目录'}
        </button>
        {result&&(
          <>
            <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索书名..."
              style={{flex:1,minWidth:140,...S.input,width:'auto'}}/>
            <span style={{fontSize:12.5,color:'var(--text-mute)'}}>排序：</span>
            {[['name','书名'],['files','文件数'],['size','大小']].map(([v,l])=>(
              <button key={v} onClick={()=>setSortBy(v)}
                style={{padding:'5px 10px',borderRadius:5,border:'1px solid var(--border)',
                  background:sortBy===v?'var(--primary)':'var(--bg-0)',
                  color:sortBy===v?'#fff':'var(--text)',fontSize:12.5,cursor:'pointer'}}>{l}</button>
            ))}
          </>
        )}
      </div>

      {error&&<Err>{error}</Err>}

      {result&&(
        <div style={{display:'flex',gap:12,flexWrap:'wrap',fontSize:13}}>
          {[['共',result.total_books+' 本书'],['文件',result.total_files+' 个'],['大小',result.total_size_fmt||fmtSize(result.total_size)],['目录',result.root]].map(([k,v])=>(
            <div key={k} style={{background:'var(--bg-0)',borderRadius:6,padding:'5px 12px',border:'1px solid var(--border)'}}>
              <span style={{color:'var(--text-mute)'}}>{k}：</span><span style={{fontWeight:600}}>{v}</span>
            </div>
          ))}
        </div>
      )}

      {result&&filtered.length===0&&<div style={{color:'var(--text-mute)',fontSize:13,padding:20,textAlign:'center'}}>未找到匹配的书籍</div>}

      <div style={{display:'flex',flexDirection:'column',gap:6}}>
        {filtered.map(book=>{
          const exts=[...new Set(book.files.map(f=>f.ext))];
          const isSelected=selectedFolder===book.folder_path;
          return(
            <div key={book.folder_path} className="glass" style={{borderRadius:8,overflow:'hidden',
              outline:isSelected?'2px solid var(--primary)':'none'}}>
              <div style={{display:'flex',alignItems:'center',gap:10,padding:'10px 14px',cursor:'pointer'}}
                onClick={()=>setExpanded(p=>({...p,[book.folder_path]:!p[book.folder_path]}))}>
                <Icon id="i-folder" className="icon icon-sm" style={{color:'var(--primary)',flexShrink:0}}/>
                <span style={{flex:1,fontWeight:600,fontSize:13.5}}>{book.folder_name}</span>
                <div style={{display:'flex',gap:5,flexWrap:'wrap'}}>
                  {exts.map(e=><span key={e} style={{padding:'1px 7px',borderRadius:99,background:'var(--bg-1)',fontSize:11,color:'var(--text-mute)',border:'1px solid var(--border)'}}>{e}</span>)}
                </div>
                <span style={{fontSize:12.5,color:'var(--text-mute)',whiteSpace:'nowrap'}}>{book.file_count} 个文件</span>
                <span style={{fontSize:12.5,color:'var(--text-mute)',whiteSpace:'nowrap'}}>{book.total_size_fmt||fmtSize(book.total_size)}</span>
                <button className="btn btn-primary btn-sm" style={{flexShrink:0}}
                  onClick={e=>{e.stopPropagation();onSelectFolder(book.folder_path);}}>
                  选择此专辑
                </button>
              </div>
              {expanded[book.folder_path]&&(
                <div style={{borderTop:'1px solid var(--border)'}}>
                  <table style={{width:'100%',borderCollapse:'collapse',fontSize:12.5}}>
                    <thead><tr style={{background:'var(--bg-1)',color:'var(--text-mute)'}}>
                      {['文件名','大小','时长','修改时间'].map(h=><th key={h} style={{padding:'5px 12px',textAlign:'left',fontWeight:500}}>{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {book.files.map(f=>(
                        <tr key={f.path} style={{borderTop:'1px solid var(--border)'}}>
                          <td style={{padding:'5px 12px',wordBreak:'break-all'}}>{f.name}</td>
                          <td style={{padding:'5px 12px',color:'var(--text-mute)',whiteSpace:'nowrap'}}>{f.size_fmt||fmtSize(f.size)}</td>
                          <td style={{padding:'5px 12px',color:'var(--text-mute)',whiteSpace:'nowrap'}}>{f.duration_fmt||'-'}</td>
                          <td style={{padding:'5px 12px',color:'var(--text-mute)',whiteSpace:'nowrap'}}>{f.mtime?f.mtime.slice(0,10):'-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {!result&&!loading&&(
        <div className="glass glass-pad" style={{color:'var(--text-mute)',textAlign:'center',padding:60,fontSize:14}}>
          <Icon id="i-folder" style={{width:48,height:48,opacity:.25}}/>
          <div style={{marginTop:12}}>点击"扫描下载目录"开始，或先选择专辑文件夹再扫描</div>
        </div>
      )}
    </div>
  );
}

// ─── Tab 2：刮削写标签 ────────────────────────────────────────────────────────

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
  const evtRef=useRef(null);

  // 加载选项和配置
  useEffect(()=>{
    api('/api/meta/options').then(r=>r.ok&&setOptions(r.options)).catch(()=>{});
    api('/api/meta/config').then(r=>r.ok&&setParams(r.params)).catch(()=>{});
  },[]);

  // selectedFolder 变化时同步到 params
  useEffect(()=>{
    if(selectedFolder&&params)
      setParams(p=>({...p,input_folder:selectedFolder}));
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

  async function doFetchById(){
    if(!apiSource||!apiId.trim())return;
    setFetchBusy(true);setFetchError('');setFetchedMeta(null);
    try{
      const r=await api('/api/meta/fetch-metadata',{method:'POST',body:JSON.stringify({api_source:apiSource,api_id:apiId.trim()})});
      if(r.ok)setFetchedMeta(r.metadata);
      else setFetchError(r.error||'获取失败');
    }catch(e){setFetchError(String(e));}
    setFetchBusy(false);
  }

  async function doFetchByLink(){
    if(!linkPlatform||!linkUrl.trim())return;
    setFetchBusy(true);setFetchError('');setFetchedMeta(null);
    try{
      const r=await api('/api/meta/fetch-link',{method:'POST',body:JSON.stringify({platform:linkPlatform,url:linkUrl.trim()})});
      if(r.ok)setFetchedMeta(r.metadata);
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
      <div style={{display:'flex',gap:4,borderBottom:'1px solid var(--border)',marginBottom:14,alignItems:'center'}}>
        {[['meta','i-search','元数据获取'],['params','i-settings','处理参数'],['queue','i-download','任务队列'],['logs','i-file','处理日志']].map(([id,icon,label])=>(
          <button key={id} onClick={()=>setSubTab(id)}
            style={{background:'none',border:'none',borderBottom:subTab===id?'2px solid var(--primary)':'2px solid transparent',
              color:subTab===id?'var(--primary)':'var(--text-mute)',padding:'7px 14px',
              fontWeight:subTab===id?600:400,fontSize:13,cursor:'pointer',display:'flex',alignItems:'center',gap:5}}>
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
                    <div style={{display:'flex',flexWrap:'wrap',gap:4,marginTop:6}}>
                      {fetchedMeta.tags.slice(0,12).map(t=>(
                        <span key={t} style={{background:'rgba(99,102,241,.15)',color:'var(--primary)',fontSize:11,padding:'1px 7px',borderRadius:99}}>{t}</span>
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
          {/* 音频目录 */}
          <div className="glass" style={{padding:14}}>
            <label style={S.label}>音频目录（专辑文件夹）</label>
            <div style={{display:'flex',gap:8}}>
              <input style={S.input} value={params.input_folder} onChange={e=>{setParam('input_folder',e.target.value);onFolderChange(e.target.value);}} placeholder="/path/to/audiobook/"/>
              <button className="btn btn-ghost btn-sm" onClick={onBrowse}><Icon id="i-folder" className="icon icon-sm"/>浏览</button>
            </div>
          </div>
          {/* 基本信息 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>基本信息</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
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
          {/* 扩展信息 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>扩展信息</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
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
                <textarea style={{...S.input,minHeight:72,resize:'vertical',fontFamily:'inherit'}} value={params.manual_desc||''} placeholder="留空则自动从 API 或已抓取元数据中提取..." onChange={e=>setParam('manual_desc',e.target.value)}/>
              </div>
            </div>
          </div>
          {/* 音频格式 */}
          <div className="glass" style={{padding:14}}>
            <div style={{fontWeight:700,fontSize:13,marginBottom:10}}>音频处理</div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
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
            queue.map(item=>(
              <div key={item.id} className="glass" style={{padding:'10px 14px',borderRadius:8,display:'flex',alignItems:'center',gap:10}}>
                <div style={{flex:1}}>
                  <div style={{fontWeight:500,fontSize:13.5}}>{item.title}</div>
                  <div style={{fontSize:12,color:'var(--text-mute)',marginTop:2}}>{item.author} · {item.source}</div>
                </div>
                <span style={{fontSize:12,padding:'2px 8px',borderRadius:99,
                  background:item.status==='done'?'rgba(16,185,129,.15)':item.status==='failed'?'rgba(239,68,68,.15)':item.status==='processing'?'rgba(99,102,241,.15)':'var(--bg-1)',
                  color:item.status==='done'?'var(--success)':item.status==='failed'?'var(--danger)':item.status==='processing'?'var(--primary)':'var(--text-mute)'}}>
                  {item.status}
                </span>
              </div>
            ))
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

// ─── Tab 3：智能重命名 ─────────────────────────────────────────────────────────

function RenameTab({selectedFolder,onBrowse,onFolderChange,onGotoHistory}){
  const[step,setStep]=useState(1);
  const[folderPath,setFolderPath]=useState(selectedFolder||'');
  const[folderFiles,setFolderFiles]=useState([]);
  const[bookMeta,setBookMeta]=useState({book_title:'',author:'',narrator:'',category:'',series:'',volume:''});
  const[template,setTemplate]=useState('{chapter_index_3}-{chapter_title}.{ext}');
  const[templates,setTemplates]=useState([]);
  const[previews,setPreviews]=useState([]);
  const[note,setNote]=useState('');
  const[loading,setLoading]=useState(false);
  const[error,setError]=useState('');
  const[aiLoading,setAiLoading]=useState(false);
  const[scrapeOpen,setScrapeOpen]=useState(false);
  const[scrapeInput,setScrapeInput]=useState({api_source:'喜马拉雅',api_id:'',link_url:'',link_platform:'起点听书'});

  // selectedFolder 从父组件传入时同步
  useEffect(()=>{if(selectedFolder&&selectedFolder!==folderPath){setFolderPath(selectedFolder);setStep(1);}},
    [selectedFolder]);

  useEffect(()=>{
    api('/api/file-manager/templates').then(r=>r.ok&&setTemplates(r.templates)).catch(()=>{});
  },[]);

  async function loadFolderFiles(){
    if(!folderPath)return;
    setLoading(true);setError('');
    try{
      const r=await api(`/api/file-manager/scan?root=${encodeURIComponent(folderPath)}`);
      if(!r.ok)throw new Error(r.error||'扫描失败');
      const allFiles=r.books.flatMap(b=>b.files);
      setFolderFiles(allFiles);
      onFolderChange(folderPath);
      setStep(2);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  async function doAiAnalyze(){
    if(!folderFiles.length)return;
    setAiLoading(true);setError('');
    try{
      const r=await api('/api/file-manager/ai-analyze',{method:'POST',body:JSON.stringify({file_names:folderFiles.map(f=>f.name)})});
      if(!r.ok)throw new Error(r.error);
      const res=r.result;
      setBookMeta(p=>({...p,book_title:res.book_title||p.book_title,author:res.author||p.author,narrator:res.narrator||p.narrator,category:res.category||p.category,series:res.series||p.series,volume:res.volume||p.volume}));
    }catch(e){setError('AI 识别失败: '+e.message);}
    finally{setAiLoading(false);}
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
      const r=await api('/api/file-manager/rename-preview',{method:'POST',body:JSON.stringify({folder_path:folderPath,template,book_meta:bookMeta})});
      if(!r.ok)throw new Error(r.error);
      setPreviews(r.previews);setStep(4);
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  async function doApply(){
    setLoading(true);setError('');
    try{
      const r=await api('/api/file-manager/rename-apply',{method:'POST',body:JSON.stringify({previews,note})});
      if(!r.ok)throw new Error(r.error);
      alert(`重命名完成：成功 ${r.success} 个，失败 ${r.failed} 个`);
      onGotoHistory();
    }catch(e){setError(e.message);}
    finally{setLoading(false);}
  }

  const livePreview=folderFiles.slice(0,3).map((f,i)=>simulateTemplate(template,bookMeta,f.name,i));

  return(
    <div style={{display:'flex',flexDirection:'column',gap:14}}>
      {/* 步骤条 */}
      <div style={{display:'flex',alignItems:'center',gap:0}}>
        {['选择文件夹','填写元数据','选择模板','预览确认'].map((label,i)=>(
          <div key={i} style={{display:'flex',alignItems:'center'}}>
            <div onClick={()=>i+1<=step&&setStep(i+1)}
              style={{display:'flex',alignItems:'center',gap:6,padding:'6px 12px',borderRadius:20,fontSize:12.5,
                cursor:i+1<=step?'pointer':'default',
                background:step===i+1?'var(--primary)':i+1<step?'var(--bg-1)':'var(--bg-0)',
                color:step===i+1?'#fff':i+1<step?'var(--text)':'var(--text-mute)',border:'1px solid var(--border)'}}>
              <span style={{width:18,height:18,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',
                background:step===i+1?'rgba(255,255,255,.3)':'var(--border)',fontSize:10,fontWeight:700}}>{i+1}</span>
              {label}
            </div>
            {i<3&&<div style={{width:14,height:1,background:'var(--border)'}}/>}
          </div>
        ))}
      </div>

      {error&&<Err>{error}</Err>}

      {/* 步骤1 */}
      {step===1&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:10}}>
          <div style={{fontWeight:600,fontSize:14}}>选择目标文件夹</div>
          <div style={{display:'flex',gap:8}}>
            <input value={folderPath} onChange={e=>{setFolderPath(e.target.value);onFolderChange(e.target.value);}}
              placeholder="输入包含音频文件的文件夹路径..."
              style={{...S.input,flex:1}}/>
            <button className="btn btn-ghost btn-sm" onClick={onBrowse}>浏览</button>
            <button className="btn btn-primary" onClick={loadFolderFiles} disabled={loading||!folderPath}>
              {loading?<span className="loading"/>:'确认'}
            </button>
          </div>
          <div style={{fontSize:12,color:'var(--text-mute)'}}>
            支持 mp3 · m4a · m4b · flac · wav · aac · ogg · opus
            {selectedFolder&&<span style={{color:'var(--primary)'}}> — 已从书库扫描预填当前专辑</span>}
          </div>
        </div>
      )}

      {/* 步骤2+3 */}
      {step>=2&&step<=3&&(
        <div className="glass glass-pad" style={{display:'flex',flexDirection:'column',gap:12}}>
          <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
            <div style={{fontWeight:600,fontSize:14}}>填写书籍元数据</div>
            <div style={{display:'flex',gap:8}}>
              <button className="btn btn-ghost btn-sm" onClick={doAiAnalyze} disabled={aiLoading}>
                {aiLoading?<span className="loading"/>:<Icon id="i-bolt" className="icon icon-sm"/>}AI 识别
              </button>
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
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
            {[['book_title','书名'],['author','作者'],['narrator','主播'],['category','分类'],['series','系列'],['volume','卷号']].map(([k,l])=>(
              <div key={k}>
                <label style={S.label}>{l}</label>
                <input value={bookMeta[k]} onChange={e=>setBookMeta(p=>({...p,[k]:e.target.value}))}
                  placeholder={l} style={S.input}/>
              </div>
            ))}
          </div>
          {step===2&&<button className="btn btn-primary" onClick={()=>setStep(3)} style={{alignSelf:'flex-start'}}>下一步：选择模板</button>}
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
          <div style={{fontSize:12,color:'var(--text-mute)',lineHeight:1.8}}>
            变量：<code>{'{book_title}'}</code> <code>{'{author}'}</code> <code>{'{narrator}'}</code> <code>{'{chapter_index_3}'}</code> <code>{'{chapter_title}'}</code> <code>{'{ext}'}</code> <code>{'{date}'}</code>
          </div>
          {folderFiles.length>0&&(
            <div style={{background:'var(--bg-0)',borderRadius:6,padding:12,border:'1px solid var(--border)'}}>
              <div style={{fontSize:12,color:'var(--text-mute)',marginBottom:8}}>实时预览（前3个文件）</div>
              {livePreview.map((name,i)=>(
                <div key={i} style={{display:'flex',gap:8,alignItems:'center',fontSize:12.5,marginBottom:4}}>
                  <span style={{color:'var(--text-mute)',flex:'0 0 auto',maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{folderFiles[i]?.name}</span>
                  <span style={{color:'var(--text-mute)'}}>→</span>
                  <span style={{color:'var(--primary)'}}>{name}</span>
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
          <div style={{fontWeight:600,fontSize:14}}>预览确认（共 {previews.length} 个文件）</div>
          <div style={{overflowX:'auto',borderRadius:6,border:'1px solid var(--border)'}}>
            <table style={{width:'100%',borderCollapse:'collapse',fontSize:12.5}}>
              <thead><tr style={{background:'var(--bg-1)',color:'var(--text-mute)'}}>
                <th style={{padding:'7px 10px',textAlign:'left',fontWeight:500}}>原文件名</th>
                <th style={{padding:'7px 10px',textAlign:'left',fontWeight:500}}>新文件名</th>
                <th style={{padding:'7px 10px',textAlign:'left',fontWeight:500}}>状态</th>
              </tr></thead>
              <tbody>
                {previews.map((p,i)=>(
                  <tr key={i} style={{borderTop:'1px solid var(--border)',background:p.conflict?'rgba(239,68,68,.05)':'transparent'}}>
                    <td style={{padding:'5px 10px',color:'var(--text-mute)',wordBreak:'break-all'}}>{p.original_name}</td>
                    <td style={{padding:'5px 10px',color:p.conflict?'var(--danger)':'var(--primary)',wordBreak:'break-all'}}>{p.new_name}</td>
                    <td style={{padding:'5px 10px',whiteSpace:'nowrap'}}>
                      {p.conflict?<Tag c="danger">冲突</Tag>:p.original_name===p.new_name?<Tag c="mute">未变</Tag>:<Tag c="success">正常</Tag>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <label style={S.label}>操作备注（可选）</label>
            <input value={note} onChange={e=>setNote(e.target.value)} placeholder="记录此次操作目的..." style={S.input}/>
          </div>
          <div style={{display:'flex',gap:8}}>
            <button className="btn btn-ghost" onClick={()=>setStep(3)}>上一步</button>
            <button className="btn btn-primary" onClick={doApply} disabled={loading}>
              {loading?<span className="loading"/>:<Icon id="i-check" className="icon icon-sm"/>}执行重命名
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Tab 4：模板管理 ──────────────────────────────────────────────────────────

const DEFAULT_TEMPLATES=[
  {id:'t1',name:'章节序号-标题',template:'{chapter_index_3}-{chapter_title}.{ext}'},
  {id:'t2',name:'书名-序号-标题',template:'{book_title}-{chapter_index_3}-{chapter_title}.{ext}'},
  {id:'t3',name:'作者-书名-序号',template:'[{author}]{book_title}-{chapter_index_3}.{ext}'},
  {id:'t4',name:'纯序号',template:'{chapter_index_4}.{ext}'},
  {id:'t5',name:'完整章节',template:'第{chapter_index_3}章 {chapter_title}.{ext}'},
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
              style={{...S.input,fontFamily:'monospace',resize:'vertical'}}/>
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
              <div style={{borderTop:'1px solid var(--border)'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
                  <thead><tr style={{background:'var(--bg-1)',color:'var(--text-mute)'}}>
                    {['原文件名','新文件名','状态'].map(h=><th key={h} style={{padding:'5px 10px',textAlign:'left',fontWeight:500}}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {(item.ops||[]).map((op,i)=>(
                      <tr key={i} style={{borderTop:'1px solid var(--border)'}}>
                        <td style={{padding:'5px 10px',color:'var(--text-mute)',wordBreak:'break-all'}}>{op.original_path.split(/[\\/]/).pop()}</td>
                        <td style={{padding:'5px 10px',wordBreak:'break-all'}}>{op.new_path.split(/[\\/]/).pop()}</td>
                        <td style={{padding:'5px 10px',whiteSpace:'nowrap',color:op.status==='success'?'var(--success)':'var(--danger)'}}>{op.status}</td>
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
      {success&&<div style={{color:'var(--success)',padding:'8px 12px',background:'var(--bg-0)',borderRadius:6,border:'1px solid var(--success)'}}>{success}</div>}

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
          <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10}}>
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
              placeholder="如：\[.*?广告.*?\]" style={{...S.input,fontFamily:'monospace',resize:'vertical'}}/>
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
            <input value={cookies[k]||''} onChange={e=>setCookies(p=>({...p,[k]:e.target.value}))}
              placeholder="粘贴 Cookie 字符串..." style={{...S.input,fontFamily:'monospace',fontSize:12}}/>
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
            <span key={p} style={{display:'flex',alignItems:'center',gap:4,padding:'3px 10px',borderRadius:99,background:'var(--bg-1)',border:'1px solid var(--border)',fontSize:12}}>
              {p}<button onClick={()=>setBlacklist(l=>l.filter(x=>x!==p))} style={{background:'none',border:'none',cursor:'pointer',color:'var(--danger)',padding:'0 0 0 4px',fontSize:14,lineHeight:1}}>×</button>
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
  return<div style={{color:'var(--danger)',padding:'8px 12px',background:'rgba(239,68,68,.08)',borderRadius:6,border:'1px solid var(--danger)',fontSize:13}}>{children}</div>;
}

function Tag({c,children}){
  const colors={success:'var(--success)',danger:'var(--danger)',mute:'var(--text-mute)'};
  return<span style={{fontSize:11,color:colors[c]||colors.mute}}>{children}</span>;
}

function Modal({children,onClose}){
  return(
    <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,.5)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1000}}
      onClick={e=>e.target===e.currentTarget&&onClose()}>
      <div style={{background:'var(--panel)',borderRadius:12,padding:24,width:480,maxWidth:'90vw',boxShadow:'0 8px 40px rgba(0,0,0,.4)'}}>
        {children}
      </div>
    </div>
  );
}
