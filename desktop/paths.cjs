'use strict';
const path=require('node:path');
function normalizeAbsolute(value,platform=process.platform){
 const paths=platform==='win32'?path.win32:path.posix;
 if(typeof value!=='string'||value.length>32767||value.includes('\0')||!paths.isAbsolute(value))throw Error('未取得有效的本机完整路径');
 return paths.normalize(value);
}
module.exports={normalizeAbsolute};
