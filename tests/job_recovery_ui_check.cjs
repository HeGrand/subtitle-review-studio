const {chromium}=require('playwright');
const {spawn}=require('node:child_process'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const root=fs.mkdtempSync(path.join(process.env.REVIEW_TEST_ROOT||os.tmpdir(),'job-recovery-ui-'));
(async()=>{
 const server=spawn(process.env.REVIEW_PYTHON||'python',['tests/job_recovery_ui_fixture.py',root],{windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});let browser;
 try{
  const url=await new Promise((resolve,reject)=>{server.stdout.on('data',d=>{const m=d.toString().match(/http:\/\/127.0.0.1:\d+\//);if(m)resolve(m[0]);});server.stderr.on('data',d=>process.stderr.write(d));server.on('exit',c=>reject(Error('fixture '+c)));});
  browser=await chromium.launch({headless:true,...(process.env.REVIEW_BROWSER_PATH?{executablePath:process.env.REVIEW_BROWSER_PATH}:{})});
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url);await page.click('#home-open');await page.fill('#project-folder',path.join(root,'project'));await page.click('#modal-ok');await page.waitForSelector('#workspace:not([hidden])');
  const before=await page.evaluate(()=>structuredClone(p().cues.find(c=>c.episode==='01')));
  await page.locator('.episode-status-row details summary').first().click();assert.match(await page.locator('#episode-status').innerText(),/Legacy error: disk full 123/);
  await page.click('[data-job-action="skip"][data-job-episode="01"]');await page.fill('[name="reason"]','暂缺证据，先做文字审查');await page.click('#modal-ok');
  await page.waitForFunction(()=>p().jobs['01'].status==='skipped');assert.equal(await page.evaluate(()=>p().cues.length),2);
  await page.locator('details[data-error-ep="02"] summary').click();
  await page.click('[data-job-action="exclude"][data-job-episode="02"]');await page.fill('[name="reason"]','暂不交付');await page.click('#modal-ok');await page.waitForFunction(()=>!p().episodes['02']);
  await page.waitForSelector('#modal[open]');assert.match(await page.locator('#modal').innerText(),/部分集数已跳过/);await page.click('#modal-cancel');
  await page.locator('.excluded-episodes summary').click();await page.click('[data-job-action="restore"]');await page.click('#modal-ok');await page.waitForFunction(()=>!!p().episodes['02']);
  assert.deepEqual(await page.evaluate(()=>structuredClone(p().cues.find(c=>c.episode==='01'))),before);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(root,'失败处理.png'),fullPage:true});fs.writeFileSync(path.join(root,'结果.json'),JSON.stringify({passed:true,checks:['旧错误可见','跳过仍保留字幕','移出后不参与本轮','恢复后保留进度','未冒充初查完成'],errors},null,2));console.log('PASS '+root);
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
