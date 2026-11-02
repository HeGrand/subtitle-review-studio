'use strict';
const $ = id => document.getElementById(id);
const TOKEN = document.querySelector('meta[name="app-token"]').content;
const CLIENT = sessionStorage.getItem('subtitle-client') || crypto.randomUUID();
sessionStorage.setItem('subtitle-client', CLIENT);
let state = {project:null,recent:[],models:[],processing:false};
let tab = 'materials', episode = '', selected = new Set(), activeId = '', editorId = '', anchorId = '';
let dragging = null, dirty = null, saveTimer = null, saving = Promise.resolve(), modalResolve = null;
let modelOverride = '', clipOffset = 0, loop = false, mediaMode = 'audio', playingEpisode = '', peaks = [];
let toastTimer, pollBusy = false, visibleRows = [], editorEpoch = 0;
let playbackRange=null, rangeTimer=null;
let mutationQueue=Promise.resolve();
let oldBackendWarned=false;
let outsideSelected=new Set();
let completionPromptBusy=false;
function initialCheckCompletionKey(project,processing){
 if(!project||processing)return '';
 const episodes=Object.keys(project.episodes).sort();
 if(!episodes.length||!episodes.every(ep=>project.jobs[ep]?.status==='skipped'||(project.jobs[ep]?.status==='done'&&project.episodes[ep].transcript)))return '';
 return 'subtitle-initial-complete:'+project.project_id+':'+JSON.stringify(episodes.map(ep=>[ep,project.jobs[ep],project.episodes[ep].transcript]));
}
async function offerAIExport(){
 const key=initialCheckCompletionKey(p(),state.processing);
 if(!key||completionPromptBusy||picking||dirty||$('modal').open||sessionStorage.getItem(key))return;
 completionPromptBusy=true;
 try{
  sessionStorage.setItem(key,'shown');
  const projectId=p().project_id;
  const skipped=Object.keys(p().episodes).filter(ep=>p().jobs[ep]?.status==='skipped');
  const dialog=showDialog(skipped.length?'本轮初查已结束，部分集数已跳过':'全部集数本地初查已完成',`<p>${Object.keys(p().episodes).length-skipped.length} 集已完成本地语音识别和初查。${skipped.length?`另有 ${skipped.length} 集跳过初查，AI包将如实保留缺失证据说明，不算初查成功。`:''}下一步导出 AI 交接包，由 AI 对照字幕、剧本及上下文审查。</p><p>是否现在前往“AI 交接与导出”？</p>`,'前往 AI 交接与导出');
  $('modal-cancel').textContent='稍后';
  const result=await dialog;
  $('modal-cancel').textContent='取消';
  if(result&&p()?.project_id===projectId){await flush();tab='exchange';render();$('export-ai-light').scrollIntoView({block:'center'});}
 }finally{completionPromptBusy=false;}
}
const certainButton=document.createElement('button');certainButton.id='batch-certain';certainButton.textContent='采用确定文字建议';$('batch-approve').before(certainButton);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const p = () => state.project;
const cue = id => p()?.cues.find(c => c.id === id);
const current = c => c.draft || c.accepted;
function setState(next){if(next.project&&state.project&&next.project.project_id===state.project.project_id&&next.project.revision<state.project.revision)return false;if(next.project&&!next.project.review_counts){const counts={};for(const c of next.project.cues){c.category=c.status==='approved'?'approved':c.draft?'draft':'incomplete';c.category_label=c.category==='approved'?'已人工通过':c.category==='draft'?'人工草稿':'文字审查未完成';counts[c.category]=(counts[c.category]||0)+1;}next.project.review_counts=counts;if(!oldBackendWarned){oldBackendWarned=true;toast('后台仍是旧版本，请保存关闭项目后使用重新启动入口；当前内容没有被当成已审。',true);}}state=next;return true;}
const time = ms => {ms=Math.max(0,Math.round(ms));return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')},${String(ms%1000).padStart(3,'0')}`;};
function parseTime(s){const m=/^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$/.exec(s.trim());if(!m||+m[2]>59||+m[3]>59)throw Error('时间码格式应为 00:00:00,000');return ((+m[1]*60 + +m[2])*60 + +m[3])*1000 + +m[4];}
function toast(message,error=false){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:5500);}
async function request(route,body,method='POST'){
 const response=await fetch('/api/'+route,{method,headers:{'Content-Type':'application/json','X-App-Token':TOKEN,'X-Client':CLIENT},...(method==='GET'?{}:{body:JSON.stringify(body||{})})});
 const data=await response.json();if(!response.ok)throw Error(data.error||'操作失败');return data;
}
async function refresh(renderEditor=false){setState(await request('state',null,'GET'));render(renderEditor);}
function mutate(route,body={}){const target=p()?.project_id;const operation=async()=>{await flush();if(target!==p()?.project_id)throw Error('项目已切换，请重新操作');const data=await request(route,{...body,project_id:target,revision:p()?.revision});await refresh();return data.result;};const task=mutationQueue.catch(()=>{}).then(operation);mutationQueue=task;return task;}
function handle(fn){return async e=>{try{await fn(e);}catch(error){toast(error.message,true);$('save-status').textContent=dirty?'保存失败，请保留页面':'操作未完成';}};}
function on(id,fn,event='click'){$(id).addEventListener(event,handle(fn));}
let picking=false;
async function picker(kind='folder',initial=''){
 if(picking){toast('请先完成当前文件选择');return '';}
 picking=true;
 try{return await window.localFiles.pick(kind,initial);}finally{picking=false;}
}

function showDialog(title,content,ok='确定'){
 if($('modal').open)throw Error('请先完成当前对话框。');
 $('modal-content').innerHTML=`<h2>${esc(title)}</h2>${content}<p class="dialog-error" id="dialog-error"></p>`;
 $('modal-ok').textContent=ok;$('modal').showModal();
 return new Promise(resolve=>modalResolve=resolve);
}
function closeDialog(result){$('modal').close();const resolve=modalResolve;modalResolve=null;resolve?.(result);}
$('modal-cancel').onclick=()=>closeDialog(null);
$('modal').addEventListener('cancel',e=>{e.preventDefault();closeDialog(null);});
$('modal-form').addEventListener('submit',e=>{e.preventDefault();const values={};$('modal-content').querySelectorAll('[name]').forEach(el=>values[el.name]=el.type==='checkbox'?el.checked:el.value);closeDialog(values);});
document.addEventListener('click',handle(async e=>{const b=e.target.closest('[data-pick]');if(!b)return;const path=await picker(b.dataset.kind,$(b.dataset.pick).value);if(path)$(b.dataset.pick).value=path;}));
function pathField(id,label,kind='folder',value=''){return `<label>${esc(label)}<div class="path-input"><input id="${id}" name="${id}" data-path-kind="${kind}" value="${esc(value)}" placeholder="拖入对应文件或文件夹，或浏览/粘贴完整路径"><button type="button" data-pick="${id}" data-kind="${kind}">选择</button></div></label>`;}

async function projectDialog(create){
 await mutationQueue.catch(()=>{});
 await flush();const d=await showDialog(create?'新建独立项目':'打开 / 切换项目',`<p>${create?'请选择程序目录以外的空文件夹，项目进度和处理证据都保存在这里。':'选择包含 project.json 的文件夹。切换前会保存当前项目，并停止本地任务。'}</p>${create?'<label>项目名称<input name="name" id="new-name" placeholder="例如：第一季英语字幕"></label>':''}${pathField('project-folder','项目文件夹')}`,create?'创建项目':'保存并打开');
 if(!d)return;if(!d['project-folder'])throw Error('未选择文件夹，项目没有创建或切换。');
 releasePlayer();await request(create?'create':'open',{folder:d['project-folder'],name:d.name});resetProjectUI();await refresh();toast(create?'新项目已创建':'项目已打开，进度已恢复');
}
function resetProjectUI(){for(const id of ['opinion-source','reference-source'])$(id).textContent='';$('model-path').value='';selected.clear();outsideSelected.clear();activeId=editorId=anchorId=episode='';modelOverride='';tab='materials';dirty=null;clearTimeout(saveTimer);$('media-dir').value='';$('srt-dir').value='';$('search').value='';$('filter-kind').value='';$('filter-status').value='pending';$('pair-results').hidden=true;$('export-result').hidden=true;editorEpoch++;}
function releasePlayer(){const player=$('player');player.pause();player.removeAttribute('src');player.load();$('subtitle-overlay').textContent='';playingEpisode='';clipOffset=0;peaks=[];drawWave();}

function render(forceEditor=false){
 const project=p();$('home').hidden=!!project;$('workspace').hidden=!project;$('save-close').hidden=!project;
 if(!project){$('recent-list').innerHTML=state.recent.length?state.recent.map((r,i)=>`<div class="recent-item"><div>${esc(r.name)}<p>${esc(r.path)}</p></div><button data-recent="${i}">继续审核 ↗</button></div>`).join(''):'<div class="empty">还没有项目，从新建开始。</div>';return;}
 $('project-name').textContent=project.name;$('project-path').textContent=project.root;
 $('save-status').textContent=dirty?'正在保存草稿…':`已保存 · ${project.saved_at.slice(11,19)}`;
 const pending=project.cues.filter(c=>['human','draft'].includes(c.category)).length;
 $('project-stats').textContent=`${Object.keys(project.episodes).length} 集 · ${project.cues.length} 条 · ${pending} 具体人工/草稿 · ${project.review_counts?.incomplete||0} 文字未完成 · ${(project.review_counts?.text_clear||0)+(project.review_counts?.ai_corrected||0)} 文字已核对 · ${project.review_counts?.term||0} 术语集中确认 · ${project.review_counts?.outside_scope||0} 范围外记录 · ${project.review_counts?.extra_tasks||0} 额外任务${project.finished?' · 本轮已完成':''}`;
 document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));
 for(const name of ['materials','review','exchange'])$('tab-'+name).hidden=tab!==name;
 renderMaterials();renderExchange();if(tab==='review')renderReview(forceEditor);
 setTimeout(()=>offerAIExport().catch(e=>toast(e.message,true)),0);
}
function renderMaterials(){
 const speed=state.acceleration||{},measure=speed.measurements||{};
 $('acceleration-reset').disabled=state.acceleration===undefined||state.processing;
 $('acceleration-status').textContent=state.acceleration===undefined?'后台尚未更新为自动加速版，请保存关闭项目后重启本应用。':speed.message?`${speed.message}${measure.cuda?.seconds!=null?` · GPU ${measure.cuda.seconds.toFixed(2)}秒`:''}${measure.cpu?.seconds!=null?` / CPU ${measure.cpu.seconds.toFixed(2)}秒`:''}（相同短段测速）`:'自动检测设备；首次使用同段音频比较 GPU 和 CPU 速度，之后复用结果。GPU不可用时自动改用CPU。';
 $('reference-list').innerHTML=p().references.map(r=>`<div class="reference-item">${esc({script:'剧本',names:'人物名表',terms:'术语表'}[r.kind])} · ${esc(r.name)}<small>${esc(r.warning)}</small></div>`).join('');
 const bundled=m=>m.replaceAll('\\','/').includes('/.runtime/models/');
 if(document.activeElement!==$('model-path'))$('model-path').value=modelOverride||$('model-select').value;
 const modelValue=$('model-select').value;const opts=state.models.map(m=>`<option value="${esc(m)}">${bundled(m)?'内置多语言模型 · '+esc(m.split(/[\\/]/).at(-1)):'已有本地资源 · '+esc(m.split(/[\\/]/).at(-4)||'Whisper')}</option>`).join('');
 $('model-select').innerHTML=`<option value="">${state.models.some(bundled)?'使用内置多语言模型（无需下载）':'首次自动下载 small 多语言模型'}</option>`+opts+(modelOverride?`<option value="${esc(modelOverride)}">指定资源 · ${esc(modelOverride)}</option>`:'');
 $('model-select').value=modelOverride||modelValue||(state.models[0]||'');
 const expandedJobs=new Set([...$('episode-status').querySelectorAll('details[open][data-error-ep]')].map(d=>d.dataset.errorEp));
 const expandedExcluded=!!$('episode-status').querySelector('.excluded-episodes[open]');
 $('episode-status').innerHTML=Object.entries(p().episodes).map(([ep,e])=>{
  const j=p().jobs[ep]||{},error=j.status==='failed'?(j.error||{title:'本集初查未完成',action:'请查看详细错误；可以重试、重新定位，或跳过本集初查。',detail:j.message}):null;
  return `<div class="episode-status-row ${esc(j.status||'')}"><b>第 ${esc(ep)} 集</b><span>${esc(error?.title||j.message||e.local_status)}${j.seconds?`<small> · 本次 ${j.seconds}s / 音频 ${j.audio_seconds||'?'}s</small>`:''}${e.warnings.length?`<small> · ${esc(e.warnings.join('；'))}</small>`:''}${error?`<p>${esc(error.action)}</p><details data-error-ep="${ep}" ${expandedJobs.has(ep)?'open':''}><summary>详细错误与处理入口</summary><pre>${esc(error.detail)}</pre><button data-copy-error="${ep}">复制错误</button><button data-job-action="skip" data-job-episode="${ep}">跳过本集初查</button><button data-job-action="exclude" data-job-episode="${ep}">从本轮移出（可恢复）</button></details>`:''}${j.status==='skipped'?'<small> · 保留字幕参与文字审查，未标为初查成功</small>':''}${j.status==='paused'?`<button data-job-action="skip" data-job-episode="${ep}">跳过本集初查</button>`:''}</span><progress max="100" value="${j.progress||0}"></progress><button data-retry="${ep}" ${state.processing?'disabled':''}>${j.status==='failed'?'重试':j.status==='skipped'?'重新初查':'处理 / 继续'}</button><button data-relink="${ep}" ${state.processing?'disabled':''}>重新定位</button></div>`;
 }).join('')||'<div class="empty">导入素材后，这里会显示每集进度。</div>';
 const archived=Object.entries(p().excluded_episodes||{});
 if(archived.length)$('episode-status').innerHTML+=`<details class="excluded-episodes" ${expandedExcluded?'open':''}><summary>已从本轮移出 ${archived.length} 集（不参与本轮导出，可恢复）</summary>${archived.map(([ep,r])=>`<p>第 ${esc(ep)} 集：${esc(r.reason)} <button data-job-action="restore" data-job-episode="${ep}">恢复本集</button></p>`).join('')}</details>`;

}
function renderExchange(){
 $('term-groups').innerHTML=(p().term_groups||[]).map(g=>`<article class="opinion-card"><b>${esc(g.canonical)} · ${g.members.length} 处 · ${g.pending} 处待统一</b><p>${esc(g.meaning_warning)}</p><details><summary>预览本组全部位置及已保护项</summary>${g.members.map(m=>`<p>第${m.episode}集 #${m.number}：${esc(m.text)} ${m.protected?'【保留人工进度】':m.handled?'【已处理】':''}${m.other_problem?'【另有问题需保留】':''}</p>`).join('')}</details><button data-term-group="${g.id}" ${g.pending?'':'disabled'}>确认本组统一方式</button></article>`).join('')||'<div class="empty">未发现需要集中确认的重复术语。</div>';
 $('review-tasks').innerHTML=(p().review_tasks||[]).filter(t=>!t.resolved&&t.in_scope!==false).map(t=>`<div class="conflict-row"><div><b>${esc(t.kind==='incomplete'?'审查未完成':'具体疑点')} · 第${esc(t.episode||'?')}集 · ${time(t.start_ms||0)}</b><p>${esc(t.reason)}</p>${t.related_stable_ids?.length?`<button data-locate-task="${esc(t.id)}">定位字幕</button>`:''}</div><button data-resolve-task="${esc(t.id)}">记录处理结果</button></div>`).join('')||'<div class="empty">没有额外的具体任务。</div>';
 $('review-limits').innerHTML=(p().review_limits||[]).filter(t=>!t.superseded).map(t=>`<p>第${esc(t.episode||'?')}集：${esc(t.reason)}</p>`).join('')||'<p>实际审查范围见各轮coverage；未记录不代表已逐句试听。</p>';
 const value=$('export-scope').value;$('export-scope').innerHTML='<option value="all">全部集数</option>'+Object.keys(p().episodes).map(ep=>`<option value="${ep}">第 ${ep} 集</option>`).join('');$('export-scope').value=value&&([...$('export-scope').options].some(x=>x.value===value))?value:'all';
 $('ai-rounds').innerHTML=p().ai_rounds.map(r=>`<div class="round"><b>${r.imported?'已收到AI意见，等待人工复核':'已导出 / 等待外部AI意见'}</b>${esc(r.at.slice(0,19))} · ${r.full_audio?'完整音频选项':'轻量包'}</div>`).join('')||'<div class="empty">还未导出 AI 交接包。</div>';
 const conflicts=p().opinion_conflicts.filter(x=>!x.resolved);$('conflict-count').textContent=`${conflicts.length} 项未决`;
 $('conflict-list').innerHTML=conflicts.map(x=>`<div class="conflict-row"><div><strong>${esc(x.reason)}</strong><p>${esc(JSON.stringify(x.opinion,null,2))}</p></div><button data-resolve-conflict="${esc(x.id)}">已查看并处理</button></div>`).join('')||'<div class="empty">没有待处理的导入冲突。</div>';
}
function filtered(){
 const status=$('filter-status').value,kind=$('filter-kind').value,q=$('search').value.toLowerCase();
 return p().cues.filter(c=>{const r=current(c);if(episode&&c.episode!==episode)return false;if(status==='pending'&&(!['human','draft'].includes(c.category)||r.deleted))return false;if(!['pending','all','approved','deleted','candidate'].includes(status)&&c.category!==status)return false;if(status==='candidate'&&!p().issues.some(i=>i.cue_id===c.id&&!i.resolved&&i.triage?.result!=='dismissed'))return false;if(status==='approved'&&(c.status!=='approved'||r.deleted))return false;if(status==='deleted'&&!r.deleted)return false;if(kind&&!p().issues.some(i=>i.cue_id===c.id&&i.kind===kind&&!i.resolved&&i.triage?.result!=='dismissed'))return false;return !q||[r.text,c.original.text,c.note,c.source_number,c.episode].join(' ').toLowerCase().includes(q);}).sort((a,b)=>Number(a.episode)-Number(b.episode)||current(a).start_ms-current(b).start_ms||a.id.localeCompare(b.id));
}
function renderReview(forceEditor=false){
 const kinds=[...new Set(p().issues.map(i=>i.kind))];const kind=$('filter-kind').value;$('filter-kind').innerHTML='<option value="">所有问题类型</option>'+kinds.map(x=>`<option>${esc(x)}</option>`).join('');$('filter-kind').value=kind;
 $('episode-list').innerHTML=Object.keys(p().episodes).map(ep=>`<button class="${episode===ep?'active':''}" data-episode="${ep}">${ep}<span>${p().cues.filter(c=>c.episode===ep&&['human','draft'].includes(c.category)&&!current(c).deleted).length}</span></button>`).join('');
 $('all-episodes').classList.toggle('active',!episode);
 visibleRows=filtered();const allowed=new Set(visibleRows.map(c=>c.id));selected=new Set([...selected].filter(id=>allowed.has(id)));
 renderCueList();renderEditor(forceEditor);
 const stand=p().issues.filter(i=>!i.cue_id&&(!episode||i.episode===episode)&&!i.resolved&&!i.excluded_by_user&&i.triage?.result!=='dismissed').filter((i,n,all)=>all.findIndex(x=>(x.triage?.group_id||x.id)===(i.triage?.group_id||i.id))===n);
 $('standalone-issues').innerHTML=stand.length?`<details class="card"><summary>范围外记录（无对应字幕）· ${stand.length} 组，不影响导出</summary><p>本项目只审已有字幕，无须补写或逐条人工处理。可选中后批量收起，原证据保留。</p><div class="button-row"><button id="outside-all">全选当前范围外记录</button><button id="outside-none">取消选择</button><button id="outside-ignore">批量收起所选</button></div>${stand.map(i=>`<div class="evidence"><label><input type="checkbox" data-outside="${i.id}" ${outsideSelected.has(i.id)?'checked':''}> ${esc(i.kind)} · 第${i.episode}集 ${time(i.start_ms)}</label><p>${esc(i.evidence_text)}</p><button data-play-issue="${i.id}">试听证据（可选）</button></div>`).join('')}</details>`:'';
 if(stand.length){$('outside-all').onclick=()=>{outsideSelected=new Set(stand.map(i=>i.id));document.querySelectorAll('[data-outside]').forEach(x=>x.checked=true);};$('outside-none').onclick=()=>{outsideSelected.clear();document.querySelectorAll('[data-outside]').forEach(x=>x.checked=false);};$('outside-ignore').onclick=handle(async()=>{const groups=new Set(p().issues.filter(i=>outsideSelected.has(i.id)).map(i=>i.triage?.group_id||i.id));const ids=p().issues.filter(i=>!i.cue_id&&groups.has(i.triage?.group_id||i.id)).map(i=>i.id);if(!ids.length)throw Error('请先选择范围外记录');await mutate('scope-ignore',{ids});outsideSelected.clear();renderReview();toast('已批量收起；未标记为听审通过，也不修改字幕');});}
}
function renderCueList(){
 const list=$('cue-list'),scroll=list.scrollTop;
 list.innerHTML=visibleRows.map(c=>{const r=current(c),issues=p().issues.filter(i=>i.cue_id===c.id&&!i.resolved&&i.triage?.result!=='dismissed');return `<article class="cue-row ${selected.has(c.id)?'selected':''} ${c.id===activeId?'active':''} ${r.deleted?'deleted':''}" data-id="${c.id}"><div class="cue-meta"><input type="checkbox" data-check="${c.id}" ${selected.has(c.id)?'checked':''} aria-label="选择第${c.episode}集第${c.source_number||'新增'}条"><span>${c.episode} / ${c.source_number||'新增'}</span><span>${time(r.start_ms)} → ${time(r.end_ms)}</span><span class="status ${c.draft?'draft':c.status}">${r.deleted?(c.merged_into?'已合并映射记录':'已删除'):c.draft?'草稿 · 待确认':c.category_label||'文字审查未完成'}</span></div><div class="cue-text">${esc(r.text)}</div>${c.draft&&c.draft.text!==c.accepted.text?`<div class="cue-original">当前基线：${esc(c.accepted.text)}</div>`:''}${issues.length?`<div class="cue-issue">${esc([...new Set(issues.map(i=>i.kind))].join(' · '))}</div>`:''}${c.opinions.length?`<div class="cue-issue">外部 AI 意见 ${c.opinions.length} 条</div>`:''}</article>`;}).join('')||'<div class="empty">当前筛选没有字幕。</div>';
 list.scrollTop=scroll;updateSelection();
}
function updateSelection(){
 $('selection-count').textContent=`已选 ${selected.size} / 当前 ${visibleRows.length} 条`;
 document.querySelectorAll('.cue-row').forEach(el=>{el.classList.toggle('selected',selected.has(el.dataset.id));el.classList.toggle('active',el.dataset.id===activeId);const cb=el.querySelector('input');if(cb)cb.checked=selected.has(el.dataset.id);});
}
function renderEditor(force=false){
 const c=cue(activeId);$('editor-empty').hidden=!!c;$('editor').hidden=!c;
 if(!c){editorId='';$('editor-key').textContent='未选择';$('opinion-list').innerHTML='';$('active-review-reason').hidden=true;return;}
 if(editorId===c.id&&!force)return;
 editorId=c.id;const r=current(c);const reasons=c.human_reasons||[];$('active-review-reason').hidden=!['human','draft','term'].includes(c.category);$('active-review-reason').textContent=c.category==='term'?'本条只需集中确认术语，请到 AI交接与导出 → 整部剧术语统一确认；不用逐条处理。':'本条仍需处理的具体原因：\n'+(reasons.join('\n')||'请核对本条待处理记录');$('editor-key').textContent=`${c.episode} / ${c.source_number||'新增'}`;
 $('original-text').textContent=c.original.text;$('edit-text').value=r.text;$('edit-start').value=c.draft_time_inputs?.start??time(r.start_ms);$('edit-end').value=c.draft_time_inputs?.end??time(r.end_ms);$('edit-note').value=c.note;
 const around=p().cues.filter(x=>x.episode===c.episode).sort((a,b)=>current(a).start_ms-current(b).start_ms),i=around.findIndex(x=>x.id===c.id);
 $('context-before').textContent=i>0?'上一条：'+current(around[i-1]).text:'';$('context-after').textContent=i<around.length-1?'下一条：'+current(around[i+1]).text:'';
 $('evidence-list').innerHTML=p().issues.filter(i=>i.cue_id===c.id).map(i=>`<div class="evidence"><strong>${esc(i.kind)} · 原片 ${time(i.start_ms)}</strong><p>${esc(i.message)}\n${esc(i.evidence_text)}</p><button data-play-issue="${i.id}">试听疑点片段</button></div>`).join('');
 $('opinion-list').innerHTML=c.opinions.map((o,n)=>{const s=ReviewHelpers.summary(o);return `<article class="opinion-card"><div class="opinion-title">AI 意见 ${n+1}${n===c.opinions.length-1?' · 最新':!o.text_review&&c.text_review?' · 历史记录，已补充筛查':''}<span>${esc({clear:'文字未见具体问题',correction:'确定修正',human:'具体疑点',incomplete:'文字审查未完成'}[o.text_review?.result]||{high:'较确定',medium:'需核对',low:'不确定'}[o.confidence]||'待核对')}</span></div><p class="opinion-problem">${esc(s.problem)}</p><dl><dt>剧本对应台词 / 定位情况</dt><dd>${esc(s.script)}</dd><dt>当前 SRT 字幕（已保存基线）</dt><dd>${esc(c.accepted.text)}</dd><dt>机器识别</dt><dd>${esc(s.asr)}</dd><dt>处理建议</dt><dd>${esc(s.advice)}</dd></dl>${s.uncertainty?`<p class="opinion-uncertain">待确认：${esc(s.uncertainty)}</p>`:''}${o.suggestion?`<button data-use-opinion="${n}">采用为待确认草稿</button>`:''}<details><summary>详细依据与原始意见</summary><p>${esc(o.reason)}</p><p>${esc(o.evidence?.map(e=>e.path+'：'+e.detail).join('\n'))}</p></details></article>`;}).reverse().join('')||'<div class="no-opinion">本条暂无 AI 意见；可查看下方本地疑点。</div>';
 document.querySelector('.inspector').scrollTop=0;
 if(playingEpisode!==c.episode||mediaMode==='clip')loadMedia(c.episode,'audio',r.start_ms);
 else{$('player').pause();$('player').currentTime=r.start_ms/1000;}
 playbackRange={episode:c.episode,start:r.start_ms,end:r.end_ms};drawWave();
}

function queueDraft(){
 if(!cue(activeId))return;
 dirty={id:activeId,values:{text:$('edit-text').value,start_input:$('edit-start').value,end_input:$('edit-end').value,note:$('edit-note').value}};
 $('save-status').textContent='正在保存草稿…';clearTimeout(saveTimer);saveTimer=setTimeout(()=>flush().catch(e=>toast(e.message,true)),450);
}
async function flush(){
 clearTimeout(saveTimer);
 const pending=dirty;dirty=null;
 const task=async()=>{
  if(!pending)return;
  try{
   await request('edit',{ids:[pending.id],action:'editor',values:pending.values,revision:p().revision});
   setState(await request('state',null,'GET'));
   $('save-status').textContent=`已保存 · ${p().saved_at.slice(11,19)}`;
   if(tab==='review'){visibleRows=filtered();renderCueList();}
  }catch(error){if(!dirty)dirty=pending;$('save-status').textContent='保存失败，请重试';throw error;}
 };
 saving=saving.catch(()=>{}).then(task);await saving;
}
async function activate(id){if(activeId===id)return;await flush();$('player').pause();playbackRange=null;activeId=id;renderEditor(true);updateSelection();}
function selectRange(start,end,add=false){const a=visibleRows.findIndex(c=>c.id===start),b=visibleRows.findIndex(c=>c.id===end);if(a<0||b<0)return;if(!add)selected.clear();for(let i=Math.min(a,b);i<=Math.max(a,b);i++)selected.add(visibleRows[i].id);}
$('cue-list').addEventListener('pointerdown',handle(async e=>{
 if(e.button!==0||e.target.closest('input,button'))return;const row=e.target.closest('[data-id]');if(!row)return;
 e.preventDefault();const id=row.dataset.id;
 if(e.shiftKey&&anchorId)selectRange(anchorId,id,e.ctrlKey||e.metaKey);
 else if(e.ctrlKey||e.metaKey){selected.has(id)?selected.delete(id):selected.add(id);anchorId=id;}
 else{selected=new Set([id]);anchorId=id;dragging={start:id,base:new Set(selected)};}
 updateSelection();await activate(id);
}));
$('cue-list').addEventListener('pointerover',e=>{if(!dragging||!(e.buttons&1))return;const row=e.target.closest('[data-id]');if(row){selectRange(dragging.start,row.dataset.id);updateSelection();}});
document.addEventListener('pointerup',()=>dragging=null);
document.addEventListener('change',e=>{if(e.target.dataset.outside){if(e.target.checked)outsideSelected.add(e.target.dataset.outside);else outsideSelected.delete(e.target.dataset.outside);}});
$('cue-list').addEventListener('change',handle(async e=>{const id=e.target.dataset.check;if(id){e.target.checked?selected.add(id):selected.delete(id);anchorId=id;await activate(id);updateSelection();}}));
document.addEventListener('keydown',handle(async e=>{if(e.target.closest('input,textarea,select')||$('modal').open||$('path-browser').open)return;if(tab==='review'&&(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'){e.preventDefault();selected=new Set(visibleRows.map(c=>c.id));updateSelection();}if(tab==='review'&&e.key==='Delete'&&selected.size){e.preventDefault();await deleteSelected([...selected]);}}));
document.addEventListener('keydown',handle(async e=>{if(tab!=='review'||e.code!=='Space'||e.ctrlKey||e.metaKey||e.altKey||$('modal').open||$('path-browser').open||e.target.closest('input,textarea,select,[contenteditable="true"]'))return;e.preventDefault();if(e.repeat)return;await togglePlayback();}));
async function togglePlayback(){const player=$('player');if(!player.paused){player.pause();return;}if(!cue(activeId))throw Error('请先选择字幕');if(!player.getAttribute('src')){await playActive();return;}if(playbackRange&&originalNow()>=playbackRange.end)player.currentTime=Math.max(0,(playbackRange.start-clipOffset)/1000);await player.play();}

function mediaUrl(ep,extra=''){return `/api/media?token=${encodeURIComponent(TOKEN)}&project=${p().project_id}&episode=${encodeURIComponent(ep)}${extra}`;}
function loadMedia(ep,mode='audio',seek=0){
 if(!p()?.episodes[ep])return;
 const player=$('player');player.pause();clipOffset=0;mediaMode=mode;playingEpisode=ep;
 playbackRange=null;
 player.src=mediaUrl(ep,mode==='video'?'&video=1':'');player.onloadedmetadata=()=>{player.currentTime=Math.max(0,Math.min(seek/1000,player.duration||Infinity));};
 $('clip-note').textContent='完整媒体时间轴 · 点击波形跳转';
 request('waveform?episode='+ep,null,'GET').then(r=>{if(playingEpisode===ep){peaks=r.peaks;drawWave();}}).catch(()=>{peaks=[];drawWave();});
}
function originalNow(){return Math.round($('player').currentTime*1000)+clipOffset;}
function waveWindow(){const duration=p()?.episodes[playingEpisode]?.duration_ms||0,c=cue(activeId),r=c&&c.episode===playingEpisode?current(c):null;return {start:r?Math.max(0,r.start_ms-3000):0,end:r?Math.min(duration,r.end_ms+3000):duration,duration,row:r};}
function drawWave(){
 const canvas=$('waveform'),ctx=canvas.getContext('2d'),v=waveWindow();ctx.clearRect(0,0,canvas.width,canvas.height);if(v.end<=v.start)return;
 const x=ms=>(ms-v.start)/(v.end-v.start)*canvas.width;
 if(v.row){ctx.fillStyle='#d5e6c8';ctx.fillRect(x(v.row.start_ms),0,x(v.row.end_ms)-x(v.row.start_ms),canvas.height);}
 const from=Math.floor(v.start/v.duration*peaks.length),to=Math.min(peaks.length,Math.ceil(v.end/v.duration*peaks.length));ctx.fillStyle='#7d9b6e';
 for(let i=from;i<to;i++){const h=Math.max(1,peaks[i]*85);ctx.fillRect(x(i/peaks.length*v.duration),(100-h)/2,Math.max(1,canvas.width/(to-from)-.4),h);}
 if(v.row){ctx.fillStyle='#477346';ctx.fillRect(x(v.row.start_ms),0,2,100);ctx.fillRect(x(v.row.end_ms)-1,0,2,100);}
 const ms=originalNow();if(ms>=v.start&&ms<=v.end){ctx.fillStyle='#c18e48';ctx.fillRect(x(ms),0,2,100);}
 $('waveform').setAttribute('aria-valuemin',v.start);$('waveform').setAttribute('aria-valuemax',v.end);$('waveform').setAttribute('aria-valuenow',ms);
 $('clip-note').textContent=`本条前后各3秒 · ${time(v.start)} → ${time(v.end)} · 绿色区域为字幕范围${clipOffset?' · 原片时间 = 播放时间 + '+time(clipOffset):''}`;
}
$('player').addEventListener('timeupdate',()=>{
 const ms=originalNow();$('original-clock').textContent='原片 '+time(ms);
 const rows=p()?.cues.filter(c=>c.episode===playingEpisode&&!current(c).deleted&&current(c).start_ms<=ms&&current(c).end_ms>=ms)||[];
 $('subtitle-overlay').textContent=rows.map(c=>current(c).text).join('\n');drawWave();
 enforcePlaybackRange();
});
function enforcePlaybackRange(){
 const player=$('player');if(player.paused||!playbackRange||playbackRange.episode!==playingEpisode)return;
 const ms=originalNow();if(ms>=playbackRange.end){
  if(loop){player.currentTime=Math.max(0,(playbackRange.start-clipOffset)/1000);}
  else{player.pause();player.currentTime=Math.max(0,(playbackRange.end-clipOffset)/1000);}
 }
}
$('player').addEventListener('play',()=>{clearInterval(rangeTimer);rangeTimer=setInterval(enforcePlaybackRange,15);});
$('player').addEventListener('pause',()=>{clearInterval(rangeTimer);rangeTimer=null;});
$('player').addEventListener('error',()=>{if($('player').getAttribute('src'))$('clip-note').textContent='媒体无法播放：可使用完整缓存音频，或在素材页重新定位。';});
$('player').addEventListener('ended',()=>{if(loop){const c=cue(activeId);$('player').currentTime=Math.max(0,((c?current(c).start_ms:clipOffset)-clipOffset)/1000);$('player').play().catch(()=>{});}});
$('waveform').addEventListener('click',e=>{const v=waveWindow();if(!v.duration)return;const rect=e.target.getBoundingClientRect(),target=v.start+(e.clientX-rect.left)/rect.width*(v.end-v.start),player=$('player');player.pause();if(clipOffset||mediaMode==='clip')loadMedia(playingEpisode,'audio',target);else player.currentTime=target/1000;const c=cue(activeId),r=c&&current(c);playbackRange=r&&c.episode===playingEpisode&&target>=r.start_ms&&target<r.end_ms?{episode:c.episode,start:r.start_ms,end:r.end_ms}:{episode:playingEpisode,start:v.start,end:v.end};$('waveform').focus();drawWave();});
async function playIssue(id){
 const i=p().issues.find(x=>x.id===id);if(!i?.clip)throw Error('此疑点尚无片段，请先运行本地处理。');
 const player=$('player');player.pause();clipOffset=i.clip_start_ms;playingEpisode=i.episode;mediaMode='clip';loop=false;$('loop-toggle').textContent='循环关';$('loop-toggle').setAttribute('aria-pressed','false');
 playbackRange={episode:i.episode,start:i.start_ms,end:i.end_ms};
 player.src=mediaUrl(i.episode,'&issue='+id);player.onloadedmetadata=()=>{player.currentTime=0;player.play().catch(()=>{});};
 $('clip-note').textContent=`疑点截段 · 原片时间 = 播放时间 + ${time(clipOffset)}`;
}
async function playActive(){const c=cue(activeId);if(!c)throw Error('请先选择字幕');const r=current(c);if(playingEpisode!==c.episode||clipOffset||mediaMode==='clip')loadMedia(c.episode,'audio',r.start_ms);else $('player').currentTime=r.start_ms/1000;playbackRange={episode:c.episode,start:r.start_ms,end:r.end_ms};await $('player').play();}
function nextAfterCompletion(before,ids){const done=new Set(ids),pos=Math.max(...before.map((id,i)=>done.has(id)?i:-1)),allowed=new Set(filtered().filter(c=>!current(c).deleted).map(c=>c.id)),next=before.slice(pos+1).find(id=>allowed.has(id)&&!done.has(id));activeId=next||'';selected=new Set(next?[next]:[]);anchorId=next||'';editorId='';playbackRange=null;renderReview(true);if(next)document.querySelector(`.cue-row[data-id="${next}"]`)?.scrollIntoView({block:'nearest'});return next;}
async function deleteSelected(ids){if(!ids.length)throw Error('请先选中字幕。');await flush();const before=filtered().map(c=>c.id);const d=await showDialog(`删除选中的 ${ids.length} 条整条字幕？`,`<p>删除只影响字幕，不剪媒体、不移动后续时间。可撤销，完成后自动选中下一条。</p><pre>${ids.map(id=>{const c=cue(id);return `${c.episode} / ${c.source_number||'新增'}  ${current(c).text}`;}).map(esc).join('\n')}</pre>`,'确认删除');if(!d)return;$('player').pause();await mutate('edit',{ids,action:'delete'});const next=nextAfterCompletion(before,ids);toast(`已删除 ${ids.length} 条，可撤销；${next?'已选下一条':'后面没有待处理字幕'}`);}
async function addCue(issue=null){
 const ep=issue?.episode||cue(activeId)?.episode||episode||Object.keys(p().episodes)[0];if(!ep)throw Error('请先导入一集字幕');
 const d=await showDialog('补一条漏掉的字幕',`<p>新字幕先作为草稿，确认通过后才进入导出。</p><label>集数<input name="episode" value="${esc(ep)}"></label><label>英文字幕<textarea name="text" rows="3">${esc(issue?.evidence_text||'')}</textarea></label><div class="time-inputs"><label>入点<input name="start" value="${time(issue?.start_ms??originalNow())}"></label><label>出点<input name="end" value="${time(issue?.end_ms??originalNow()+2000)}"></label></div>`,'保存为草稿');
 if(!d)return;const id=await mutate('add-cue',{episode:d.episode,row:{text:d.text,start_ms:parseTime(d.start),end_ms:parseTime(d.end)}});activeId=id;episode=d.episode;editorId='';renderReview(true);
}

async function scan(){const r=await request('scan',{media_dir:$('media-dir').value,srt_dir:$('srt-dir').value});const box=$('pair-results');box.hidden=false;box.innerHTML=`<div class="section-title"><h3>配对结果 · ${r.pairs.length} 集</h3><button id="import-pairs" class="primary">导入已选择配对</button></div><p class="subtle">多个候选不会自动选择。缺音视频可先导入字幕，稍后逐集关联。导入错误单独报告。</p>`+r.pairs.map(pair=>`<div class="pair-row" data-pair-episode="${pair.episode}"><span>第 ${pair.episode} 集</span><select data-pair-media><option value="">未关联媒体</option>${pair.media.map(path=>`<option value="${esc(path)}" ${pair.media.length===1?'selected':''}>${esc(path)}</option>`).join('')}</select><select data-pair-srt><option value="">未选择 SRT</option>${pair.srt.map(path=>`<option value="${esc(path)}" ${pair.srt.length===1?'selected':''}>${esc(path)}</option>`).join('')}</select><small>${esc(pair.warnings.join('；')||'可导入')}</small></div>`).join('')+(r.unassigned.length?`<p class="dialog-error">集数不明确，请逐集指定：${r.unassigned.map(x=>esc(x.path)).join('；')}</p>`:'');
 $('import-pairs').onclick=handle(async()=>{const pairs=[...box.querySelectorAll('[data-pair-episode]')].map(row=>({episode:row.dataset.pairEpisode,media:row.querySelector('[data-pair-media]').value,srt:row.querySelector('[data-pair-srt]').value})).filter(x=>x.srt);if(!pairs.length)throw Error('没有选中的有效SRT配对');const result=await mutate('pairs',{pairs});box.innerHTML=`<h3>导入结果</h3><pre>${esc(result.map(x=>`${x.episode}：${x.error||'已导入 '+x.count+' 条'}`).join('\n'))}</pre>`;toast('导入完成，逐集结果已显示');});
}
async function manualPair(){const d=await showDialog('逐集指定素材',`<label>集数<input name="episode" type="number" min="0" placeholder="例如 01"></label>${pathField('single-srt','SRT 字幕','srt')}${pathField('single-media','音视频（可稍后关联）','media')}<label>素材版本说明<input name="version" placeholder="如：有配乐 V1"></label>`,'导入此集');if(!d)return;const r=await mutate('pairs',{pairs:[{episode:d.episode,srt:d['single-srt'],media:d['single-media'],video_version:d.version}]});if(r[0]?.error)throw Error(r[0].error);toast('此集已导入');}
async function processEpisodes(episodes){if(!episodes.length)throw Error('请先导入素材');await mutate('process',{episodes,model:$('model-select').value});toast('本地任务已开始；可离开页面，程序继续处理');}
async function exportPackage(kind,full=false){
 await flush();let include_video=false;
 if(kind==='backup'){const d=await showDialog('备份 / 转交项目','<p>保存完整进度、原字幕、用户资料和已有音频证据。模型不打包。</p><label><input type="checkbox" name="video" style="width:auto"> 附带原始视频 / 音频（文件可能很大）</label>','选择输出文件夹');if(!d)return;include_video=d.video;}
 const folder=await picker();if(!folder)return;
 toast('正在生成导出包，请稍候…');const scope=$('export-scope').value;const result=await mutate('export',{folder,kind,full,include_video,...((kind==='current'||kind==='delivery')&&scope!=='all'?{episodes:[scope]}:{})});
 $('export-result').hidden=false;$('export-result').innerHTML=`<h3>导出已保存</h3><p>文件夹</p><div class="path">${esc(result.directory)}</div><p>压缩包</p><div class="path">${esc(result.zip)}</div>${result.scope?`<p>${esc(result.scope)}</p>`:''}${result.missing?.length?`<p class="dialog-error">${esc(result.missing.join('\n'))}</p>`:''}`;toast('导出完成');
}
async function cleanCache(){await flush();releasePlayer();const plan=await mutate('cleanup-plan');const d=await showDialog('清理当前项目的可再生成缓存',`<p>${esc(plan.project)}<br>${esc(plan.root)}</p><p>预计释放 ${(plan.bytes/1048576).toFixed(2)} MB · ${plan.files.length} 个文件</p><pre>${esc(plan.files.map(x=>x.category+' · '+x.path+' · '+x.bytes+' B').join('\n')||'没有可清理文件')}</pre><p>${esc(plan.warning)}</p>`,'确认清理所列文件');if(!d)return;const r=await mutate('cleanup',{token:plan.token});toast(`清理完成：${r.filter(x=>x.deleted).length} 项成功，${r.filter(x=>x.error).length} 项未删除`);if(r.some(x=>x.error))await showDialog('未删除的文件',`<pre>${esc(r.filter(x=>x.error).map(x=>x.path+': '+x.error).join('\n'))}</pre>`,'知道了');}

on('new-project',()=>projectDialog(true));on('home-create',()=>projectDialog(true));on('open-project',()=>projectDialog(false));on('home-open',()=>projectDialog(false));
on('save-close',async()=>{await mutationQueue.catch(()=>{});await flush();releasePlayer();await request('close');resetProjectUI();await refresh();toast('项目已保存并关闭，待审状态保留');});
on('manual-save',async()=>{await mutate('save');toast('项目进度已保存');});on('clear-cache',cleanCache);on('scan',scan);on('manual-pair',manualPair);
on('choose-model',async()=>{const folder=await picker();if(folder){modelOverride=folder;renderMaterials();}});on('process-all',()=>processEpisodes(Object.keys(p().episodes).filter(ep=>!['done','skipped'].includes(p().jobs[ep]?.status))));on('pause-jobs',async()=>{await request('pause');await refresh();toast('任务已暂停，已完成分段保留');});
on('model-select',()=>{const value=$('model-select').value;modelOverride=state.models.includes(value)?'':value;$('model-path').value=value;},'change');
on('model-path',()=>{modelOverride=$('model-path').value.trim();renderMaterials();},'change');
on('acceleration-reset',async()=>{await mutate('acceleration-reset');toast('已清除本机测速结果，下次需要识别时会重新检测；已有字幕和识别稿保持原样');});
on('legacy-import',async()=>{const d=await showDialog('只读导入旧审核项目',`<p>仅可导入到空项目。会核对原基线指纹、读取旧字幕与识别证据，将进度映射到新稳定ID；旧目录不会修改。</p>${pathField('legacy-folder','旧项目目录')}${pathField('legacy-progress','旧审核进度 JSON','json')}`,'校验并导入');if(!d)return;const result=await mutate('legacy',{folder:d['legacy-folder'],progress:d['legacy-progress']});toast(`已导入 ${result.episodes} 集 / ${result.cues} 条，${result.pending} 条待审`);});
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',handle(async()=>{await flush();tab=b.dataset.tab;render();})));
for(const id of ['filter-status','filter-kind','search'])on(id,async()=>{await flush();selected.clear();renderReview();},id==='search'?'input':'change');
on('all-episodes',async()=>{await flush();episode='';selected.clear();renderReview();});on('select-pending',()=>{selected=new Set(visibleRows.filter(c=>['human','draft'].includes(c.category)).map(c=>c.id));updateSelection();});on('select-list',()=>{selected=new Set(visibleRows.map(c=>c.id));updateSelection();});on('clear-selection',()=>{selected.clear();updateSelection();});
on('batch-approve',async()=>{if(!selected.size)throw Error('请先选择字幕');await mutate('edit',{ids:[...selected],action:'approve'});editorId='';renderReview(true);toast('所选字幕已通过');});on('batch-delete',()=>deleteSelected([...selected]));on('undo',async()=>{await mutate('undo');editorId='';renderReview(true);toast('已撤销上一项人工操作');});
on('batch-certain',async()=>{if(!selected.size)throw Error('请先选择字幕');const result=await mutate('apply-certain',{ids:[...selected]});editorId='';renderReview(true);await showDialog('批量采用结果',`<p>${result.applied} 条已采用。新格式确定文字修正记为AI修正，不冒充人工通过；旧格式候选仍保留草稿。</p><pre>${esc(result.skipped.map(x=>(cue(x.id)?.episode||'')+' / '+(cue(x.id)?.source_number||x.id)+': '+x.reason).join('\n')||'没有跳过条目')}</pre>`,'知道了');});
for(const id of ['edit-text','edit-start','edit-end','edit-note'])$(id).addEventListener('input',queueDraft);
on('approve-cue',async()=>{if(!activeId)return;await flush();const before=filtered().map(c=>c.id),pos=before.indexOf(activeId),after=before.slice(pos+1);$('player').pause();await mutate('edit',{ids:[activeId],action:'approve'});const allowed=new Set(filtered().map(c=>c.id)),next=after.find(id=>allowed.has(id));activeId=next||'';selected=new Set(next?[next]:[]);anchorId=next||'';editorId='';playbackRange=null;renderReview(true);if(next)document.querySelector(`.cue-row[data-id="${next}"]`)?.scrollIntoView({block:'nearest'});toast(next?'已通过，已选中下一条':'已通过，当前列表后面没有待处理条目');});on('pending-cue',async()=>{await mutate('edit',{ids:[activeId],action:'pending'});editorId='';renderReview(true);});on('delete-cue',()=>deleteSelected([activeId]));on('restore-cue',async()=>{await mutate('edit',{ids:[activeId],action:'restore'});editorId='';renderReview(true);});on('add-cue',()=>addCue());
on('set-start',()=>{$('edit-start').value=time(originalNow());queueDraft();});on('set-end',()=>{$('edit-end').value=time(originalNow());queueDraft();});
document.querySelectorAll('[data-nudge]').forEach(b=>b.addEventListener('click',handle(async()=>{const [edge,delta]=b.dataset.nudge.split(':');const input=$('edit-'+edge);input.value=time(parseTime(input.value)+Number(delta));queueDraft();})));
on('play-audio',()=>{const c=cue(activeId);if(c)loadMedia(c.episode,'audio',current(c).start_ms);});on('play-video',()=>{const c=cue(activeId);if(c)loadMedia(c.episode,'video',current(c).start_ms);});on('play-selection',playActive);on('loop-toggle',()=>{loop=!loop;$('loop-toggle').textContent=loop?'循环开':'循环关';$('loop-toggle').setAttribute('aria-pressed',String(loop));});
on('export-ai-light',()=>exportPackage('ai'));on('export-ai-full',()=>exportPackage('ai',true));on('export-current',()=>exportPackage('current'));on('export-delivery',()=>exportPackage('delivery'));on('export-backup',()=>exportPackage('backup'));on('copy-prompt',async()=>{await navigator.clipboard.writeText($('handoff-prompt').value);toast('交接提示已复制');});
on('import-opinions',()=>window.localFiles.chooseOpinions());
on('finish-close',async()=>{await mutationQueue.catch(()=>{});await flush();const d=await showDialog('完成并关闭项目','<p>要求待审事项已处理，并已导出当前修订的交付版。所有进度仍会保留；也可随时重新打开。</p>','完成并关闭');if(!d)return;releasePlayer();await request('close',{finished:true});resetProjectUI();await refresh();toast('项目已完成并保存');});
document.addEventListener('click',handle(async e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.recent!==undefined){await flush();releasePlayer();await request('open',{folder:state.recent[+b.dataset.recent].path});resetProjectUI();await refresh();}
 if(b.dataset.reference)await window.localFiles.chooseReference(b.dataset.reference);
 if(b.dataset.copyError){const ep=b.dataset.copyError,j=p().jobs[ep];await navigator.clipboard.writeText(`第 ${ep} 集\n${j.error?.title||j.message}\n${j.error?.action||''}\n${j.error?.detail||j.message}`);toast('详细错误已复制');}
 if(b.dataset.jobAction){
  if(state.processing)throw Error('请先暂停正在运行的初查，再处理此集');
  const action=b.dataset.jobAction,ep=b.dataset.jobEpisode;
  const descriptions={skip:'保留本集字幕和人工进度，跳过本地识别。AI包会注明缺失证据；已有字幕仍需完成实际文字审查。',exclude:'将本集及相关审查事项从本轮移出，不再参与本轮导出。原文件、缓存和人工进度保留，可在列表下方恢复。',restore:'恢复本集及原有人工进度，重新参与本轮导出。'};
  const d=await showDialog(`第 ${ep} 集：${{skip:'跳过初查',exclude:'从本轮移出',restore:'恢复本集'}[action]}`,`<p>${descriptions[action]}</p>${action==='restore'?'':'<label>处理原因<input name="reason" required placeholder="例如：反复失败，先交AI文字审查"></label>'}`,'确认');
  if(d){await mutate('episode-manage',{episode:ep,action,reason:d.reason||''});toast(action==='restore'?'本集及原有进度已恢复':'已记录处理方式，可以继续其他集数');}
 }
 if(b.dataset.retry)await processEpisodes([b.dataset.retry]);
 if(b.dataset.relink){const path=await picker('media');if(path){await mutate('relink',{episode:b.dataset.relink,path});toast('相同指纹素材已重新关联');}}
 if(b.dataset.episode){await flush();episode=b.dataset.episode;selected.clear();renderReview();}
 if(b.dataset.playIssue)await playIssue(b.dataset.playIssue);
 if(b.dataset.addIssue)await addCue(p().issues.find(i=>i.id===b.dataset.addIssue));
 if(b.dataset.resolveIssue){const d=await showDialog('记录独立疑点处理结果','<label>处理说明<textarea name="note" required rows="3" placeholder="例如：已试听，为背景音乐，不补字幕"></textarea></label>','保存处理结果');if(d)await mutate('resolve',{id:b.dataset.resolveIssue,kind:'issue',resolved:true,note:d.note});}
 if(b.dataset.resolveConflict){const d=await showDialog('记录意见处理结果','<p>此操作只记录已处理。字幕修改请在人工审核页完成并确认。</p><label>处理说明<textarea name="note" required rows="3"></textarea></label>','记录为已处理');if(d)await mutate('resolve',{id:b.dataset.resolveConflict,kind:'conflict',resolved:true,note:d.note});}
 if(b.dataset.useOpinion!==undefined){const o=cue(activeId).opinions[Number(b.dataset.useOpinion)];await mutate('edit',{ids:[activeId],action:'draft',values:o.suggestion});editorId='';renderReview(true);toast('已采用为草稿，仍需确认通过');}
 if(b.dataset.termGroup){const preview=await mutate('term-preview');const group=preview.groups.find(g=>g.id===b.dataset.termGroup);if(!group)throw Error('术语组已变化，请重新查看');const d=await showDialog('整组确认：'+group.canonical,`<p>仅处理本组术语，不通过其他问题。已有通过、草稿、备注和明确待审要求均保留。</p><pre>${esc(group.members.map(m=>`${m.episode} / ${m.number} ${m.text} ${m.protected?'【保护】':''}`).join('\n'))}</pre><label>统一依据<select name="choice"><option value="script">采用剧本正式写法</option><option value="current">保留各条当前写法</option><option value="audio">按音频，使用我填写的统一写法</option></select></label><label>按音频时填写术语<input name="text" value="${esc(group.canonical)}"></label><p>${esc(group.meaning_warning)}</p>`,'确认应用本组');if(d){const result=await mutate('term-apply',{group_id:group.id,choice:d.choice,text:d.text,token:preview.token});editorId='';render();toast(`已处理本组 ${result.applied} 处，保护 ${result.skipped_protected} 处；可撤销`);}}
 if(b.dataset.locateTask){const t=p().review_tasks.find(t=>t.id===b.dataset.locateTask);await flush();tab='review';episode=t.episode||'';$('filter-status').value='all';activeId=t.related_stable_ids?.[0]||'';editorId='';render(true);}
 if(b.dataset.resolveTask){const d=await showDialog('记录具体事项的处理结果','<p>请先核对具体证据；此处不会替代字幕编辑与通过。</p><label>处理说明<textarea name="note" required rows="3"></textarea></label>','保存处理结果');if(d)await mutate('resolve',{kind:'task',id:b.dataset.resolveTask,resolved:true,note:d.note});}
}));
on('merge-titles',async()=>{const plan=await mutate('merge-preview');const d=await showDialog('称谓与姓名合并预览',`<p>合并首条入点和后条出点，不移动后续时间、不剪媒体、不冒充人工通过。原始两个ID保留，可撤销。</p>${plan.candidates.map(c=>`<label><input type="checkbox" name="merge-${c.ids[0]}" ${c.eligible?'checked':'disabled'}> 第${c.episode}集 #${c.numbers.join(' + #')} → ${esc(c.text)} ${esc(c.reasons.join('；'))}</label>`).join('')||'<p>未发现可合并的独立称谓条。</p>'}`,'应用已勾选合并');if(!d)return;const ids=Object.entries(d).filter(([k,v])=>k.startsWith('merge-')&&v).map(([k])=>k.slice(6));if(!ids.length)return;const r=await mutate('merge-apply',{ids,token:plan.token});toast(`已合并 ${r.merged} 处；未改变原媒体和人工确认，可撤销`);});
document.addEventListener('visibilitychange',()=>{if(document.hidden)flush().catch(()=>{});});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';flush().catch(()=>{});}});
setInterval(async()=>{if(pollBusy||dirty||$('modal').open||picking)return;pollBusy=true;try{await saving;const next=await request('state',null,'GET');const changed=next.project?.revision!==p()?.revision||next.processing!==state.processing;if(next.project?.project_id!==p()?.project_id||!setState(next))return;if(changed)render();else if(p()){renderMaterials();$('save-status').textContent=`已保存 · ${p().saved_at.slice(11,19)}`;await offerAIExport();}}catch(error){toast(error.message,true);}finally{pollBusy=false;}},6000);
refresh().catch(e=>toast(e.message,true));
