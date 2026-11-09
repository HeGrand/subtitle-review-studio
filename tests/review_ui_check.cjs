const {chromium}=require('playwright');
const {spawn}=require('node:child_process');const path=require('node:path');const fs=require('node:fs');const assert=require('node:assert/strict');
const helper=require('../web/review-helpers');
assert.match(helper.summary({reason:'无法确认实际对白，音频可读取，但未试听。',evidence:[]}).problem,/未给出具体/);
assert.match(helper.summary({reason:'用词不同',evidence:[]}).script,/不能据此判定无台词/);
const root=fs.mkdtempSync(path.join(process.env.REVIEW_TEST_ROOT||require('node:os').tmpdir(),'subtitle-review-ui-'));
(async()=>{
 const server=spawn('python',['tests/review_ui_fixture.py',root],{cwd:path.resolve('.'),env:{...process.env,PYTHONUTF8:'1',PYTHONPATH:path.resolve('.')+path.delimiter+path.resolve('.runtime/deps')},windowsHide:true});let browser,page;
 try{
 const url=await new Promise((resolve,reject)=>{server.stdout.on('data',b=>{const m=b.toString().match(/http:\/\/127.0.0.1:\d+\//);if(m)resolve(m[0]);});server.stderr.on('data',b=>process.stderr.write(b));server.on('exit',c=>reject(Error('fixture exited '+c)));});
 browser=await chromium.launch({headless:true,...(process.env.REVIEW_BROWSER_PATH?{executablePath:process.env.REVIEW_BROWSER_PATH}:{})});
 page=await browser.newPage({viewport:{width:1540,height:1080}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 if(process.env.REVIEW_TEST_MERGE_PREVIOUS||process.env.REVIEW_TEST_ONSET||process.env.REVIEW_TEST_LAYOUT){
  await page.click('#home-open');
  await page.fill('#project-folder',path.join(root,'generated'));
  await page.click('#modal-ok');
  await page.waitForFunction(()=>p()?.mode==='generation');
  await page.click('[data-tab="review"]');
  await page.selectOption('#filter-status','all');
  if(process.env.REVIEW_TEST_LAYOUT){
   await page.locator('.cue-row').first().click();
   const [first, next]=await page.evaluate(()=>p().cues.map(c=>c.id));
   await page.click('#split-cue');
   await page.fill('#modal [name="left"]','Hel');
   await page.fill('#modal [name="right"]','lo.');
   await page.fill('#modal [name="boundary"]','00:00:00,400');
   await page.click('#modal-ok');
   await page.waitForFunction(id=>cue(id)?.accepted.end_ms===400&&p().cues.some(c=>c.accepted.start_ms===400&&c.accepted.text==='lo.'),first);
   await page.click('#undo');
   await page.waitForFunction(id=>cue(id)?.accepted.end_ms===700&&p().cues.length===2,first);
   await page.locator('.cue-row').first().click();
   await page.click('#shared-boundary');
   await page.fill('#modal [name="boundary"]','00:00:00,850');
   await page.click('#modal-ok');
   await page.waitForFunction(([a,b])=>cue(a)?.draft?.end_ms===850&&cue(b)?.draft?.start_ms===850,[first,next]);
   await page.click('#undo');
   await page.waitForFunction(([a,b])=>!cue(a)?.draft&&!cue(b)?.draft&&cue(a)?.accepted.end_ms===700&&cue(b)?.accepted.start_ms===1000,[first,next]);
   assert.deepEqual(errors,[]);
   console.log('PASS split, shared boundary and undo');
   return;
  }
  if(process.env.REVIEW_TEST_ONSET){
   await page.locator('.cue-row').first().click();
   const id=await page.evaluate(()=>activeId);
   await page.click('#calibrate-onset');
   assert.equal(await page.inputValue('#modal [name="start-0"]'),'00:00:00,240');
   await page.fill('#modal [name="start-0"]','00:00:00,220');
   await page.click('#modal-ok');
   await page.waitForFunction(id=>cue(id)?.accepted.start_ms===220&&cue(id).accepted.end_ms===700,id);
   await page.click('#undo');
   await page.waitForFunction(id=>cue(id)?.accepted.start_ms===100,id);
   assert.deepEqual(errors,[]);
   console.log('PASS onset preview, save and undo');
   return;
  }
  await page.locator('.cue-row').filter({hasText:'Goodbye.'}).click();
 const pair=await page.evaluate(()=>p().cues.filter(c=>c.episode==='01').slice(0,2));
  await page.click('#merge-prev');
 assert.equal(await page.inputValue('#modal textarea[name="text"]'),pair.map(c=>c.accepted.text.replaceAll('\n',' ')).join(' '));
  await page.click('#modal-ok');
  await page.waitForFunction(id=>activeId===id&&cue(id)?.merged_ids?.length===2,pair[0].id);
  await page.click('#undo');
  await page.waitForFunction(id=>!cue(id)?.accepted.deleted,pair[1].id);
  assert.deepEqual(errors,[]);
  console.log('PASS merge previous');
  return;
 }
await page.click('#home-open');await page.fill('#project-folder',path.join(root,'project'));await page.click('#modal-ok');await page.waitForSelector('#workspace:not([hidden])');await page.click('[data-tab="review"]');await page.locator('.cue-row').first().click();
 assert.equal(await page.locator('#review-context').getAttribute('open'),null);
 await page.locator('#review-context > summary').click();
 assert(await page.locator('#opinion-list').isVisible());
 assert.equal(await page.locator('#opinion-list .opinion-card:not(.script-source-card)').count(),2);assert.match(await page.locator('#opinion-list .opinion-card:not(.script-source-card)').first().innerText(),/最新/);assert.match(await page.locator('#opinion-list .opinion-card:not(.script-source-card)').first().innerText(),/Hello there/);assert(!(await page.locator('#opinion-list').innerText()).includes('evidence/'));
 const windowBefore=await page.evaluate(()=>waveWindow());assert.equal(windowBefore.start,0);assert.equal(windowBefore.end,3700);assert(windowBefore.end<windowBefore.duration);
 // Space works without waveform focus, including when a button has focus.
 await page.locator('#filter-status').focus();await page.selectOption('#filter-status','all');
 await page.locator('.cue-row.active').click();
 assert.equal(await page.evaluate(()=>document.activeElement.id),'cue-list');
 await page.keyboard.press('Space');await page.waitForTimeout(100);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.keyboard.press('Space');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);await page.selectOption('#filter-status','pending');
 await page.click('#play-selection');await page.waitForFunction(()=>document.getElementById('player').paused&&document.getElementById('player').currentTime>=.69);const stopped=await page.locator('#player').evaluate(v=>v.currentTime);assert(Math.abs(stopped-.7)<.04);
 await page.click('#loop-toggle');await page.click('#play-selection');await page.waitForTimeout(1400);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.click('#loop-toggle');await page.waitForTimeout(800);assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.locator('#waveform').click({position:{x:25,y:20}});await page.keyboard.press('Space');await page.waitForTimeout(120);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.keyboard.press('Space');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.fill('#edit-note','test');await page.locator('#edit-note').press('End');await page.keyboard.press('Space');assert.equal(await page.inputValue('#edit-note'),'test ');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.locator('.review-details > summary').filter({hasText:'更多操作'}).click();
 await page.click('#delete-cue');await page.click('#modal-ok');await page.waitForFunction(()=>document.getElementById('editor-key').textContent==='01 / 2');
 await page.click('#undo');
 await page.waitForFunction(()=>[...document.querySelectorAll('.cue-row')].some(row=>row.textContent.includes('01 / 1')));
 await page.locator('.cue-row').filter({hasText:'01 / 1'}).click();
 await page.waitForFunction(()=>document.getElementById('editor-key').textContent==='01 / 1');
 await page.locator('#standalone-issues summary').click();await page.click('#outside-all');assert.equal(await page.locator('[data-outside]:checked').count(),2);await page.click('#outside-ignore');await page.waitForFunction(()=>!document.querySelector('[data-outside]'));
 let disk=JSON.parse(fs.readFileSync(path.join(root,'project','project.json'),'utf8'));assert(disk.issues.every(i=>i.excluded_by_user&&!i.resolved));
 await page.click('#undo');await page.waitForSelector('#standalone-issues summary');
 await page.click('#approve-cue');await page.waitForFunction(()=>document.getElementById('editor-key').textContent==='01 / 2');assert.equal(await page.locator('.cue-row.active').count(),1);assert.match(await page.locator('.cue-row.active').innerText(),/Goodbye/);
 const windowAfter=await page.evaluate(()=>waveWindow());assert.equal(windowAfter.end,4600);assert.notEqual(windowAfter.end,windowBefore.end);
 await page.locator('#approve-cue').focus();await page.keyboard.press('Space');await page.waitForTimeout(100);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);assert.equal(await page.locator('#editor-key').innerText(),'01 / 2');await page.keyboard.press('Space');
 await page.click('[data-tab="exchange"]');await page.locator('[data-term-group]').click();await page.waitForSelector('#modal[open]');await page.click('#modal-ok');await page.waitForFunction(()=>document.querySelector('[data-term-group]')?.disabled);
 disk=JSON.parse(fs.readFileSync(path.join(root,'project','project.json'),'utf8'));assert(disk.cues.filter(c=>c.id.startsWith('term-')).every(c=>c.accepted.text==='Starlight King.'&&c.status!=='approved'&&c.term_policy.choice==='script'));
 await page.click('[data-tab="review"]');
 await page.click('#ai-review-episode');await page.click('#modal-ok');
 await page.waitForFunction(()=>p().cues.some(c=>c.opinions.some(o=>o.origin==='api_review')));
 const reviewed=await page.evaluate(()=>p().cues.find(c=>c.opinions.some(o=>o.origin==='api_review')));
 assert.equal(reviewed.accepted.text,'Goodbye.');
 assert.equal(reviewed.opinions.at(-1).suggestion.text,'Goodbye!');
 assert.equal(reviewed.opinions.at(-1).suggestion.start_ms,reviewed.accepted.start_ms);
 assert.equal(reviewed.category,'human');
 // Completion state checks include failures and incomplete episodes; no real recognition is rerun.
 const complete=await page.evaluate(()=>{const project=structuredClone(p());project.jobs={'01':{status:'done'}};project.episodes['01'].transcript='evidence/test.json';return {key:initialCheckCompletionKey(project,false),running:initialCheckCompletionKey(project,true),failed:initialCheckCompletionKey({...project,jobs:{'01':{status:'failed'}}},false),partial:initialCheckCompletionKey({...project,episodes:{...project.episodes,'02':{}}},false)};});assert(complete.key);assert.equal(complete.running,'');assert.equal(complete.failed,'');assert.equal(complete.partial,'');
 await page.evaluate(()=>{p().jobs={'01':{status:'done',message:'验收完成'}};p().episodes['01'].transcript='evidence/test.json';tab='materials';render();});await page.waitForSelector('#modal[open]');assert.match(await page.locator('#modal-content').innerText(),/全部集数本地初查已完成/);assert.equal(await page.locator('#modal-cancel').innerText(),'稍后');assert.equal(await page.locator('#modal-ok').innerText(),'交给 AI 复查全部');await page.click('#modal-cancel');await page.evaluate(()=>render());await page.waitForTimeout(150);assert.equal(await page.locator('#modal').evaluate(v=>v.open),false);
 await page.screenshot({path:path.join(root,'审核意见新版.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(root,'结果.json'),JSON.stringify({passed:true,checks:['字幕出点暂停','循环播放','波形空格播放暂停','输入框空格正常','多轮意见同时展开','意见顶部显示','详细证据折叠'],stopped,errors},null,2));console.log('PASS '+root);
 }catch(error){if(page){console.error(await page.evaluate(()=>({tab,filter:document.getElementById('filter-status')?.value,editor:document.getElementById('editor-key')?.textContent,toast:document.getElementById('toast')?.textContent,rows:document.querySelectorAll('.cue-row').length,project:p()?.mode})));await page.screenshot({path:path.join(root,'失败现场.png'),fullPage:true});}throw error;}finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
