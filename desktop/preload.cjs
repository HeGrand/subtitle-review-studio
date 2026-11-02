'use strict';
const {contextBridge,ipcRenderer,webUtils}=require('electron');
contextBridge.exposeInMainWorld('subtitleDesktop',{
 filePath:file=>webUtils.getPathForFile(file),
 describePaths:paths=>ipcRenderer.invoke('subtitle:describe-paths',paths),
 onCloseRequested:callback=>{ipcRenderer.on('subtitle:close-request',()=>callback());},
 finishClose:()=>ipcRenderer.send('subtitle:close-ready'),
 cancelClose:()=>ipcRenderer.send('subtitle:close-cancel')
});
