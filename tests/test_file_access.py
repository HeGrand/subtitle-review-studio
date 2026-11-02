import base64,copy,io,zipfile,unittest
from pathlib import Path
from unittest.mock import patch
from app.file_access import browse,make_directory,import_document
from app.core import UserError
from test_core import WorkflowTests

class FileAccessTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown
    def test_unified_browser_filters_preserve_unicode_paths_and_selection(self):
        folder=self.tmp/'共享 素材';folder.mkdir();(folder/'子目录').mkdir();(folder/'意见.JSON').write_text('{}');(folder/'01.srt').write_text('test')
        result=browse(str(folder),'json');self.assertEqual([row['name'] for row in result['entries']],['子目录','意见.JSON'])
        self.assertEqual(browse(str(folder/'意见.JSON'),'json')['selection'],str(folder/'意见.JSON'))
        self.assertEqual(len(browse(str(folder),'folder')['entries']),1)
        self.assertEqual(len(browse(str(folder),'json','意见')['entries']),1)
    def test_invalid_or_unavailable_paths_are_reported(self):
        for value in ('relative-folder',str(self.tmp/'missing')):
            with self.assertRaises(UserError):browse(value)
        with patch('app.file_access.os.scandir',side_effect=PermissionError('denied')):
            with self.assertRaises(UserError):browse(str(self.tmp))
    def test_folder_creation_never_overwrites_or_traverses(self):
        created=make_directory(str(self.tmp),'新建 项目');self.assertTrue(Path(created).is_dir())
        for value in ('../outside','nested/name','NUL','already.',r'..\outside'):
            with self.assertRaises(UserError):make_directory(str(self.tmp),value)
        with self.assertRaises(UserError):make_directory(str(self.tmp),'新建 项目')
    def test_dropped_docx_retains_original_and_human_progress(self):
        self.p.edit([self.p.data['cues'][0]['id']],'approve');before=copy.deepcopy(self.p.data['cues'])
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Alex: Hello.</w:t></w:r></w:p></w:body></w:document>')
        import_document(self.p,'剧本 新版.docx',base64.b64encode(data.getvalue()).decode(),'script')
        ref=self.p.data['references'][0];self.assertTrue((self.p.root/ref['file']).exists());self.assertIn('Alex: Hello.',(self.p.root/ref['text_file']).read_text(encoding='utf-8'));self.assertEqual(self.p.data['cues'],before)
        self.assertFalse(list((self.p.root/'cache/tmp').glob('document-*')))
    def test_document_upload_rejects_bad_data_without_changes(self):
        before=copy.deepcopy(self.p.data)
        for name,data in [('../evil.docx','AA=='),('evil.exe','AA=='),('script.docx','not-base64')]:
            with self.assertRaises(UserError):import_document(self.p,name,data,'script')
        self.assertEqual(self.p.data,before)

del WorkflowTests
