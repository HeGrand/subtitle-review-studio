const {chromium}=require('playwright');
const {spawn}=require('node:child_process');const path=require('node:path');const fs=require('node:fs');const assert=require('node:assert/strict');
const root=path.resolve(process.argv[2]),work=path.resolve(process.argv[3]);fs.mkdirSync(work,{recursive:true});
(async()=>{
 const server=spawn(path.join(root,'.runtime/python/python.exe'),['-B','-m','app.server','--no-browser'],{cwd:root,windowsHide:true,env:{...process.env,PATH:path.join(process.env.SystemRoot||'C:/Windows','System32'),USERPROFILE:path.join(work,'empty-user'),HOME:path.join(work,'empty-user'),PYTHONUTF8:'1',PYTHONNOUSERSITE:'1',PYTHONDONTWRITEBYTECODE:'1',HF_HUB_OFFLINE:'1'}});let browser;
 try{
 const url=await new Promise((resolve,reject)=>{server.stdout.on('data',b=>{const m=b.toString().match(/http:\/\/127.0.0.1:\d+\//);if(m)resolve(m[0]);});server.stderr.on('data',b=>process.stderr.write(b));server.on('exit',c=>reject(Error('portable server exited '+c)));});
 browser=await chromium.launch({headless:true,...(process.env.REVIEW_BROWSER_PATH?{executablePath:process.env.REVIEW_BROWSER_PATH}:{})});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await page.waitForSelector('#home:not([hidden])');await page.waitForFunction(()=>document.getElementById('recent-list').textContent.includes('还没有项目'));
 const folder=path.join(work,'我的字幕项目');fs.mkdirSync(folder);await page.click('#home-create');await page.fill('#new-name','便携版新项目');await page.fill('#project-folder',folder);await page.click('#modal-ok');await page.waitForSelector('#workspace:not([hidden])');
 await page.locator('#processing-settings > summary').click();
 const model=await page.inputValue('#model-select');assert(model.startsWith(root));assert.match(model,/large-v3-turbo/);assert.match(await page.locator('#model-select option:checked').innerText(),/内置多语言模型/);assert(!(await page.locator('#model-select').innerText()).includes('首次自动下载'));
 if(await page.locator('#acceleration-reset').count()){
  assert.match(await page.locator('#acceleration-status').innerText(),/GPU|CPU|自动检测/);
  await page.click('#acceleration-reset');await page.waitForFunction(()=>document.getElementById('acceleration-status').textContent.includes('首次使用同段音频'));
 }
 await page.click('[data-tab="exchange"]');await page.waitForSelector('#tab-exchange:not([hidden])');await page.click('#save-close');await page.waitForSelector('#home:not([hidden])');assert(fs.existsSync(path.join(folder,'project.json')));assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(work,'便携版页面.png'),fullPage:true});fs.writeFileSync(path.join(work,'页面验收.json'),JSON.stringify({passed:true,checks:['最终解压目录启动页面','首页无个人最近项目','页面新建独立项目','默认选中包内模型且无下载提示','AI交接页可打开','保存关闭项目'],errors},null,2));console.log('PASS portable UI');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
