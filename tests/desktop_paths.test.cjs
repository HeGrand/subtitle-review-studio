const {test}=require('node:test');const assert=require('node:assert/strict');const {normalizeAbsolute}=require('../desktop/paths.cjs');
test('Windows local paths retain Chinese characters and spaces',()=>assert.equal(normalizeAbsolute('F:\\视频 素材\\第01集','win32'),'F:\\视频 素材\\第01集'));
test('UNC paths are preserved as real shared paths',()=>assert.equal(normalizeAbsolute('\\\\studio-server\\共享盘\\视频 文件夹','win32'),'\\\\studio-server\\共享盘\\视频 文件夹'));
test('invalid relative paths never become guessed absolute paths',()=>{for(const path of ['视频文件夹','C:relative','file.json','x\0y'])assert.throws(()=>normalizeAbsolute(path,'win32'));});
