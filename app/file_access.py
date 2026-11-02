"""Local directory browsing and document uploads shared by every UI path picker."""
import base64,os,re,tempfile
from pathlib import Path
from .core import MEDIA_EXT,UserError

FILTERS={'folder':set(),'json':{'.json'},'srt':{'.srt'},'reference':{'.docx','.txt','.md','.pdf'},'media':MEDIA_EXT}
MAX_UPLOAD=12*1024*1024

def browse(path='',kind='folder',query=''):
    if kind not in FILTERS:raise UserError('未知选择类型')
    raw=str(path).strip().strip('"')
    if os.name=='nt' and re.fullmatch('[A-Za-z]:',raw):raw+='\\'
    target=Path(raw).expanduser() if raw else Path.home()
    if not target.is_absolute():raise UserError('请填写完整路径，例如磁盘路径或共享盘路径')
    selection=''
    if target.is_file():
        if kind=='folder' or target.suffix.lower() not in FILTERS[kind]:raise UserError('此处需要选择对应类型的文件或文件夹')
        selection=str(target);target=target.parent
    if not target.is_dir():raise UserError('文件夹不存在或无权限访问，请检查共享盘是否已连接')
    entries=[];search=str(query).casefold()
    try:
        with os.scandir(target) as directory:
            for item in directory:
                try:
                    is_dir=item.is_dir()
                    if search and search not in item.name.casefold():continue
                    if not is_dir and (kind=='folder' or Path(item.name).suffix.lower() not in FILTERS[kind] or not item.is_file()):continue
                    entries.append({'name':item.name,'path':str(target/item.name),'directory':is_dir})
                except OSError:continue
    except OSError as error:raise UserError('无法读取这个文件夹，请检查访问权限或共享盘连接') from error
    entries.sort(key=lambda row:(not row['directory'],row['name'].casefold()))
    roots=list(os.listdrives()) if hasattr(os,'listdrives') else [str(Path.home().anchor)]
    return {'path':str(target),'parent':str(target.parent),'entries':entries[:2000],'truncated':len(entries)>2000,
            'roots':roots,'home':str(Path.home()),'selection':selection}

def make_directory(parent,name):
    name=str(name).strip()
    if not name or name in ('.','..') or name.endswith(('.',' ')) or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or len(name)>120:
        raise UserError('请输入有效的单个文件夹名称')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',name.split('.')[0],re.I):raise UserError('这个文件夹名称由系统保留')
    root=Path(parent)
    if not root.is_absolute() or not root.is_dir():raise UserError('请选择有效的上级文件夹')
    target=root/name
    try:target.mkdir()
    except FileExistsError as error:raise UserError('同名文件或文件夹已存在，不会覆盖') from error
    return str(target)

def import_document(project,name,content,kind):
    if not isinstance(name,str) or re.search(r'[/\\\x00]',name) or name in ('','.','..') or Path(name).suffix.lower() not in FILTERS['reference']:
        raise UserError('仅支持 DOCX、PDF、TXT 和 Markdown 资料文件')
    if not isinstance(content,str) or len(content)>MAX_UPLOAD*4//3+4:raise UserError('资料超过12MB，请通过浏览本机文件读取原件')
    try:data=base64.b64decode(content,validate=True)
    except (ValueError,TypeError) as error:raise UserError('资料文件传输不完整，请重新拖入') from error
    if len(data)>MAX_UPLOAD:raise UserError('资料超过12MB，请通过浏览本机文件读取原件')
    with tempfile.TemporaryDirectory(prefix='document-',dir=project.root/'cache/tmp') as folder:
        path=Path(folder)/name;path.write_bytes(data)
        return project.add_reference(path,kind)
