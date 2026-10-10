#!/usr/bin/env python3
"""Existing-engine conversions for Lumen. No skill files are runtime dependencies.

PPT compiler ownership transferred from editable-html-deck on 2026-10-07;
source hashes are in bundled assets/PROVENANCE.json. Directory graphs delegate
to Folio, the existing owner of graph scanning, restrictions and layout.
"""
from __future__ import annotations
import argparse,base64,hashlib,html,io,json,mimetypes,posixpath as pp,re
from pathlib import Path
from zipfile import ZipFile, BadZipFile, LargeZipFile
from xml.etree import ElementTree as E

A='http://schemas.openxmlformats.org/drawingml/2006/main'
P='http://schemas.openxmlformats.org/presentationml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS={'a':A,'p':P}
ASSETS=Path(__file__).resolve().parent/'assets'
from .rendering import enhance_html
from .types import LumenError

def esc(text):return html.escape(str(text),quote=True)
def rels(z,name):
    path=pp.join(pp.dirname(name),'_rels',pp.basename(name)+'.rels')
    return {r.get('Id'):{'target':pp.normpath(pp.join(pp.dirname(name),r.get('Target'))),'type':r.get('Type','').split('/')[-1],'external':r.get('TargetMode')=='External'} for r in E.fromstring(z.read(path))} if path in z.namelist() else {}

def slide_order(z):
    mapping=rels(z,'ppt/presentation.xml')
    return [mapping[n.get('{'+R+'}id')]['target'] for n in E.fromstring(z.read('ppt/presentation.xml')).findall('.//p:sldId',NS)]

def para(p):
    # Keep actual paragraph/soft-break boundaries, join split runs without added spaces.
    txt=''.join((n.text or '') if n.tag=='{'+A+'}t' else '\n' for n in p.iter() if n.tag in {'{'+A+'}t','{'+A+'}br'})
    return re.sub(r'[ \t\u00a0]+',' ',txt).strip()

def text_blocks(root):
    blocks=[]
    for shape in root.findall('.//p:sp',NS):
        tx=shape.find('p:txBody',NS)
        if tx is None:continue
        lines=[para(p) for p in tx.findall('a:p',NS)];lines=[s for s in lines if s]
        if lines:blocks.append(lines)
    return blocks

def to_data(data,name):
    from PIL import Image
    suffix=Path(name).suffix.lower()
    if suffix in {'.svg','.gif'}:
        mime={'.svg':'image/svg+xml','.gif':'image/gif'}[suffix];return f'data:{mime};base64,'+base64.b64encode(data).decode()
    image=Image.open(io.BytesIO(data))
    # Preserve tall screenshots: cap width independently instead of shrinking into 16:9.
    if image.mode=='P':image=image.convert('RGBA' if 'transparency' in image.info else 'RGB')
    image.thumbnail((1600,6500))
    if image.mode not in {'RGB','RGBA'}:image=image.convert('RGB')
    buf=io.BytesIO();image.save(buf,format='WEBP',quality=89,method=4)
    return 'data:image/webp;base64,'+base64.b64encode(buf.getvalue()).decode()

def table_html(tbl):
    rows=[]
    for j,row in enumerate(tbl.findall('a:tr',NS)):
        cells=[];tag='th' if j==0 else 'td'
        for cell in row.findall('a:tc',NS):
            text='\n'.join(filter(None,(para(p) for p in cell.findall('.//a:p',NS))))
            cells.append(f'<{tag} class="edit" contenteditable="true">{esc(text)}</{tag}>')
        rows.append('<tr>'+''.join(cells)+'</tr>')
    return '<div class="table-scroll"><table>'+''.join(rows)+'</table></div>'

def extract_page(z,name,config,cache,video_cache,video_assets):
    root=E.fromstring(z.read(name));mapping=rels(z,name);pictures=[];media=[];omitted=[]
    excludes=set(config.get('exclude_images',[]));seen=set()
    for pic in root.findall('.//p:pic',NS):
        blip=pic.find('.//a:blip',NS)
        if blip is None:continue
        target=mapping.get(blip.get('{'+R+'}embed'),{}).get('target')
        if not target:raise ValueError(f'Unsupported linked picture: {name}')
        key=pp.basename(target)
        if key in excludes:omitted.append(key);continue
        if target not in cache:cache[target]=to_data(z.read(target),target)
        movie=pic.find('.//a:videoFile',NS)
        movie_target=mapping.get(movie.get('{'+R+'}link'),{}).get('target','') if movie is not None else ''
        if target not in seen:pictures.append({'key':key,'data':cache[target],'video':pp.basename(movie_target)});seen.add(target)
    for relation in mapping.values():
        if relation['type'] not in {'video','audio'}:continue
        if relation['external']:raise ValueError(f'External media needs review: {relation}')
        target=relation['target'];key=pp.basename(target)
        if key in [m['key'] for m in media]:continue
        original=z.read(target)
        cache_key=hashlib.sha256(original).hexdigest()[:20]+'.mp4'
        source=video_cache/cache_key if video_cache else None
        data=source.read_bytes() if source and source.exists() else original
        mime='video/mp4' if source and source.exists() else (mimetypes.guess_type(key)[0] or 'video/mp4')
        video_assets[key]=f'data:{mime};base64,'+base64.b64encode(data).decode()
        media.append({'key':key,'type':relation['type']})
    return {'blocks':text_blocks(root),'pictures':pictures,'media':media,'tables':[table_html(t) for t in root.findall('.//a:tbl',NS)],'omitted_images':omitted}

def render_page(page,i,title,deck_title,settings):
    blocks=page['blocks'];remove=set(settings.get('remove_text',[]));title_consumed=False;clean=[];removed=[]
    for block in blocks:
        items=[]
        for s in block:
            if s in remove or re.fullmatch(r'单击此处添加(?:页面的)?标题',s):removed.append(s);continue
            if s==title and not title_consumed:title_consumed=True;continue
            items.append(s)
        if items:clean.append(items)
    body_length=sum(len(x) for b in clean for x in b)
    mode=settings.get('mode','')
    if not mode:mode='reading' if body_length>600 else 'media-only' if body_length<75 and page['pictures'] else ''
    if page['media'] and body_length<200:mode='media-only'
    cls=['slide']
    if mode:cls.append(mode)
    if body_length>280:cls.append('dense')
    if not page['pictures'] and not page['media']:cls.append('text-only')
    content=[]
    # Text remains grouped by original text box; never use an entire long paragraph as a heading.
    for block in clean:
        content.append('<div class="block">'+''.join(f'<p class="edit" contenteditable="true">{esc(s)}</p>' for s in block)+'</div>')
    content+=page['tables']
    body=''.join(content)
    if mode=='media-only' and body:
        # Caption/context is kept above images and is directly editable.
        before=f'<div class="media-context edit" data-feedback-block="slide-{i}-media-context" contenteditable="true">{esc(chr(10).join(s for b in clean for s in b))}</div>'
        # The original short-text/image heuristic discarded native tables here.
        # Keep them as editable copy even when the surrounding slide is media-only.
        body=''.join(page['tables']);content=[]
    else:before=''
    figures=[]
    posters={}
    for pic in page['pictures']:
        if pic['video']:posters[pic['video']]=pic['data']
    for media in page['media']:
        tag=media['type'];poster=f' poster="{posters[media["key"]]}"' if media['key'] in posters and tag=='video' else ''
        figures.append(f'<figure class="video-wrap"><{tag} controls preload="metadata" playsinline data-video="{esc(media["key"])}"{poster}></{tag}><figcaption>视频案例 · 点击播放</figcaption></figure>')
    for j,pic in enumerate(page['pictures']):
        # Video poster is already visible in the player; keep its original bytes in poster.
        if pic['video']:continue
        figures.append(f'<figure><img class="photo" src="{pic["data"]}" alt="{esc(title)} · 图 {j+1}" loading="lazy" data-source="{esc(pic["key"])}"><figcaption>图 {j+1} · 点击放大</figcaption></figure>')
    gallery=f'<div class="media-grid scroll-zone count-{len(figures)}">'+''.join(figures)+'</div>' if figures else ''
    if mode=='cover':gallery=''
    copy=f'<div class="copy scroll-zone">{body}</div>' if body else ''
    if not gallery:cls.append('text-only')
    if mode=='cover':copy+=f'<span class="chapter-number" aria-hidden="true">{esc(settings.get("chapter",""))}</span>'
    headtag='h1' if mode=='cover' else 'h2'
    titleclass=' long-title' if len(title)>28 else ''
    note='<div class="reading-note">案例全文 · 可在正文区域滚动阅读和修改</div>' if mode=='reading' else '<div class="reading-note overflow-note" hidden>本页内容较多，可在正文或图片区域内滚动查看</div>'
    html_part=f'<section class="{" ".join(cls)}" data-no="{i:02}" data-title="{esc(title)}"><div class="eyebrow">{esc(deck_title)}</div><{headtag} class="edit{titleclass}" contenteditable="true">{esc(title)}</{headtag}>{before}<div class="frame">{copy}{gallery}</div>{note}</section>'
    number=0
    def identify(match):
        nonlocal number
        number+=1
        return '<'+match.group(1)+f' data-feedback-block="slide-{i}-block-{number}"'
    html_part=re.sub(r'<(p|h[1-4]|td|th|figure)(?=[\s>])',identify,html_part)
    return html_part,removed

def build_pptx(inp,out,config=None,video_cache=None,audit_path=None):
    config = config or {}
    inp, out = Path(inp), Path(out)
    assets={};cache={};parts=[];audit={'source':str(inp),'sha256':hashlib.sha256(inp.read_bytes()).hexdigest(),'pages':[]}
    deck_title=config.get('title',inp.stem)
    with ZipFile(inp) as z:
        names=slide_order(z)
        for i,name in enumerate(names,1):
            page=extract_page(z,name,config,cache,video_cache,assets)
            settings=config.get('pages',{}).get(str(i),{})
            texts=[s for b in page['blocks'] for s in b]
            title=settings.get('title') or (texts[0] if texts and len(texts[0])<65 else f'{deck_title} · 案例 {i}')
            part,removed=render_page(page,i,title,deck_title,settings);parts.append(part)
            audit['pages'].append({'page':i,'xml':name,'title':title,'source_text':texts,'source_characters':sum(map(len,texts)),'images':[p['key'] for p in page['pictures']],'media':[m['key'] for m in page['media']],'omitted_decoration':page['omitted_images'],'removed_text':removed,'tables':len(page['tables'])})
    options=''.join(f'<option value="{i}">{i+1}. {esc(p["title"])}</option>' for i,p in enumerate(audit['pages']))
    style=(ASSETS/'deck.css').read_text();js=(ASSETS/'deck.js').read_text()
    doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(deck_title)} · 可编辑课件</title><style>{style}</style></head>
<body data-filename="{esc(out.name)}"><nav id="toolbar"><span class="brand">{esc(deck_title)}</span><div class="tools"><button id="prev" aria-label="上一页">←</button><span id="counter">1 / {len(parts)}</span><button id="next" aria-label="下一页">→</button><select id="page-select" aria-label="跳到页面">{options}</select><button id="present">演示模式</button><button id="save">保存副本</button></div><span class="hint" id="save-status" aria-live="polite">点文字修改 · 点图片放大／双击换图 · 修改后保存副本</span></nav>
<div id="present-controls"><button id="present-prev" aria-label="演示上一页">←</button><button id="present-exit">退出演示</button><button id="present-next" aria-label="演示下一页">→</button></div><input type="file" id="replace-image" accept="image/*" hidden>
<main id="deck">{''.join(parts)}</main>
<dialog id="lightbox"><div class="lightbar"><strong id="light-title">图片</strong><small>长图可滚动阅读</small><button id="light-fit">适应窗口／原比例</button><button id="light-replace">替换图片</button><button id="light-close">关闭</button></div><div class="lightbody"><img id="light-image" alt="放大图片"></div></dialog>
<script type="application/json" id="media-data">{json.dumps(assets).replace('<',chr(92)+'u003c')}</script><script>{js}</script></body></html>'''
    doc=enhance_html(doc,'conversion-ppt','deck',1,src=str(out),native_editor=True)
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(doc)
    if audit_path:audit_path.parent.mkdir(parents=True,exist_ok=True);audit_path.write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    return {'file':str(out),'pages':len(parts),'images':sum(len(p['images']) for p in audit['pages']),'videos':len(assets),'bytes':out.stat().st_size,'audit':audit}


def extract_pptx_text(source):
    """Extract paragraph/table text in real presentation order, without Pillow."""
    pages = []
    with ZipFile(source) as archive:
        for number, name in enumerate(slide_order(archive), 1):
            root = E.fromstring(archive.read(name))
            text = "\n".join(filter(None, (para(p) for p in root.findall(".//a:p", NS))))
            if text.strip():
                pages.append("[第 " + str(number) + " 页]\n" + text)
    return "\n\n".join(pages)


def _folio_command():
    import os
    import shutil
    candidates = [str(Path.home() / ".local/bin/folio"), shutil.which("folio")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise LumenError("capability_unavailable", "目录图谱需要现有 Folio 引擎，请安装 Folio 后再转换",
                     {"capability": "graph", "owner": "Folio"})


def convert(state_dir=None, kind=None, source=None, project=None, tags=None):
    """Convert using the existing owner, then import a complete managed snapshot."""
    import shutil
    import subprocess
    import uuid
    from .store import Store, atomic_json
    if kind not in ("graph", "ppt"):
        raise LumenError("invalid_conversion", "转换用途应为 graph 或 ppt")
    if not source:
        raise LumenError("invalid_source", "请选择需要转换的来源")
    original = Path(source).expanduser().absolute()
    if original.is_symlink():
        raise LumenError("invalid_source", "转换来源不能是软链，请选择实际目录或文件")
    original = original.resolve()
    if kind == "graph" and not original.is_dir():
        raise LumenError("invalid_source", "目录图谱需要一个现有目录")
    if kind == "ppt" and (not original.is_file() or original.suffix.lower() not in (".pptx", ".potx")):
        raise LumenError("invalid_source", "课件转换需要现有 PPTX 或 POTX 文件")
    store = Store(state_dir)
    run_id = "conversion-" + uuid.uuid4().hex[:20]
    folder = store.root / "conversions" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    output = folder / ("目录图谱.html" if kind == "graph" else original.stem + "_可编辑.html")
    audit_path = folder / "audit.json"
    conversion = {"id":run_id,"kind":kind,"source":str(original),"output":str(output),"audit_path":str(audit_path)}
    try:
        if kind == "graph":
            executable = _folio_command()
            result = subprocess.run([executable,"graph",str(original),"-o",str(output),"--json"],
                                    capture_output=True,text=True,timeout=120)
            try:
                report = json.loads(result.stdout)
            except ValueError as error:
                raise LumenError("conversion_failed", "Folio 未返回有效转换结果",
                                 {"exit_code":result.returncode,"stderr":result.stderr[-2000:]}) from error
            if result.returncode or not report.get("ok") or not output.is_file():
                raise LumenError("conversion_failed", "目录图谱生成失败", report)
            graph_html = output.read_text(encoding="utf-8")
            if 'id="data"' not in graph_html or "function rebuild(" not in graph_html or "function render(" not in graph_html:
                raise LumenError("capability_incompatible", "Folio 图谱格式已变化，请更新 Lumen 转换适配器")
            adapter = '<script id="lumen-graph-adapter">' + (ASSETS / "graph-adapter.js").read_text() + "</script>"
            output.write_text(graph_html.replace("</body>", adapter + "</body>"), encoding="utf-8")
            conversion["engine"] = "Folio"
            conversion["report"] = report
            atomic_json(audit_path, conversion)
        else:
            # Keep an input checkpoint so provenance remains valid if the source changes.
            snapshot = folder / original.name
            shutil.copy2(original, snapshot)
            try:
                report = build_pptx(snapshot, output, audit_path=audit_path)
            except ModuleNotFoundError as error:
                raise LumenError("capability_unavailable", "课件转换缺少图片组件，请修复 Lumen 安装",
                                 {"dependency":error.name}) from error
            report["audit"]["source"] = str(original)
            report["audit"]["source_snapshot"] = str(snapshot)
            atomic_json(audit_path, report["audit"])
            conversion.update(engine="Lumen PPT compiler",pages=report["pages"],images=report["images"],
                              videos=report["videos"],bytes=report["bytes"],source_snapshot=str(snapshot),
                              limitations=["复杂图表、SmartArt和合并单元格需逐项核对；此转换不宣称无损还原或反写PPTX"])
        imported = store.import_document(str(output),kind="report" if kind=="ppt" else "portal",
                                         project=project,tags=tags,include_test=True)
        store.metadata(imported["document"]["id"],source_path=str(original))
        imported["document"] = store.document(imported["document"]["id"])
        imported["conversion"] = conversion
        atomic_json(folder / "conversion.json", dict(conversion,status="complete",document_id=imported["document"]["id"]))
        return imported
    except subprocess.TimeoutExpired as error:
        atomic_json(folder / "conversion.json",dict(conversion,status="failed",error="timeout"))
        raise LumenError("conversion_timeout", "转换达到两分钟期限，未登记为完成成果") from error
    except LumenError as error:
        atomic_json(folder / "conversion.json",dict(conversion,status="failed",error=error.as_dict()))
        raise
    except (ValueError,KeyError,OSError,E.ParseError,BadZipFile,LargeZipFile) as error:
        atomic_json(folder / "conversion.json",dict(conversion,status="failed",error=str(error)))
        raise LumenError("conversion_failed", "来源无法转换，原文件保持不变", {"reason":str(error)}) from error
