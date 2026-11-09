'use strict';
const {app,BrowserWindow,ipcMain,dialog,Menu}=require('electron');
const fs=require('node:fs');const fsp=fs.promises;const path=require('node:path');const http=require('node:http');const {spawn}=require('node:child_process');
const {normalizeAbsolute}=require('./paths.cjs');
const root=fs.realpathSync(process.env.SUBTITLE_APP_ROOT||path.join(__dirname,'..'));
const appId='SubtitleReviewStudio.Desktop';
const studioIcon=path.join(__dirname,'studio.ico');
const userData=path.join(root,'.runtime','desktop-user-data');fs.mkdirSync(userData,{recursive:true});app.setPath('userData',userData);
if(process.platform==='win32')app.setAppUserModelId(appId);
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
    if(alive){try{return await verifyServer(saved.url);}catch{throw Error('旧程序仍在运行。请在旧窗口保存并退出字幕工作室，再双击“字幕工作室.exe”。');}}
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
const mediaFilter={name:'视频和音频',extensions:['mp4','mkv','mov','avi','webm','wav','mp3','m4a','flac','aac']};
ipcMain.handle('subtitle:pick-path',async(event,kind,initial)=>{
 trusted(event);
 const filters=new Map([
  ['folder',[]],['media',[mediaFilter]],['reference',[{name:'剧本和资料',extensions:['docx','txt','md','pdf']}]],
  ['srt',[{name:'SRT 字幕',extensions:['srt']}]],['json',[{name:'JSON 文件',extensions:['json']}]]
 ]);
 if(!filters.has(kind))throw Error('未知文件选择类型');
 let defaultPath;
 if(initial){try{defaultPath=normalizeAbsolute(initial);}catch{}}
 const result=await dialog.showOpenDialog(window,{
  title:kind==='folder'?'选择文件夹':'选择文件',
  properties:[kind==='folder'?'openDirectory':'openFile'],filters:filters.get(kind),defaultPath
 });
 return result.canceled||!result.filePaths.length?'':normalizeAbsolute(result.filePaths[0]);
});
ipcMain.handle('subtitle:pick-videos',async event=>{
 trusted(event);
 const result=await dialog.showOpenDialog(window,{
  title:'批量选择视频或音频',properties:['openFile','multiSelections'],
  filters:[mediaFilter]
 });
 return result.canceled?[]:result.filePaths.map(value=>normalizeAbsolute(value));
});
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
 app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.show();window.focus();}});
 app.whenReady().then(async()=>{
  try{
   const url=await startServer();origin=new URL(url).origin;
   Menu.setApplicationMenu(null);
   window=new BrowserWindow({title:'字幕工作室',width:1500,height:1000,minWidth:1000,minHeight:650,show:false,icon:studioIcon,webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true}});
   const launcher=path.join(root,'字幕工作室.exe');
   if(process.platform==='win32'&&fs.existsSync(launcher)){
    window.setAppDetails({appId,appIconPath:launcher,relaunchCommand:`"${launcher}"`,relaunchDisplayName:'字幕工作室'});
   }
   window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
   window.webContents.on('will-navigate',(event,target)=>{if(new URL(target).origin!==origin)event.preventDefault();});
   window.on('close',event=>{if(!approvedExit){event.preventDefault();if(!closePending){closePending=true;window.webContents.send('subtitle:close-request');}}});
   window.once('ready-to-show',()=>{if(process.env.SUBTITLE_TEST_HIDDEN!=='1')window.show();});await window.loadURL(url);
  }catch(error){dialog.showErrorBox('字幕工作室未启动',error.message);approvedExit=true;app.quit();}
 });
 app.on('window-all-closed',()=>app.quit());
 app.on('will-quit',()=>{if(backend&&approvedExit)backend.kill();});
}
