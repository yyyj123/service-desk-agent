"""Bounded local extraction. Office files are read as XML, never executed."""
import base64
import io
import json
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

MAX_FILE=1_000_000
MAX_TEXT=100_000
EXTENSIONS={'.txt','.md','.csv','.html','.docx','.xlsx','.pptx','.pdf'}

def parse_document(filename,data):
    suffix=Path(filename).suffix.lower()
    if suffix not in EXTENSIONS: raise ValueError('支持 TXT、MD、CSV、HTML、DOCX、XLSX、PPTX、文本 PDF')
    if not data or len(data)>MAX_FILE: raise ValueError('文件不能为空且不能超过 1 MB')
    try:
        result=subprocess.run([sys.executable,'-m','service.documents',suffix],input=data,capture_output=True,timeout=15,check=False)
    except subprocess.TimeoutExpired:
        raise ValueError('文档解析超时，请拆分文件') from None
    if result.returncode: raise ValueError('无法解析文档，请检查文件格式、加密状态和大小')
    payload=json.loads(result.stdout)
    if 'error' in payload: raise ValueError(payload['error'])
    return payload['text']

class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self,text):
        if not self.skip: self.parts.append(text)

def extract(suffix,data):
    if suffix in ('.txt','.md','.csv','.html'):
        text=data.decode('utf-8-sig')
        if suffix=='.html':
            parser=PlainHTML();parser.feed(text);text='\n'.join(parser.parts)
    elif suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages)>100: raise ValueError('PDF 必须未加密且不超过 100 页')
        text='\n'.join(page.extract_text() or '' for page in reader.pages)
    else:
        from defusedxml.ElementTree import fromstring
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)>2000 or sum(e.file_size for e in entries)>12_000_000:
                raise ValueError('文档解压后过大，请拆分上传')
            names=archive.namelist()
            parts=[]
            if suffix=='.docx':
                root=fromstring(archive.read('word/document.xml'))
                for paragraph in root.iter():
                    if paragraph.tag.endswith('}p'):
                        parts.append(''.join(n.text or '' for n in paragraph.iter() if n.tag.endswith('}t')))
            elif suffix=='.pptx':
                for name in sorted(n for n in names if n.startswith('ppt/slides/slide') and n.endswith('.xml')):
                    root=fromstring(archive.read(name))
                    parts.append(' '.join(n.text or '' for n in root.iter() if n.tag.endswith('}t')))
            else:
                shared=[]
                if 'xl/sharedStrings.xml' in names:
                    root=fromstring(archive.read('xl/sharedStrings.xml'))
                    shared=[''.join(t.text or '' for t in n.iter() if t.tag.endswith('}t')) for n in root]
                for name in sorted(n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')):
                    root=fromstring(archive.read(name))
                    for row in root.iter():
                        if not row.tag.endswith('}row'): continue
                        values=[]
                        for cell in row:
                            value=''.join(n.text or '' for n in cell.iter() if n.tag.endswith(('}v','}t')))
                            if cell.get('t')=='s' and value.isdigit(): value=shared[int(value)]
                            values.append(value)
                        parts.append(' | '.join(values))
            text='\n'.join(parts)
    text=text.strip()
    if not text: raise ValueError('未提取到文字；扫描 PDF 请先进行 OCR')
    if len(text)>MAX_TEXT: raise ValueError('提取文字超过 10 万字符，请拆分文档')
    return text

if __name__=='__main__':
    try:
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(10,10))
        data=sys.stdin.buffer.read(MAX_FILE+1)
        if len(data)>MAX_FILE: raise ValueError('文件过大')
        result={'text':extract(sys.argv[1],data)}
    except Exception as exc:
        result={'error':str(exc) if isinstance(exc,ValueError) else '解析失败，请检查格式或文件是否损坏'}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
