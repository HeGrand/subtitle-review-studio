(function(root){
 'use strict';
 function summary(o){
  const s=o.review_summary||{};
  const evidence=Array.isArray(o.evidence)?o.evidence:[];
  const transcripts=evidence.filter(e=>/transcript/i.test(e.path||'')).map(e=>e.detail||'').join('\n');
  const matches=[...transcripts.matchAll(/"text"\s*:\s*"((?:\\.|[^"\\])*)"/g)].map(m=>{try{return JSON.parse('"'+m[1]+'"');}catch{return '';}}).filter(Boolean);
  const script=evidence.filter(e=>/references/i.test(e.path||'')).map(e=>e.detail||'').join('\n');
  const sentences=String(o.reason||'').split(/(?<=[。；\n])/).map(x=>x.trim()).filter(Boolean);
  const useful=sentences.filter(x=>!/快照|索引|PCM|WAV|工具|接口|读取|历史|输入能力|未试听|尚未试听|未听取|无法确认实际对白|不代表.*通过/.test(x));
  return {
   problem:s.problem||useful.slice(0,2).join('')||'AI未给出具体错词结论，需要试听确认。',
   script:s.script_text||(s.script_status==='no_dialogue'?'AI判断此处剧本无台词，仍需结合实际音频确认。':script?'剧本候选（尚未精确对齐）：'+script:'未定位到本条对应的剧本台词；不能据此判定无台词。'),
   asr:s.asr_text||[...new Set(matches)].join(' / ')||'该意见未提供可直接展示的识别原句，请查看详细证据。',
   advice:s.recommendation||(o.suggestion?(o.suggestion.deleted?'AI建议删除本条，需人工确认。':'建议改为：'+o.suggestion.text):o.decision==='keep'?'保留当前字幕。':'先保留当前字幕，试听后决定。'),
   uncertainty:s.uncertainty||(['clear','correction'].includes(o.text_review?.result)?'':o.audio_checked===false?'AI未试听音频，实际对白和时间边界尚未确认。':o.needs_human?'仍需人工判断。':'')
  };
 }
 const api={summary};root.ReviewHelpers=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
