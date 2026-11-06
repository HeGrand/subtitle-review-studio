'use strict';
(()=>{
 document.querySelector('.local-badge').textContent=window.subtitleDesktop?'语音在本机处理 · 可拖入路径':'语音在本机处理 · 网页模式';
 const dialog=$('path-browser');let resolvePick=null,kind='folder',listing=null,selection='',lastDirectory='',searchTimer,browseEpoch=0,locationDirty=false;
 const allowed={json:['json'],reference:['docx','pdf','txt','md'],srt:['srt'],media:['mp4','mkv','mov','avi','webm','wav','mp3','m4a','flac','aac']};
 const suffix=name=>name.split('.').at(-1).toLowerCase();
 function finish(value){browseEpoch++;dialog.close();const resolve=resolvePick;resolvePick=null;resolve?.(value);}
 async function loadPath(path,query=''){
  const epoch=++browseEpoch;locationDirty=false;$('browser-location').value=path;$('browser-confirm').disabled=true;$('browser-error').textContent='正在读取文件夹…';
  try{
   const result=await request('browse',{path,kind,query});if(epoch!==browseEpoch||!dialog.open)return;
   listing=result;selection=result.selection||'';lastDirectory=result.path;if(!locationDirty)$('browser-location').value=result.path;
   $('browser-roots').innerHTML=result.roots.map(root=>`<button type="button" data-browse-dir="${esc(root)}">${esc(root)}</button>`).join('');
   $('browser-entries').innerHTML=result.entries.map(row=>`<button type="button" class="browser-entry ${row.path===selection?'selected':''}" data-browse-${row.directory?'dir':'file'}="${esc(row.path)}"><small>${row.directory?'文件夹':'文件'}</small>${esc(row.name)}</button>`).join('')||'<p class="empty">没有符合条件的项目</p>';
   $('browser-error').textContent=result.truncated?'项目较多，请用名称筛选找到目标':'';updateChoice();
  }catch(error){if(epoch===browseEpoch){$('browser-error').textContent=error.message;$('browser-confirm').disabled=true;}}
 }
 function updateChoice(){$('browser-confirm').disabled=locationDirty||(kind!=='folder'&&!selection);$('browser-create').disabled=locationDirty||!listing;$('browser-selected').textContent=locationDirty?'请先打开输入的路径':kind==='folder'?listing?.path||'':selection||'请选择文件';}
 $('browser-location').addEventListener('input',()=>{locationDirty=true;updateChoice();});
 dialog.addEventListener('click',e=>{
  const directory=e.target.closest('[data-browse-dir]');if(directory){$('browser-search').value='';loadPath(directory.dataset.browseDir);}
  const file=e.target.closest('[data-browse-file]');if(file){selection=file.dataset.browseFile;dialog.querySelectorAll('.browser-entry').forEach(row=>row.classList.toggle('selected',row===file));updateChoice();}
 });
 dialog.addEventListener('dblclick',e=>{const file=e.target.closest('[data-browse-file]');if(file)finish(file.dataset.browseFile);});
 $('browser-form').addEventListener('submit',e=>{e.preventDefault();$('browser-search').value='';loadPath($('browser-location').value);});
 $('browser-search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadPath(listing?.path||$('browser-location').value,$('browser-search').value),220);});
 $('browser-parent').onclick=()=>{if(listing){$('browser-search').value='';loadPath(listing.parent);}};
 $('browser-home').onclick=()=>{if(listing){$('browser-search').value='';loadPath(listing.home);}};
 $('browser-cancel').onclick=()=>finish('');dialog.addEventListener('cancel',e=>{e.preventDefault();finish('');});
 $('browser-confirm').onclick=()=>{if(!$('browser-confirm').disabled)finish(kind==='folder'?listing.path:selection);};
 $('browser-create').onclick=handle(async()=>{if(!listing)return;const result=await request('mkdir',{parent:listing.path,name:$('browser-new-name').value});$('browser-new-name').value='';await loadPath(result.path);});
 function pick(nextKind,initial){
  if(window.subtitleDesktop?.pickPath)return window.subtitleDesktop.pickPath(nextKind,initial);
  if(!state.file_access)throw Error('后台尚未更新，请先保存关闭项目并重启本应用');
  kind=nextKind;listing=null;selection='';$('browser-title').textContent=kind==='folder'?'选择本机或共享盘文件夹':'选择文件';$('browser-confirm').textContent=kind==='folder'?'选择当前文件夹':'选择此文件';
  $('browser-new-row').hidden=kind!=='folder';$('browser-search').value='';$('browser-new-name').value='';$('browser-entries').innerHTML='';dialog.showModal();
  const promise=new Promise(resolve=>resolvePick=resolve);loadPath(initial||lastDirectory||'');return promise;
 }
 function absoluteText(value){
  let text=value.trim().split(/\r?\n/).find(line=>line&&!line.startsWith('#'))||'';
  if(text.startsWith('file://')){const url=new URL(text);text=url.hostname?'\\\\'+url.hostname+decodeURIComponent(url.pathname).replaceAll('/','\\'):decodeURIComponent(url.pathname);if(/^\/[a-z]:\//i.test(text))text=text.slice(1).replaceAll('/','\\');}
  text=text.replace(/^"|"$/g,'');return /^(?:[a-z]:[\\/]|\\\\|\/)/i.test(text)?text:'';
 }
 async function physical(files){
  if(!window.subtitleDesktop)return null;
  const paths=files.map(file=>window.subtitleDesktop.filePath(file));
  if(paths.some(path=>!path))return null;
  return window.subtitleDesktop.describePaths(paths);
 }
 function checkFile(file,type){if(!allowed[type].includes(suffix(file.name)))throw Error('文件类型不匹配，请选择 '+allowed[type].map(x=>'.'+x).join('、'));}
 function opinionFeedback(result,label){editorId='';render();$('opinion-source').textContent='已读取：'+label;toast(result.duplicate?'这份意见已导入，本次没有重复添加':`已导入 ${result.accepted} 条意见；${result.conflicts} 项真实冲突；人工进度保留`);}
 async function importOpinions(files){
  if(!state.file_access)throw Error('后台尚未更新，请先保存关闭项目并重启本应用');
  if(files.length!==1)throw Error('一次请拖入一份 AI 意见 JSON');const file=files[0];checkFile(file,'json');
  const project=p()?.project_id;if(!project)throw Error('请先打开项目');
  const zone=$('opinion-drop');zone.classList.add('busy');
  try{
   const paths=await physical(files);let body,label;
   if(paths){if(paths[0].directory)throw Error('请拖入JSON文件');body={path:paths[0].path};label=paths[0].path;}
   else{if(file.size>15*1024*1024)throw Error('JSON较大，请通过本机浏览选择原件');let data;try{data=JSON.parse((await file.text()).replace(/^\uFEFF/,''));}catch{throw Error('JSON无法解析，请检查是否为完整的AI返回文件');}body={data};label=file.name;}
   if(p()?.project_id!==project)throw Error('项目已切换，请在目标项目重新拖入');
   const result=await mutate('import',body);opinionFeedback(result,label);
  }finally{zone.classList.remove('busy');}
 }
 function encode(buffer){const bytes=new Uint8Array(buffer);let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(binary);}
 async function importReferences(files,category){
  if(!state.file_access)throw Error('后台尚未更新，请先保存关闭项目并重启本应用');
  if(!files.length)return;if(!p())throw Error('请先打开项目');files.forEach(file=>checkFile(file,'reference'));const project=p().project_id,zone=$('reference-drop');zone.classList.add('busy');
  try{
   const paths=await physical(files);const labels=[];
   for(let i=0;i<files.length;i++){
    if(p()?.project_id!==project)throw Error('项目已切换，请在目标项目重新拖入');
    if(paths){if(paths[i].directory)throw Error('请拖入剧本或资料文件');await mutate('reference',{path:paths[i].path,kind:category});labels.push(paths[i].path);}
    else{if(files[i].size>12*1024*1024)throw Error('资料超过12MB，请通过本机浏览选择原件');const content=encode(await files[i].arrayBuffer());if(p()?.project_id!==project)throw Error('项目已切换');await mutate('reference-upload',{name:files[i].name,kind:category,content});labels.push(files[i].name);}
   }
   $('reference-source').textContent='已读取：'+labels.join('；');toast('资料已导入，原件和提取文字均已保留');
  }finally{zone.classList.remove('busy');}
 }
 function pathTarget(target){return target.closest('[data-path-kind]')||target.closest('.path-input')?.querySelector('input')||target.closest('label')?.querySelector('[data-path-kind]');}
 for(const id of ['media-dir','srt-dir'])$(id).dataset.pathKind='folder';
 document.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')||pathTarget(e.target)){e.preventDefault();document.querySelectorAll('.drop-active').forEach(zone=>zone.classList.remove('drop-active'));const zone=e.target.closest('.drop-zone,.path-input')||pathTarget(e.target);zone?.classList.add('drop-active');}});
 document.addEventListener('dragleave',e=>{if(!e.currentTarget.contains(e.relatedTarget))document.querySelectorAll('.drop-active').forEach(zone=>zone.classList.remove('drop-active'));});
 document.addEventListener('drop',handle(async e=>{
  const files=Array.from(e.dataTransfer.files),input=pathTarget(e.target),opinion=e.target.closest('#opinion-drop'),reference=e.target.closest('#reference-drop');
  const text=e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain');
  if(!files.length&&!input)return;e.preventDefault();document.querySelectorAll('.drop-active').forEach(zone=>zone.classList.remove('drop-active'));
  if(input){
   let path,info;
   if(files.length){if(files.length!==1)throw Error('此处一次拖入一个文件或文件夹');const paths=await physical(files);if(paths){info=paths[0];path=info.path;}}
   if(!path)path=absoluteText(text);
   if(!path)throw Error('读取文件夹真实路径请使用桌面入口；网页版可点击“浏览”选择，JSON和剧本可直接拖到导入区域');
   const expected=input.dataset.pathKind;
   if(info&&expected!=='any'&&(expected==='folder'?!info.directory:info.directory||!allowed[expected]?.includes(suffix(info.name))))throw Error(expected==='folder'?'此处需要拖入文件夹':'此处需要拖入对应类型的文件');
   input.value=path;input.dispatchEvent(new Event('change',{bubbles:true}));if(input.id==='browser-location')await loadPath(path);else toast('已读取路径：'+path);return;
  }
  if(opinion)return importOpinions(files);
  if(reference)return importReferences(files,$('reference-kind').value);
  if(files.length)toast('请拖到对应路径框、剧本区域或AI意见区域',true);
 }));
 window.localFiles={pick,importOpinions,importReferences,absoluteText,
  chooseOpinions:async()=>{if(!p())throw Error('请先打开项目');const project=p().project_id,path=await picker('json');if(!path)return;if(p()?.project_id!==project)throw Error('项目已切换');opinionFeedback(await mutate('import',{path}),path);},
  chooseReference:async category=>{if(!p())throw Error('请先打开项目');$('reference-kind').value=category;const project=p().project_id,path=await picker('reference');if(!path)return;if(p()?.project_id!==project)throw Error('项目已切换');await mutate('reference',{path,kind:category});$('reference-source').textContent='已读取：'+path;toast('资料已导入');}};
 if(window.subtitleDesktop){window.subtitleDesktop.onCloseRequested(async()=>{
  try{toast('正在保存并关闭，请稍候…');await mutationQueue.catch(()=>{});await flush();await request('pause');await request('close');window.subtitleDesktop.finishClose();}
  catch(error){toast('保存未完成，窗口保持打开：'+error.message,true);window.subtitleDesktop.cancelClose();}
 });}
})();
