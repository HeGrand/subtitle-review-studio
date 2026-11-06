import sys,math,array,wave,json,copy
from pathlib import Path
from app.core import Project,digest
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
folder=root/'project';folder.mkdir()
srt=root/'01.srt';srt.write_text('1\n00:00:00,100 --> 00:00:00,700\nHello.\n\n2\n00:00:01,000 --> 00:00:01,600\nGoodbye.\n',encoding='utf-8')
p=Project.create(folder,'试听与意见布局验收');p.import_pairs([{'episode':'01','srt':str(srt)}])
audio=folder/'evidence/audio.wav'
with wave.open(str(audio),'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(array.array('h',(int(1000*math.sin(i*.1)) for i in range(160000))).tobytes())
p.data['episodes']['01'].update(audio='evidence/audio.wav',duration_ms=10000)
c=p.data['cues'][0]
for row in p.data['cues']:
 row['text_review']={'performed':True,'result':'human','snapshot_hash':digest(row['accepted']),'method':'测试：需要核对具体对白'}
c['opinions']=[{'confidence':'low','decision':'unable','needs_human':True,'audio_checked':False,'reason':'音频可读取，但没有音频输入工具，尚未试听。','evidence':[{'path':'evidence/01/transcript.json','detail':'[{"text":"Hello there."}]'}]},
 {'confidence':'medium','decision':'suggest','needs_human':True,'audio_checked':False,'reason':'代词不一致，请试听。','review_summary':{'problem':'当前字幕与剧本用词不同','script_text':'Hello there.','asr_text':'Hello there.','recommendation':'试听后确认是否补上 there','uncertainty':'实际对白尚未试听','script_status':'matched'},'evidence':[]}]
p.data['issues']=[{'id':'outside-'+str(i),'episode':'01','cue_id':None,'kind':'可疑漏句','resolved':False,'start_ms':2000+i*1000,'end_ms':2500+i*1000,'evidence_text':'无字幕识别证据'} for i in range(2)]
script=root/'terms.txt';script.write_text('Starlight King.',encoding='utf-8');p.add_reference(script,'script')
for i in range(2):
 term=copy.deepcopy(p.data['cues'][1]);term['id']='term-'+str(i);term['source_number']=i+3
 term['accepted'].update(text='Starlite King.',start_ms=3000+i*2000,end_ms=4000+i*2000)
 term['text_review']={'performed':True,'result':'human','snapshot_hash':digest(term['accepted'])}
 term['opinions']=[{'reason':'术语正式拼写差异','term_review':{'canonical':'Starlight King'},'review_summary':{'script_text':'Starlight King'}}]
 p.data['cues'].append(term)
p.save();p.close()
(root/'generated').mkdir()
generated = Project.create(root/'generated', '生成字幕合并验收')
generated.import_pairs([{'episode': '01', 'srt': str(srt)}])
generated.data['mode'] = 'generation'
import shutil
shutil.copyfile(audio, root/'generated'/'evidence'/'audio.wav')
generated.data['episodes']['01'].update(audio='evidence/audio.wav', duration_ms=10000)
generated.save()
generated.close()
from app import onset
onset.speech_regions = lambda path: [(360, 690), (1000, 1600)]
from app.server import Handler,STATE,ThreadingHTTPServer
import json
from app import ai_config
def fake_ai(messages, **kwargs):
    rows = json.loads(messages[-1]['content'])
    assert all(row['subtitle'] != 'Hello.' for row in rows)
    return json.dumps({'findings': [{'id': row['id'], 'reason': '检查句尾标点', 'text': 'Goodbye!'}
                                    for row in rows if row['subtitle'] == 'Goodbye.']})
ai_config.chat = fake_ai
ai_config.CONFIG = root/'ai-config.json'
ai_config.save_config({'base_url': 'http://localhost:19999/v1', 'model': 'fixture-model'})
STATE.recent_path=root/'recent.json'
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);STATE.port=server.server_address[1]
print('http://127.0.0.1:'+str(STATE.port)+'/',flush=True)
server.serve_forever()
