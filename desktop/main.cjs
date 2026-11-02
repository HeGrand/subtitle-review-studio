'use strict';
const {app,BrowserWindow,ipcMain,dialog}=require('electron');
const fs=require('node:fs');const fsp=fs.promises;const path=require('node:path');const http=require('node:http');const {spawn}=require('node:child_process');
const {normalizeAbsolute}=require('./paths.cjs');
const root=fs.realpathSync(process.env.SUBTITLE_APP_ROOT||path.join(__dirname,'..'));
const userData=path.join(root,'.runtime','desktop-user-data');fs.mkdirSync(userData,{recursive:true});app.setPath('userData',userData);
let window,backend,origin='',approvedExit=false,closePending=false;
function get(url,headers={}){return new Promise((resolve,reject)=>{
 const request=http.get(url,{headers,timeout:2000},response=>{let body='';response.setEncoding('utf8');response.on('data',chunk=>body+=chunk);response.on('end',()=>response.statusCode===200?resolve(body):reject(Error('HTTP '+response.statusCode)));});
 request.on('timeout',()=>request.destroy(Error('连接本机服务超时')));request.on('error',reject);
});}
async function verifyServer(url){
 if(!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(url))throw Error('无效的本机服务地址');
 const page=await get(url);const token=page.match(/name="app-token" content="([^"]+)"/);
 if(!page.includes('LOCAL REVIEW STUDIO')||!token)throw Error('无法核对本机字幕服务');
 const info=JSON.parse(await get(url+'api/capabilities',{'X-App-Token':token[1]}));
 if(!info.file_browser||fs.realpathSync(info.app_root).toLowerCase()!==root.toLowerCase())throw Error('本机服务版本或目录不匹配');
 return url;
}
async function startServer(){
 const record=path.join(root,'.runtime','server.json');
 if(fs.existsSync(record)){
  try{
   const saved=JSON.parse(fs.readFileSync(record,'utf8'));
   if(/^http:\/\/127\.0\.0\.1:\d+\/$/.test(saved.url)){
    let alive=false;try{await get(saved.url);alive=true;}catch{}
    if(alive){try{return await verifyServer(saved.url);}catch{throw Error('旧程序仍在运行。请先保存关闭原项目、关闭旧页面，再运行“重新启动字幕审查.cmd”。');}}
   }
  }catch(error){if(error.message.startsWith('旧程序'))throw error;}
 }
 const embedded=path.join(root,'.runtime/python/python.exe');const python=process.env.SUBTITLE_PYTHON||(fs.existsSync(embedded)?embedded:'python');
 return new Promise((resolve,reject)=>{
  backend=spawn(python,['-m','app.server','--no-browser'],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:[root,path.join(root,'.runtime/deps'),process.env.PYTHONPATH||''].join(path.delimiter)}});
  let errorText='',found=false;const timeout=setTimeout(()=>reject(Error('本机服务启动超时，请检查运行组件')),30000);
  backend.stderr.on('data',chunk=>{errorText=(errorText+chunk.toString('utf8')).slice(-2500);});
  backend.stdout.on('data',chunk=>{const match=chunk.toString('utf8').match(/http:\/\/127\.0\.0\.1:\d+\//);if(match&&!found){found=true;clearTimeout(timeout);verifyServer(match[0]).then(resolve,reject);}});
  backend.once('error',error=>{clearTimeout(timeout);reject(error);});backend.once('exit',()=>{clearTimeout(timeout);if(!found)reject(Error('启动未完成：'+errorText));});
 });
}
function trusted(event){
 if(!window||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame)throw Error('不允许此页面操作本机文件');
 if(new URL(event.senderFrame.url).origin!==origin)throw Error('页面地址不匹配');
}
ipcMain.handle('subtitle:describe-paths',async(event,paths)=>{
 trusted(event);if(!Array.isArray(paths)||!paths.length||paths.length>20)throw Error('一次最多拖入20个文件');
 return Promise.all(paths.map(async value=>{
  const normalized=normalizeAbsolute(value);
  let timer;try{
   const info=await Promise.race([fsp.stat(normalized),new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('读取超时，请检查共享盘是否连接')),10000))]);
   if(!info.isFile()&&!info.isDirectory())throw Error('此项不是文件或文件夹');
   return {path:normalized,name:path.basename(normalized),directory:info.isDirectory()};
  }finally{clearTimeout(timer);}
 }));
});
ipcMain.on('subtitle:close-ready',event=>{trusted(event);if(closePending){approvedExit=true;window.destroy();}});
ipcMain.on('subtitle:close-cancel',event=>{trusted(event);closePending=false;});
if(!app.requestSingleInstanceLock())app.quit();
else{
 app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.focus();}});
 app.whenReady().then(async()=>{
  try{
   const url=await startServer();origin=new URL(url).origin;
   window=new BrowserWindow({title:'字幕工作室',width:1500,height:1000,minWidth:1000,minHeight:650,show:false,autoHideMenuBar:true,webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true}});
   window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
   window.webContents.on('will-navigate',(event,target)=>{if(new URL(target).origin!==origin)event.preventDefault();});
   window.on('close',event=>{if(!approvedExit){event.preventDefault();if(!closePending){closePending=true;window.webContents.send('subtitle:close-request');}}});
   window.once('ready-to-show',()=>{if(process.env.SUBTITLE_TEST_HIDDEN!=='1')window.show();});await window.loadURL(url);
  }catch(error){dialog.showErrorBox('字幕工作室未启动',error.message);approvedExit=true;app.quit();}
 });
 app.on('window-all-closed',()=>app.quit());
 app.on('will-quit',()=>{if(backend&&approvedExit)backend.kill();});
}
