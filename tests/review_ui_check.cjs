const {chromium}=require('playwright');
const {spawn}=require('node:child_process');const path=require('node:path');const fs=require('node:fs');const assert=require('node:assert/strict');
const helper=require('../web/review-helpers');
assert.match(helper.summary({reason:'无法确认实际对白，音频可读取，但未试听。',evidence:[]}).problem,/未给出具体/);
assert.match(helper.summary({reason:'用词不同',evidence:[]}).script,/不能据此判定无台词/);
const root=fs.mkdtempSync(path.join(process.env.REVIEW_TEST_ROOT||require('node:os').tmpdir(),'subtitle-review-ui-'));
(async()=>{
 const server=spawn('python',['tests/review_ui_fixture.py',root],{cwd:path.resolve('.'),env:{...process.env,PYTHONUTF8:'1',PYTHONPATH:path.resolve('.')+path.delimiter+path.resolve('.runtime/deps')},windowsHide:true});let browser;
 try{
 const url=await new Promise((resolve,reject)=>{server.stdout.on('data',b=>{const m=b.toString().match(/http:\/\/127.0.0.1:\d+\//);if(m)resolve(m[0]);});server.stderr.on('data',b=>process.stderr.write(b));server.on('exit',c=>reject(Error('fixture exited '+c)));});
 browser=await chromium.launch({headless:true,...(process.env.REVIEW_BROWSER_PATH?{executablePath:process.env.REVIEW_BROWSER_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1540,height:1080}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.click('#home-open');await page.fill('#project-folder',path.join(root,'project'));await page.click('#modal-ok');await page.waitForSelector('#workspace:not([hidden])');await page.click('[data-tab="review"]');await page.locator('.cue-row').first().click();
 assert.equal(await page.locator('#opinion-list .opinion-card').count(),2);assert.match(await page.locator('#opinion-list .opinion-card').first().innerText(),/最新/);assert.match(await page.locator('#opinion-list .opinion-card').first().innerText(),/Hello there/);assert(!(await page.locator('#opinion-list').innerText()).includes('evidence/'));
 const box=await page.locator('#opinion-list').boundingBox();assert(box.y<450);
 const windowBefore=await page.evaluate(()=>waveWindow());assert.equal(windowBefore.start,0);assert.equal(windowBefore.end,3700);assert(windowBefore.end<windowBefore.duration);
 // Space works without waveform focus, including when a button has focus.
 await page.locator('#cue-list').focus();await page.keyboard.press('Space');await page.waitForTimeout(100);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.keyboard.press('Space');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.click('#play-selection');await page.waitForFunction(()=>document.getElementById('player').paused&&document.getElementById('player').currentTime>=.69);const stopped=await page.locator('#player').evaluate(v=>v.currentTime);assert(Math.abs(stopped-.7)<.04);
 await page.click('#loop-toggle');await page.click('#play-selection');await page.waitForTimeout(1400);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.click('#loop-toggle');await page.waitForTimeout(800);assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.locator('#waveform').click({position:{x:25,y:20}});await page.keyboard.press('Space');await page.waitForTimeout(120);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);await page.keyboard.press('Space');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.fill('#edit-note','test');await page.locator('#edit-note').press('End');await page.keyboard.press('Space');assert.equal(await page.inputValue('#edit-note'),'test ');assert.equal(await page.locator('#player').evaluate(v=>v.paused),true);
 await page.click('#delete-cue');await page.click('#modal-ok');await page.waitForFunction(()=>document.getElementById('editor-key').textContent==='01 / 2');
 await page.click('#undo');await page.locator('.cue-row').first().click();
 await page.locator('#standalone-issues summary').click();await page.click('#outside-all');assert.equal(await page.locator('[data-outside]:checked').count(),2);await page.click('#outside-ignore');await page.waitForFunction(()=>!document.querySelector('[data-outside]'));
 let disk=JSON.parse(fs.readFileSync(path.join(root,'project','project.json'),'utf8'));assert(disk.issues.every(i=>i.excluded_by_user&&!i.resolved));
 await page.click('#undo');await page.waitForSelector('#standalone-issues summary');
 await page.click('#approve-cue');await page.waitForFunction(()=>document.getElementById('editor-key').textContent==='01 / 2');assert.equal(await page.locator('.cue-row.active').count(),1);assert.match(await page.locator('.cue-row.active').innerText(),/Goodbye/);
 const windowAfter=await page.evaluate(()=>waveWindow());assert.equal(windowAfter.end,4600);assert.notEqual(windowAfter.end,windowBefore.end);
 await page.locator('#approve-cue').focus();await page.keyboard.press('Space');await page.waitForTimeout(100);assert.equal(await page.locator('#player').evaluate(v=>v.paused),false);assert.equal(await page.locator('#editor-key').innerText(),'01 / 2');await page.keyboard.press('Space');
 await page.click('[data-tab="exchange"]');await page.locator('[data-term-group]').click();await page.waitForSelector('#modal[open]');await page.click('#modal-ok');await page.waitForFunction(()=>document.querySelector('[data-term-group]')?.disabled);
 disk=JSON.parse(fs.readFileSync(path.join(root,'project','project.json'),'utf8'));assert(disk.cues.filter(c=>c.id.startsWith('term-')).every(c=>c.accepted.text==='Starlight King.'&&c.status!=='approved'&&c.term_policy.choice==='script'));
 // Completion state checks include failures and incomplete episodes; no real recognition is rerun.
 const complete=await page.evaluate(()=>{const project=structuredClone(p());project.jobs={'01':{status:'done'}};project.episodes['01'].transcript='evidence/test.json';return {key:initialCheckCompletionKey(project,false),running:initialCheckCompletionKey(project,true),failed:initialCheckCompletionKey({...project,jobs:{'01':{status:'failed'}}},false),partial:initialCheckCompletionKey({...project,episodes:{...project.episodes,'02':{}}},false)};});assert(complete.key);assert.equal(complete.running,'');assert.equal(complete.failed,'');assert.equal(complete.partial,'');
 await page.evaluate(()=>{p().jobs={'01':{status:'done',message:'验收完成'}};p().episodes['01'].transcript='evidence/test.json';tab='materials';render();});await page.waitForSelector('#modal[open]');assert.match(await page.locator('#modal-content').innerText(),/全部集数本地初查已完成/);assert.equal(await page.locator('#modal-cancel').innerText(),'稍后');await page.click('#modal-ok');await page.waitForSelector('#tab-exchange:not([hidden])');await page.evaluate(()=>render());await page.waitForTimeout(150);assert.equal(await page.locator('#modal').evaluate(v=>v.open),false);
 await page.screenshot({path:path.join(root,'审核意见新版.png'),fullPage:true});assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(root,'结果.json'),JSON.stringify({passed:true,checks:['字幕出点暂停','循环播放','波形空格播放暂停','输入框空格正常','多轮意见同时展开','意见顶部显示','详细证据折叠'],stopped,errors},null,2));console.log('PASS '+root);
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
