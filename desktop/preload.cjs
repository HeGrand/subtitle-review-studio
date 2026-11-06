'use strict';
const {contextBridge,ipcRenderer,webUtils}=require('electron');
contextBridge.exposeInMainWorld('subtitleDesktop',{
 filePath:file=>webUtils.getPathForFile(file),
 describePaths:paths=>ipcRenderer.invoke('subtitle:describe-paths',paths),
 pickVideos:()=>ipcRenderer.invoke('subtitle:pick-videos'),
 pickPath:(kind,initial)=>ipcRenderer.invoke('subtitle:pick-path',kind,initial),
 onCloseRequested:callback=>{ipcRenderer.on('subtitle:close-request',()=>callback());},
 finishClose:()=>ipcRenderer.send('subtitle:close-ready'),
 cancelClose:()=>ipcRenderer.send('subtitle:close-cancel')
});
