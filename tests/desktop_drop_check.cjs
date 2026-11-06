const {_electron}=require('playwright');const fs=require('node:fs');const path=require('node:path');const os=require('node:os');const {spawnSync}=require('node:child_process');const assert=require('node:assert/strict');
const source=path.resolve('.'),base=fs.mkdtempSync(path.join(process.env.REVIEW_TEST_ROOT||os.tmpdir(),'desktop-drop-')),runtime=path.join(base,'桌面 程序');fs.mkdirSync(runtime);
for(const name of ['app','web'])fs.cpSync(path.join(source,name),path.join(runtime,name),{recursive:true,filter:p=>!p.includes('__pycache__')});
const fixture=`import sys,json,zipfile\nfrom pathlib import Path\nfrom app.core import Project\nfrom app.exchange import export_ai\nr=Path(sys.argv[1]);(r/'project').mkdir();(r/'exports').mkdir();srt=r/'01.srt';srt.write_text('1\\n00:00:00,100 --> 00:00:01,200\\nHello.\\n\\n2\\n00:00:02,000 --> 00:00:03,000\\nWelcome.\\n',encoding='utf-8');p=Project.create(r/'project','桌面路径验收');p.import_pairs([{'episode':'01','srt':str(srt)}]);p.edit([p.data['cues'][0]['id']],'approve');out=Path(export_ai(p,r/'exports')['directory']);d=json.loads((out/'输出示例_不可直接当真实意见.json').read_text(encoding='utf-8'));[o['evidence'].append({'path':'项目与字幕索引.json','detail':'Synthetic test'}) for o in d['opinions']];(r/'AI意见.json').write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8');p.close()\nwith zipfile.ZipFile(r/'剧本 新版.docx','w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Alex: Hello.</w:t></w:r></w:p></w:body></w:document>')`;
const prepared=spawnSync(process.env.REVIEW_PYTHON||'python',['-c',fixture,base],{encoding:'utf8',env:{...process.env,PYTHONUTF8:'1',PYTHONPATH:[source,process.env.PYTHONPATH||''].join(path.delimiter)}});if(prepared.status!==0)throw Error(prepared.stderr);
(async()=>{let electron;
 try{
 electron=await _electron.launch({executablePath:path.join(source,'node_modules/electron/dist/electron.exe'),args:[path.join(source,'desktop')],env:{...process.env,SUBTITLE_APP_ROOT:runtime,SUBTITLE_PYTHON:process.env.REVIEW_PYTHON||'python',SUBTITLE_TEST_HIDDEN:'1',PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'},timeout:60000});
 const page=await electron.firstWindow();const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.waitForSelector('#home:not([hidden])');
 const nativeMenuState=()=>electron.evaluate(({BrowserWindow,Menu})=>({
  installed:Menu.getApplicationMenu()!==null,visible:BrowserWindow.getAllWindows()[0].isMenuBarVisible()
 }));
 await page.keyboard.press('Alt');
 assert.deepEqual(await nativeMenuState(),{installed:false,visible:false});
 assert.equal(await page.evaluate(()=>typeof require),'undefined');assert.equal(await page.evaluate(()=>typeof window.subtitleDesktop.filePath),'function');
 await page.click('#home-open');await page.fill('#project-folder',path.join(base,'project'));await page.click('#modal-ok');await page.waitForSelector('#workspace:not([hidden])');
 const humanBefore=await page.evaluate(()=>({accepted:p().cues[0].accepted,status:p().cues[0].status,note:p().cues[0].note}));
 const cdp=await page.context().newCDPSession(page);
 async function drop(selector,file){await page.locator(selector).scrollIntoViewIfNeeded();const box=await page.locator(selector).boundingBox();const x=box.x+Math.min(25,box.width/2),y=box.y+Math.min(15,box.height/2);const data={items:[],files:[file],dragOperationsMask:1};for(const type of ['dragEnter','dragOver','drop'])await cdp.send('Input.dispatchDragEvent',{type,x,y,data});}
 for(const [id,name] of [['media-dir','视频 素材'],['srt-dir','SRT 字幕']]){const folder=path.join(base,name);fs.mkdirSync(folder);await drop('#'+id,folder);await page.waitForFunction(({id,folder})=>document.getElementById(id).value===folder,{id,folder});}
 let uncTested=false;
 if(process.env.REVIEW_UNC_PREFIX){
  const folder=path.join(base,'共享盘 目录');fs.mkdirSync(folder);const shared=process.env.REVIEW_UNC_PREFIX+folder.slice(2);
  await drop('#media-dir',shared);await page.waitForFunction(value=>document.getElementById('media-dir').value===value,shared);
  await page.click('[data-pick="media-dir"]');await page.waitForFunction(value=>document.getElementById('browser-selected').textContent===value,shared);await page.click('#browser-confirm');uncTested=true;
 }
 await drop('#reference-drop',path.join(base,'剧本 新版.docx'));await page.waitForFunction(()=>document.getElementById('reference-source').textContent.includes('剧本 新版.docx'));assert.match(await page.locator('#reference-source').innerText(),new RegExp(base.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
 await page.click('[data-tab="exchange"]');await page.locator('#export-more > summary').click();await drop('#opinion-drop',path.join(base,'AI意见.json'));await page.waitForFunction(()=>document.getElementById('opinion-source').textContent.includes('AI意见.json'));assert((await page.locator('#opinion-source').innerText()).includes(base));
 assert.deepEqual(await page.evaluate(()=>({accepted:p().cues[0].accepted,status:p().cues[0].status,note:p().cues[0].note})),humanBefore);
 await page.click('[data-tab="review"]');await page.selectOption('#filter-status','all');await page.locator('.cue-row').last().click();await page.fill('#edit-note','关闭前的新备注');
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(base,'桌面拖放.png'),fullPage:true});
 const closed=electron.waitForEvent('close',{timeout:20000});await electron.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].close());await closed;electron=null;
 const saved=JSON.parse(fs.readFileSync(path.join(base,'project/project.json'),'utf8'));assert.equal(saved.cues[0].status,'approved');assert.equal(saved.cues[1].note,'关闭前的新备注');
 fs.writeFileSync(path.join(base,'结果.json'),JSON.stringify({passed:true,uncTested,checks:['真实文件夹拖入完整路径','中文及空格路径','SRT文件夹路径','DOCX真实路径读取','JSON真实路径自动导入','人工内容不变','关闭窗口保存项目','页面未开放Node'],errors},null,2));console.log('PASS '+base+' UNC='+uncTested);
 }finally{if(electron)await electron.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
