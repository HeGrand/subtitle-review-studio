'use strict';
(() => {
 function markup(shared=false){
  const label=shared?'共同切点':'拆分点';
  return `<section class="split-audio-picker" aria-label="按音频选择${label}"><canvas id="split-waveform" width="1000" height="150" tabindex="0" role="slider" aria-label="拆分位置，点击或拖动波形选择，左右方向键微调"></canvas><div id="split-wave-ticks" class="split-wave-ticks"></div><div class="split-transport"><button type="button" id="split-play-all">${shared?'试听两条':'试听整句'}</button><button type="button" id="split-play-left">${shared?'试听上一条':'试听前半句'}</button><button type="button" id="split-play-right">${shared?'试听下一条':'试听后半句'}</button><button type="button" id="split-pause">暂停</button><button type="button" id="split-use-playhead">播放位置作${label}</button></div><div class="split-point-row"><strong id="split-point-label"></strong><span id="split-play-clock"></span><div><button type="button" data-split-nudge="-100">−0.1s</button><button type="button" data-split-nudge="-20">−0.02s</button><button type="button" data-split-nudge="20">+0.02s</button><button type="button" data-split-nudge="100">+0.1s</button></div></div><p id="split-audio-status" class="subtle">点击或拖动橙线选择${label}；波形中的灰色部分是前后参考音频。</p><audio id="split-audio" preload="metadata"></audio></section>`;
 }

 function mount(row, ep, initial, shared=false){
  const label=shared?'共同切点':'拆分点';
  const canvas=$('split-waveform'),ctx=canvas.getContext('2d'),audio=$('split-audio'),input=document.querySelector('#modal [name="boundary"]');
  const duration=p().episodes[ep].duration_ms,start=Math.max(0,row.start_ms-400),end=Math.min(duration,row.end_ms+400);
  let point=initial,playEnd=null,timer=null,disposed=false,dragging=false,wave=[],resolution=10,desiredSeek=initial,timedWords=[],manualText=false;
  const left=document.querySelector('#modal [name="left"]'),right=document.querySelector('#modal [name="right"]');
  const tokenize=value=>Array.from(value.matchAll(/[A-Za-z0-9]+(?:['‘’][A-Za-z0-9]+)*|[\u3400-\u9fff]/g),m=>({text:m[0].toLowerCase().replace(/[‘’]/g,"'"),index:m.index}));
  const textTokens=tokenize(row.text);
  left?.addEventListener('input',()=>manualText=true);right?.addEventListener('input',()=>manualText=true);
  function suggestText(){
   if(shared||manualText||textTokens.length<2||timedWords.length!==textTokens.length||timedWords.some((w,i)=>w.text!==textTokens[i].text))return;
   const count=Math.max(1,Math.min(textTokens.length-1,timedWords.filter(w=>(w.start+w.end)*500<point).length));
   const splitAt=textTokens[count].index;
   left.value=row.text.slice(0,splitAt).trim();right.value=row.text.slice(splitAt).trim();
  }
  const x=ms=>(ms-start)/(end-start)*canvas.width;
  function draw(){
   if(disposed)return;
   ctx.clearRect(0,0,canvas.width,canvas.height);
   ctx.fillStyle='#eef0e9';ctx.fillRect(0,0,canvas.width,canvas.height);
   ctx.fillStyle='#d8e7cf';ctx.fillRect(x(row.start_ms),0,x(point)-x(row.start_ms),canvas.height);
   ctx.fillStyle='#e0e9ed';ctx.fillRect(x(point),0,x(row.end_ms)-x(point),canvas.height);
   ctx.fillStyle='#6f8d67';
   const first=Math.max(0,Math.floor(start/resolution)),last=Math.min(wave.length,Math.ceil(end/resolution));
   for(let i=first;i<last;i++){
    const height=Math.max(1,wave[i]*110);
    ctx.fillRect(x(i*resolution),(canvas.height-height)/2,Math.max(1,canvas.width*resolution/(end-start)-.5),height);
   }
   const playMs=audio.currentTime*1000;
   if(playMs>=start&&playMs<=end){ctx.fillStyle='#385a58';ctx.fillRect(x(playMs),22,2,canvas.height-22);}
   ctx.fillStyle='#b57824';ctx.fillRect(x(point)-1,0,3,canvas.height);
   ctx.beginPath();ctx.moveTo(x(point)-8,0);ctx.lineTo(x(point)+8,0);ctx.lineTo(x(point),12);ctx.fill();
   ctx.font='14px sans-serif';ctx.textAlign='center';ctx.fillText(label,Math.max(30,Math.min(canvas.width-30,x(point))),28);
   canvas.setAttribute('aria-valuemin',row.start_ms+1);canvas.setAttribute('aria-valuemax',row.end_ms-1);
   canvas.setAttribute('aria-valuenow',point);canvas.setAttribute('aria-valuetext',time(point));
   $('split-point-label').textContent=label+' '+time(point);
   $('split-play-clock').textContent='播放 '+time(Math.round(playMs));
  }
  function seek(ms){desiredSeek=ms;try{audio.currentTime=ms/1000;}catch{}draw();}
  function stop(){audio.pause();playEnd=null;clearInterval(timer);timer=null;draw();}
  function choose(ms,seekToPoint=true){
   stop();point=Math.max(row.start_ms+1,Math.min(row.end_ms-1,Math.round(ms)));
   input.value=time(point);input.setCustomValidity('');if(seekToPoint)seek(point);suggestText();draw();
  }
  function enforce(){
   if(disposed||audio.paused||playEnd===null)return;
   if(audio.currentTime*1000>=playEnd){const limit=playEnd;audio.pause();seek(limit);playEnd=null;}
   draw();
  }
  async function play(from,to){
   stop();playEnd=to;seek(from);
   try{await audio.play();}catch{if(!disposed)$('split-audio-status').textContent='音频暂时无法播放；仍可按波形或输入时间选择拆分点。';}
  }
  function pick(event){const rect=canvas.getBoundingClientRect();choose(start+(event.clientX-rect.left)/rect.width*(end-start));}
  const down=event=>{if(event.button!==0)return;event.preventDefault();dragging=true;canvas.setPointerCapture(event.pointerId);pick(event);canvas.focus();};
  const move=event=>{if(dragging)pick(event);};
  const up=event=>{dragging=false;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);};
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
  canvas.addEventListener('keydown',event=>{
   if(['ArrowLeft','ArrowRight','Home','End','Space'].includes(event.code))event.preventDefault();else return;
   if(event.code==='Space'){if(audio.paused)play(row.start_ms,row.end_ms);else stop();return;}
   choose(event.code==='Home'?row.start_ms+1:event.code==='End'?row.end_ms-1:point+(event.code==='ArrowRight'?1:-1)*(event.shiftKey?100:20));
  });
  document.querySelectorAll('[data-split-nudge]').forEach(button=>button.addEventListener('click',()=>choose(point+Number(button.dataset.splitNudge))));
  $('split-play-all').onclick=()=>play(row.start_ms,row.end_ms);
  $('split-play-left').onclick=()=>play(row.start_ms,point);
  $('split-play-right').onclick=()=>play(point,row.end_ms);
  $('split-pause').onclick=stop;
  $('split-use-playhead').onclick=()=>choose(audio.currentTime*1000);
  input.addEventListener('input',()=>{
   try{const value=parseTime(input.value);if(value<=row.start_ms||value>=row.end_ms)throw Error();input.setCustomValidity('');stop();point=value;suggestText();draw();}
   catch{input.setCustomValidity('拆分时间须位于当前字幕入点和出点之间。');}
  });
  input.addEventListener('change',()=>{if(input.validity.valid)choose(parseTime(input.value));});
  audio.addEventListener('loadedmetadata',()=>seek(desiredSeek));audio.addEventListener('timeupdate',enforce);
  audio.addEventListener('play',()=>{clearInterval(timer);timer=setInterval(enforce,15);});
  audio.addEventListener('pause',()=>{clearInterval(timer);timer=null;draw();});
  audio.addEventListener('error',()=>{if(!disposed)$('split-audio-status').textContent='音频暂时无法播放；可继续按波形选点。';});
  audio.src=mediaUrl(ep);
  $('split-wave-ticks').innerHTML=Array.from({length:5},(_,i)=>`<span>${time(Math.round(start+(end-start)*i/4))}</span>`).join('');
  choose(initial);
  request('waveform?episode='+encodeURIComponent(ep)+'&include_words='+(shared?'0':'1')+'&start_ms='+row.start_ms+'&end_ms='+row.end_ms,null,'GET').then(result=>{
   if(disposed)return;wave=result.peaks||[];resolution=result.resolution_ms||10;
   timedWords=(result.words||[]).flatMap(w=>{const parts=tokenize(w.word);return parts.map((part,i)=>({text:part.text,start:w.start+(w.end-w.start)*i/parts.length,end:w.start+(w.end-w.start)*(i+1)/parts.length}));});
   suggestText();draw();
  }).catch(()=>{if(!disposed)$('split-audio-status').textContent='波形暂不可用；可试听音频并使用播放位置选择拆分点。';});
  return {dispose(){disposed=true;clearInterval(timer);timer=null;audio.pause();audio.removeAttribute('src');audio.load();}};
 }
 window.SplitWave={markup,mount};
})();
