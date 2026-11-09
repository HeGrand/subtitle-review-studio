'use strict';
(() => {
 let projectId='',previewKey='',aiBusy=false;
 const drafts=new Map();
 function render(){
  if(p()?.mode!=='generation')return;
  const panel=$('generation-panel');
  if(!panel.dataset.ready){
   panel.dataset.ready='1';
   panel.innerHTML=`<div class="generation-top">
    <div><h2>字幕生成</h2><p class="subtle">选择一集，检查文稿并开始匹配。</p></div>
    <div class="button-row"><button id="generation-ai-review">AI 复查全部集数</button></div>
   </div>
   <details class="card project-source" open><summary>项目剧本 <span id="generation-script-status" class="subtle"></span></summary>
    <div class="source-fields"><div class="path-input"><input id="generation-script-path" aria-label="整本剧本" data-path-kind="reference" placeholder="DOCX / TXT / Markdown / 文字 PDF"><button data-pick="generation-script-path" data-kind="reference">选择</button></div><button id="generation-import-script">导入并按集提取</button></div>
   </details>
   <div class="generation-workbench">
    <div id="generation-queue"></div>
    <article class="card generation-preview">
     <div class="section-title"><div><h3>本集文稿</h3><p id="generation-episode-meta" aria-live="polite"></p></div><div class="button-row"><label>集数<select id="generation-episode"></select></label><label>原声语言<select id="generation-language"><option value="en">英文</option><option value="zh">中文</option></select></label></div></div>
     <div class="button-row generation-primary"><button class="primary" id="generation-start-selected">开始文稿匹配</button><button id="generation-open-review">进入本集听改</button><button id="generation-rematch" hidden>重新匹配</button></div>
     <p id="generation-confirmed" class="subtle"></p>
     <textarea id="generation-lines" rows="11" aria-label="本集提取台词"></textarea>
     <div class="button-row generation-doc-actions"><button id="generation-confirm">保存文稿</button><button id="generation-mark-lines">确认台词</button><button id="generation-ai-extract">AI 提取并断句</button><button id="generation-ai-wrap">AI 文稿断句</button></div>
     <details id="generation-more" class="review-details"><summary>更多</summary><div id="generation-episode-actions" class="button-row"></div><div class="button-row"><button id="generation-add-video">手动指定视频与集数</button><button id="generation-settings">匹配与断句设置</button></div></details>
    </article>
   </div>`;
   on('generation-import-script',async()=>{const path=$('generation-script-path').value.trim().replace(/^"|"$/g,'');if(!path)throw Error('请选择整本剧本');const r=await mutate('generation-script',{path});previewKey='';render();panel.querySelector('.project-source').open=false;toast(`已读取 ${r.episodes.length} 集，提取 ${r.lines} 段台词，请检查本集结果`);});
   on('generation-add-video',()=>addVideos(false));
   on('generation-settings',subtitleSettings);
   on('generation-start-selected',()=>processEpisodes([$('generation-episode').value]));
   on('generation-episode',()=>selectEpisode($('generation-episode').value),'change');
   on('generation-language',()=>{previewKey='';renderPreview();},'change');
   $('generation-lines').addEventListener('input',()=>{drafts.set(previewKey,$('generation-lines').value);renderPreview();});
   on('generation-confirm',async()=>{const ep=$('generation-episode').value,language=$('generation-language').value;await mutate('generation-confirm',{episode:ep,language,lines:$('generation-lines').value.split('\n')});drafts.delete(previewKey);previewKey='';renderPreview();toast('已保存本集文稿');});
   on('generation-mark-lines',async()=>{
    const ep=$('generation-episode').value,language=$('generation-language').value;
    const key=p().project_id+':'+ep+':'+language, text=drafts.get(key);
    const confirmed=!isConfirmed(ep,language);
    if(text!==undefined){
     await mutate('generation-confirm',{episode:ep,language,lines:text.split('\n')});
     if(drafts.get(key)===text)drafts.delete(key);
    }
    await mutate('generation-mark-lines',{episode:ep,language,confirmed});
    previewKey='';renderPreview();
   });
   on('generation-ai-extract',async()=>{
    const ep=$('generation-episode').value,language=$('generation-language').value,key=previewKey,projectId=p().project_id;
    if(!ep)throw Error('请先导入剧本');
    const d=await showDialog('AI 提取并断句',`<p>将第 ${esc(ep)} 集剧本发送到已配置的服务，提取台词并为超长句换行。可能产生接口费用，音视频留在本机。</p>`,'提取并断句');
    if(!d)return;
    aiBusy=true;renderPreview();
    try{
     await mutationQueue.catch(()=>{});
     await flush();
     if(p()?.project_id!==projectId)throw Error('项目已切换，请重试');
     const {result}=await request('generation-ai-extract',{project_id:projectId,revision:p().revision,episode:ep,language});
     if(p()?.project_id!==projectId)return;
     drafts.delete(key);previewKey='';await refresh();
     toast(`已提取并整理 ${result.lines} 行，可直接确认台词${result.fallback?`，其中 ${result.fallback} 段由本地规则断句`:''}`);
    }finally{aiBusy=false;renderPreview();}
   });
   on('generation-ai-wrap',async()=>{
    const ep=$('generation-episode').value,language=$('generation-language').value;
    const key=previewKey,text=$('generation-lines').value,projectId=p().project_id;
    const d=await showDialog('AI 文稿断句',`<p>将第 ${esc(ep)} 集文稿里需要拆分的台词发送给已配置的 AI。可能产生接口费用。断句结果会放回文稿框预览。</p>`,'开始断句');
    if(!d)return;
    aiBusy=true;renderPreview();
    try{
     await mutationQueue.catch(()=>{});
     await flush();
     if(p()?.project_id!==projectId)throw Error('项目已切换，请重试');
     const {result}=await request('generation-ai-wrap',{project_id:projectId,revision:p().revision,episode:ep,language,text});
     if(p()?.project_id!==projectId)throw Error('项目已切换，请重试');
     drafts.set(key,result.lines.join('\n'));previewKey='';renderPreview();
     toast('文稿断句已生成。可预览后保存，或直接开始生成字幕');
    }finally{aiBusy=false;renderPreview();}
   });
   on('generation-ai-review',()=>runAIReview(reviewableEpisodes(),false,true));
   on('generation-open-review',async()=>{
    const ep=$('generation-episode').value;
    await flush();
    if(!p().cues.some(c=>c.episode===ep))throw Error('本集还没有可听改的字幕');
    tab='review';episode=ep;activeId=editorId=anchorId='';selected.clear();
    $('filter-status').value='all';$('filter-kind').value='';$('search').value='';
    window.render(true);
   });
  }
  if(projectId!==p().project_id){projectId=p().project_id;previewKey='';$('generation-script-path').value='';$('generation-episode').innerHTML='';$('generation-language').value=p().episodes[Object.keys(p().episodes)[0]]?.generation_language||'en';}
  const g=p().generation||{},episodes=[...new Set([...Object.keys(g.episode_source||{}),...Object.keys(p().episodes)])].sort((a,b)=>+a-+b),select=$('generation-episode'),selectedEpisode=select.value;
  select.innerHTML=episodes.map(ep=>`<option value="${esc(ep)}">第 ${esc(ep)} 集</option>`).join('');
  select.value=episodes.includes(selectedEpisode)?selectedEpisode:(Object.keys(p().episodes)[0]||episodes[0]||'');
  if(panel.dataset.project!==p().project_id){
   panel.dataset.project=p().project_id;
   panel.querySelector('.project-source').open=!g.reference_id;
  }
  $('generation-script-status').textContent=g.reference_id?`已读取 ${Object.keys(g.episode_source||{}).length} 集 · ${g.lines?.length||0} 段台词`:'尚未导入剧本';
  $('generation-import-script').disabled=state.processing||p().cues.length>0;
  renderPreview();
 }
 function isConfirmed(ep,lang){
  return !drafts.has(p().project_id+':'+ep+':'+lang)&&!!p().generation?.line_confirmations?.[ep+':'+lang];
 }
 function selectEpisode(ep,jump=false){
  const select=$('generation-episode');
  if(![...select.options].some(option=>option.value===ep))return;
  select.value=ep;
  const queued=p().episodes[ep];
  if(queued)$('generation-language').value=queued.generation_language;
  previewKey='';renderPreview();
  if(jump&&matchMedia('(max-width: 900px)').matches)select.closest('.generation-preview').scrollIntoView({block:'nearest'});
 }
 function renderPreview(){
  const ep=$('generation-episode').value,lang=$('generation-language').value,g=p().generation||{},key=p().project_id+':'+ep+':'+lang;
  if(previewKey!==key){previewKey=key;$('generation-lines').value=drafts.get(key)??(g.lines||[]).filter(x=>x.episode===ep&&x.language===lang).map(x=>x.text.replace(/(?<=[A-Za-z])[‘’](?=[A-Za-z])/g,"'")).join('\n');}
  const episodeCues=p().cues.filter(c=>c.episode===ep);
  const generated=episodeCues.length>0||!!p().episodes[ep]?.original_srt;
  const confirmed=isConfirmed(ep,lang),hasText=!!$('generation-lines').value.trim();
  const status=$('generation-confirmed'),mark=$('generation-mark-lines');
  const busyReason=aiBusy?'正在处理 AI 文稿，请稍候':state.processing?'生成队列运行中，请先暂停或等任务结束':'';
  const lockedReason=!generated?'':episodeCues.every(c=>c.accepted.deleted)
   ?'本集字幕已全部标为删除。可在生成队列清空本集字幕，解锁文稿。'
   :'本集已有字幕，原文稿已锁定以保留听改进度';
  const extractReason=lockedReason||busyReason||(!g.episode_source?.[ep]?'本集没有导入的原剧本，可粘贴文稿后使用 AI 文稿断句':'');
  const detail=lockedReason||busyReason||(drafts.has(key)?'有未保存修改':!ep?'先添加音视频或导入剧本':!hasText?'文稿为空，将直接识别':'只匹配文稿');
  status.textContent=(confirmed?'台词已确认':'台词未确认')+' · '+detail;
  mark.textContent=confirmed?'标为未确认':'确认台词';
  mark.disabled=!ep||!hasText||state.processing||aiBusy;
  $('generation-lines').disabled=generated||state.processing||aiBusy;
  $('generation-confirm').disabled=!ep||generated||state.processing||aiBusy;
  const extract=$('generation-ai-extract');
  extract.disabled=!!extractReason;
  extract.title=extractReason;
  extract.setAttribute('aria-busy',String(aiBusy));
  const start=$('generation-start-selected'),queued=p().episodes[ep];
  start.textContent=hasText?'开始文稿匹配':'开始音频识别';
  start.hidden=generated;
  start.disabled=!queued||queued.generation_language!==lang||generated||state.processing||aiBusy;
  const openReview=$('generation-open-review'), rematch=$('generation-rematch');
  openReview.hidden=!generated;
  openReview.disabled=!episodeCues.length;
  rematch.hidden=!generated||!queued;
  rematch.disabled=state.processing||aiBusy;
  rematch.dataset.resetEpisode=ep;
  rematch.dataset.rematch='true';
  const job=p().jobs[ep];
  $('generation-episode-meta').textContent=queued?[queued.video_version,job?.message||queued.local_status].filter(Boolean).join(' · '):
   ep?'这一集还没有关联视频':'导入剧本或新增视频后开始';
  $('generation-episode-actions').innerHTML=queued?`
   ${job?.status==='failed'?'<p class="queue-error">'+esc(job.error?.action||job.message||'本集未生成完成')+'</p>':''}
   ${job?.error?`<button data-copy-error="${esc(ep)}">复制错误详情</button>`:''}
   ${generated?`<button data-reset-episode="${esc(ep)}" ${rematch.disabled?'disabled':''}>清空字幕</button>`:''}
   ${p().versioned_episodes?.includes(ep)?`<button data-episode-versions="${esc(ep)}" ${state.processing?'disabled':''}>对比与恢复旧版</button>`:''}
   <button data-relink="${esc(ep)}" ${state.processing?'disabled':''}>重新定位视频</button>
   <button data-job-action="exclude" data-job-episode="${esc(ep)}" ${state.processing?'disabled':''}>移除本集</button>`:'';
  start.title=queued&&queued.generation_language!==lang?'请选择与生成队列一致的原声语言':'';
  const wrap=$('generation-ai-wrap');
  wrap.disabled=!ep||!hasText||generated||state.processing||aiBusy;
  wrap.title=lockedReason||busyReason||(!hasText?'请先填写本集文稿':'');
  wrap.setAttribute('aria-busy',String(aiBusy));
  document.querySelectorAll('[data-preview-episode]').forEach(button=>{
   const key=button.dataset.previewEpisode;
   button.setAttribute('aria-pressed',String(key===ep));
   const badge=button.parentElement.querySelector('.line-confirmation');
   const queued=p().episodes[key];
   if(!queued)return;
   const done=isConfirmed(key,queued.generation_language);
   badge.textContent=done?'已确认':'未确认';badge.classList.toggle('confirmed',done);
  });
 }
 function episodeFromFilename(name){
  const stem=name.replace(/\.[^.]+$/,'');
  const explicit=[...stem.matchAll(/第\s*(\d{1,6})\s*集|(?:^|[^a-z0-9])(?:episode|ep|e)[ ._-]*(\d{1,6})(?!\d)/gi)];
  const candidates=explicit.length?explicit.map(match=>match[1]||match[2]):
   stem.replace(/\b\d{3,4}p\b|\b[248]k\b|\b[hv]26[45]\b/gi,'').match(/\d+/g)||[];
  const unique=new Set(candidates.map(value=>String(Number(value)).padStart(2,'0')));
  return unique.size===1?[...unique][0]:null;
 }
 async function addVideos(batch){
  const project=p(),eps=Object.keys(project.generation?.episode_source||{});

  const options=keys=>keys.map(ep=>`<option value="${esc(ep)}">第 ${esc(ep)} 集</option>`).join('');
  const language=$('generation-language').value;
  let rows=[];
  if(!batch){
   const d=await showDialog('添加一集视频',`<label>原声语言<select name="language"><option value="en" ${language==='en'?'selected':''}>英文</option><option value="zh" ${language==='zh'?'selected':''}>中文</option></select></label><label>集数${eps.length?`<select name="episode">${options(eps)}</select>`:`<input type="number" name="episode" min="1" max="999999" required value="${Math.max(0,...Object.keys(project.episodes).map(Number))+1}">`}</label>${pathField('generation-media','视频 / 音频','media')}`,'加入生成队列');
   if(!d)return;
   rows=[{episode:d.episode,media:d['generation-media'],language:d.language}];
  }else{
   if(!window.subtitleDesktop?.pickVideos)throw Error('请在字幕工作室桌面窗口使用文件多选');
   const paths=await window.subtitleDesktop.pickVideos();
   if(!paths.length)return;
   if(p()?.project_id!==project.project_id)throw Error('项目已切换，请重新选择视频');
   rows=[...new Set(paths)].map(media=>{
    const name=media.split(/[\\/]/).at(-1);
    return {media,name,episode:episodeFromFilename(name),language};
   });
  }
  const failures=[];let added=0;
  async function add(row){
   if(p()?.project_id!==project.project_id)throw Error('项目已切换，请重新选择视频');
   try{await mutate('generation-video',row);added++;}
   catch(error){failures.push(`${row.name||row.episode}：${error.message}`);}
  }
  if(batch){
   const counts=new Map();
   for(const row of rows)counts.set(row.episode,(counts.get(row.episode)||0)+1);
   const uncertain=[];
   for(const row of rows){
    const reason=!row.episode?'无法确定集数':eps.length&&!eps.includes(row.episode)?`剧本没有第 ${row.episode} 集`:
     p().episodes[row.episode]?`第 ${row.episode} 集已在队列`:counts.get(row.episode)>1?`多个文件指向第 ${row.episode} 集`:'';
    if(reason)uncertain.push({...row,reason});else await add(row);
   }
   if(uncertain.length){
    const available=eps.filter(ep=>!p().episodes[ep]);
    let previous={};
    while(true){
     const d=await showDialog('确认无法自动配对的视频',`<p>已自动加入 ${added} 集。为下列文件选择集数，留空可跳过。</p>${uncertain.map((row,i)=>`<label>${esc(row.name)}<small> · ${esc(row.reason)}</small>${eps.length?`<select name="episode-${i}"><option value="">跳过</option>${options(available)}</select>`:`<input type="number" name="episode-${i}" min="1" max="999999" placeholder="填写集数，留空跳过">`}</label>`).join('')}`,'加入所选集数');
     const selects=document.querySelectorAll('#modal [name^="episode-"]');
     selects.forEach(select=>{select.value=previous[select.name]||'';});
     const chosen=await d;
     if(!chosen)break;
     const picks=Object.values(chosen).filter(Boolean).map(Number);
     if(new Set(picks).size!==picks.length){previous=chosen;toast('同一集只能选择一个视频',true);continue;}
     for(const [i,row] of uncertain.entries())if(chosen['episode-'+i])await add({...row,episode:chosen['episode-'+i]});
     break;
    }
   }
  }else await add(rows[0]);
  if(failures.length)await showDialog('添加结果',`<p>已添加 ${added} 集。</p><pre>${esc(failures.join('\n'))}</pre>`,'知道了');else if(added)toast(`已添加 ${added} 集，可以批量生成`);
 }
 async function subtitleSettings(){
  const s=p().generation?.settings||{};
  const d=await showDialog('匹配与断句设置',`<p>每条最多 44 个字符、最多两行，停顿达到 500 毫秒另起一条。超长时依次优先标点、音频短停顿和语义断点。</p><p>有文稿时只输出能定位的文稿台词，有出入的条目仍会标为待试听。文稿为空时直接识别音频。</p><label>英文每行字符上限<input name="max_line_chars_en" type="number" min="8" max="80" value="${s.max_line_chars_en||42}"></label><label>中文每行字数上限<input name="max_line_chars_zh" type="number" min="8" max="40" value="${s.max_line_chars_zh||18}"></label><label>每条最长秒数<input name="max_cue_seconds" type="number" min="1" max="12" step="0.5" value="${s.max_cue_seconds||6}"></label>`,'保存');
  if(d)await mutate('generation-settings',{settings:d});
 }

 async function adjustSharedBoundary(){
  await flush();
  const c=cue(activeId);
  if(!c)throw Error('请先选一条字幕');
  const next=timingNeighbors(c)[1];
  if(!next||current(next).deleted||c.draft_time_error||next.draft_time_error)throw Error('找不到可调整的下一条，请检查相邻字幕。');
  const left=current(c),right=current(next);
  const row={start_ms:left.start_ms,end_ms:right.end_ms,text:''};
  if(row.end_ms-row.start_ms<2)throw Error('两条字幕的时间范围过短。');
  const point=Math.max(row.start_ms+1,Math.min(row.end_ms-1,Math.round((left.end_ms+right.start_ms)/2)));
  $('player').pause();
  const result=showDialog('调整两条字幕的共同切点',`
   <div class="boundary-text"><div><b>本条</b><p>${esc(left.text)}</p></div><div><b>下一条</b><p>${esc(right.text)}</p></div></div>
   ${SplitWave.markup(true)}<label>共同切点<input name="boundary" value="${time(point)}" required></label>
   <p>保存后，本条出点和下一条入点会同时更新为草稿。</p>`,'保存两个时间点');
  const controller=SplitWave.mount(row,c.episode,point,true);
  let d;
  try{d=await result;}finally{controller.dispose();}
  if(d){await mutate('subtitle-layout',{id:c.id,action:'shared-boundary',
   values:{next_id:next.id,boundary_ms:parseTime(d.boundary),before:[left,right]}});
   renderReview(true);toast('已同步更新两条字幕的时间草稿');}
 }

 async function compareVersions(ep){
  const project=p().project_id;
  const data=await mutate('generation-episode-versions',{episode:ep});
  if(!data.versions.length)throw Error('找不到可读取的旧版本。');
  const comparison=rows=>rows.length?rows.map(r=>`${time(r.start_ms)} → ${time(r.end_ms)}${r.approved?' · 已通过':''}
${r.text}`).join('\n\n'):'（无字幕）';
  const result=showDialog(`第 ${esc(ep)} 集 · 字幕版本对比`,`
   <label>旧版本<select id="version-choice" name="version_id">${data.versions.map(v=>
    `<option value="${esc(v.id)}">${esc(v.at.slice(0,19).replace('T',' '))} · ${v.rows.length} 条</option>`).join('')}</select></label>
   <div class="version-comparison"><div><b>当前已保存字幕</b><pre>${esc(comparison(data.current))}</pre></div>
   <div><b>旧版已保存字幕</b><pre id="version-preview"></pre></div></div>
   <p>恢复只作用于本集。当前版本会先归档，恢复后仍可从这里切回。</p>`,'恢复所选版本');
  const select=$('version-choice');
  const preview=()=>$('version-preview').textContent=comparison(data.versions.find(v=>v.id===select.value).rows);
  select.addEventListener('change',preview);preview();
  const choice=await result;
  if(!choice)return;
  if(p()?.project_id!==project)throw Error('项目已切换，请重试。');
  await mutate('generation-restore-version',{episode:ep,version_id:choice.version_id});
  for(const lang of ['en','zh'])drafts.delete(project+':'+ep+':'+lang);
  selectEpisode(ep);
  toast('已恢复本集旧版字幕、文稿和听改记录');
 }

 async function changeLayout(action){
  await flush();const c=cue(activeId);if(!c)throw Error('请先选择字幕');const r=current(c);
  if(action==='split'){
   const input=$('edit-text'),middle=Math.floor(r.text.length/2),cursor=input.selectionStart>0&&input.selectionStart<r.text.length?input.selectionStart:(r.text.lastIndexOf(' ',middle)>0?r.text.lastIndexOf(' ',middle):middle),point=originalNow()>r.start_ms&&originalNow()<r.end_ms?originalNow():Math.round((r.start_ms+r.end_ms)/2);
   if(r.end_ms-r.start_ms<2)throw Error('本条时间过短，无法拆分');
   $('player').pause();
   const result=showDialog('拆分此条字幕',`${SplitWave.markup()}<label>前半句<textarea name="left" rows="2" required>${esc(r.text.slice(0,cursor).trim())}</textarea></label><label>后半句<textarea name="right" rows="2" required>${esc(r.text.slice(cursor).trim())}</textarea></label><label>拆分时间<input name="boundary" value="${time(point)}" required></label>`,'保存拆分');
   const controller=SplitWave.mount(r,c.episode,point);let d;
   try{d=await result;}finally{controller.dispose();}
   if(d)await mutate('subtitle-layout',{id:c.id,action:'split',
    values:{left:d.left,right:d.right,boundary_ms:parseTime(d.boundary),before:r}});
  }else{
   const previous=action==='merge-prev';
   const rows=p().cues.filter(x=>x.episode===c.episode&&!current(x).deleted)
    .sort((a,b)=>current(a).start_ms-current(b).start_ms||(a.id<b.id?-1:1));
   const index=rows.findIndex(x=>x.id===c.id);
   if(index<0)throw Error('请选择有效字幕');
   const neighbor=rows[index+(previous?-1:1)];
   if(!neighbor)throw Error(previous?'前面没有字幕':'后面没有字幕');
   const [first,second]=previous?[neighbor,c]:[c,neighbor];
   const join=p().episodes[c.episode].generation_language==='zh'?'':' ';
   const mergedText=[first,second].map(x=>current(x).text.replaceAll('\n',' ')).join(join);
   const approvedWarning=[first,second].some(x=>x.status==='approved')?'<p>其中有已通过的字幕。合并后，新条目需要重新确认。</p>':'';
   const d=await showDialog(previous?'与上一条合并':'与下一条合并',`<p>合并后仍需听改，原始条目和操作历史保留，可撤销。</p>${approvedWarning}<label>合并文字<textarea name="text" rows="4">${esc(mergedText)}</textarea></label>`,'保存合并');
   if(d){
    const result=await mutate('subtitle-layout',{id:first.id,action:'merge',values:{text:d.text,next_id:second.id}});
    activeId=anchorId=result.id;selected=new Set([result.id]);
   }
  }
  editorId='';window.render(true);
 }
 on('split-cue',()=>changeLayout('split'));
 on('merge-prev',()=>changeLayout('merge-prev'));
 on('merge-next',()=>changeLayout('merge-next'));
 async function saveDrafts(episodes){
  const id=p().project_id;
  for(const ep of episodes){
   const language=p().episodes[ep].generation_language;
   const key=id+':'+ep+':'+language;
   if(!drafts.has(key))continue;
   const text=drafts.get(key);
   await mutate('generation-confirm',{episode:ep,language,lines:text.split('\n')});
   if(drafts.get(key)===text)drafts.delete(key);
  }
  previewKey='';renderPreview();
 }
 on('queue-add-video',()=>addVideos(true));
 on('shared-boundary',adjustSharedBoundary);
 window.GenerationUI={render,saveDrafts,isConfirmed,selectEpisode,compareVersions};
 on('home-generate',()=>projectDialog(true,'generation'));
})();
