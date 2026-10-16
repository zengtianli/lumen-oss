"""Explicit document management; source files are never moved or deleted."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit, unquote, quote
from html import unescape, escape
import json
import os
import re
import shutil
import tempfile
import uuid

from .store import atomic_json, library_lock, metadata_revision, now, read_json
from .types import LumenError


def _expected(document, expected_version=None, expected_metadata_revision=None):
    if expected_version is not None and expected_version != document["version"]:
        raise LumenError("conflict", "文档版本已经变化，请重新读取后操作")
    if expected_metadata_revision is not None and expected_metadata_revision != document["metadata_revision"]:
        raise LumenError("conflict", "文档属性已经变化，请重新读取后操作")


def _write(store, before, updated):
    updated = {**updated,"updated_at":now()}
    updated["metadata_revision"] = metadata_revision(before,updated)
    atomic_json(store.document_root(before["id"])/"properties.json",updated)
    with store.connect() as db:
        db.execute("UPDATE documents SET metadata=? WHERE id=?",(json.dumps(updated,ensure_ascii=False),before["id"]))
    return {"document":updated}


def active_writers(store, document_id):
    from .jobs import list_tasks
    return [t for t in list_tasks(str(store.root)) if t.get("status") in {"queued","running","cancelling"}
            and t.get("params",{}).get("document_id")==document_id]


def rename(store, document_id, title, expected_version=None, expected_metadata_revision=None, sync_content_title=False):
    if not isinstance(title,str) or not title.strip() or len(title.strip())>1000:
        raise LumenError("invalid_title", "标题必须为 1 至 1000 个字符")
    if sync_content_title:
        current=store.get(document_id)
        _expected(current["document"],expected_version,expected_metadata_revision)
        if current["content"] is None:
            raise LumenError("unstructured_title", "普通 HTML 请用页面编辑修改正文标题；文档显示名可直接重命名")
        content=deepcopy(current["content"]); content["title"]=title.strip()
        # Avoid two separate writes: publishing and overriding title are atomic.
        with library_lock(store.root):
            before=store.document(document_id); _expected(before,expected_version,expected_metadata_revision)
            store.check_version(before,before["version"])
            from .rendering import render_document
            stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=store.root))
            try:
                doc={**before,"title":title.strip(),"title_override":title.strip(),"version":before["version"]+1,"updated_at":now()}
                doc["metadata_revision"]=metadata_revision(before,doc)
                rendered=render_document(content,stage/"site",document_id,doc["version"],feedback=current["feedback"],labels=store._labels())
                atomic_json(stage/"content.json",content); atomic_json(stage/"generated_content.json",read_json(store.version_root(document_id,before["version"])/"generated_content.json",content))
                atomic_json(stage/"feedback.json",current["feedback"])
                return store._publish(doc,stage,rendered["pages"],"rename-title")
            finally:
                if stage.exists(): shutil.rmtree(stage)
    return store.metadata(document_id,expected_version=expected_version,expected_metadata_revision=expected_metadata_revision,title=title.strip())


def trash(store, document_id, expected_version=None, expected_metadata_revision=None):
    with library_lock(store.root):
        doc=store.document(document_id); _expected(doc,expected_version,expected_metadata_revision)
        if doc.get("trashed"): return {"document":doc}
        writers=active_writers(store,document_id)
        if writers: raise LumenError("document_busy", "此文档有正在写入的任务，请先取消任务再删除", {"task_ids":[t["id"] for t in writers]})
        return _write(store,doc,{**doc,"trashed":True,"deleted_at":now()})


def untrash(store, document_id, expected_version=None, expected_metadata_revision=None):
    with library_lock(store.root):
        doc=store.document(document_id); _expected(doc,expected_version,expected_metadata_revision)
        if not doc.get("trashed"): return {"document":doc}
        return _write(store,doc,{**doc,"trashed":False,"deleted_at":None})


def purge(store, document_id, confirmed=False, expected_version=None, expected_metadata_revision=None):
    if confirmed is not True: raise LumenError("confirmation_required", "彻底删除不可恢复，必须明确 confirmed=true")
    root=store.document_root(document_id)
    marker=store.root/"purged"/(document_id+".json")
    with library_lock(store.root):
        pending=store.root/".purge"/document_id
        if root.is_symlink() or pending.is_symlink():
            raise LumenError("path_rejected", "托管或清理目录是符号链接，未删除")
        if root.exists() and pending.exists():
            raise LumenError("purge_conflict", "清理目录冲突，请检查已有清理记录")
        if not marker.exists():
            doc=store.document(document_id); _expected(doc,expected_version,expected_metadata_revision)
            if not doc.get("trashed"): raise LumenError("not_trashed", "只能彻底删除废纸篓中的文档")
            if active_writers(store,document_id): raise LumenError("document_busy", "关联写入任务仍在执行，请先取消")
            atomic_json(marker,{"document_id":document_id,"purged_at":now(),"version":doc["version"]})
        pending.parent.mkdir(parents=True,exist_ok=True)
        if root.exists():
            root.rename(pending)
        with store.connect() as db:
            for table,field in (("documents","id"),("page_index","document_id"),("positions","document_id"),("idempotency","document_id")):
                db.execute("DELETE FROM "+table+" WHERE "+field+"=?",(document_id,))
        if pending.exists(): shutil.rmtree(pending)
        for name in ("reading-cache","ai-materials"):
            cache=store.root/name/document_id
            if cache.exists() and not cache.is_symlink(): shutil.rmtree(cache)
        return {"document_id":document_id,"purged":True,"source_files_preserved":True}


def paths(store, document_id, page_id=None, version=None):
    current=store.get(document_id,version); doc=current["document"]; pages=current["pages"]
    if page_id:
        page=next((p for p in pages if p["id"]==page_id),None)
        if page is None: raise LumenError("invalid_page", "该章节不属于所选文档版本")
    else:
        home=(current.get("content") or {}).get("home")
        page=next((p for p in pages if p["absolute_path"]==doc["entry_path"] or home and p["id"]==home),None) or (pages[0] if pages else None)
    def entry(label,path):
        p=Path(path).expanduser().absolute()
        value={"label":label,"path":str(p),"file_url":p.as_uri(),"exists":p.exists(),"filename":p.name}
        if p.exists():
            stat=p.stat(); value.update(bytes=stat.st_size if p.is_file() else None,modified_at=stat.st_mtime,is_directory=p.is_dir())
        return value
    sources=list(dict.fromkeys([p for p in [doc.get("source_path"),*[s.get("path") for s in doc.get("sources",[]) if isinstance(s,dict)]] if p]))
    folder=store.version_root(document_id,doc["version"])/"site"
    total=sum(p.stat().st_size for p in store.document_root(document_id).rglob("*") if p.is_file() and not p.is_symlink())
    return {"document_id":document_id,"title":doc["title"],"version":doc["version"],"trashed":doc.get("trashed",False),"deleted_at":doc.get("deleted_at"),"page_count":len(pages),"storage_bytes":total,
            "current_html":entry("当前 HTML",page["absolute_path"]) if page else None,"entry_html":entry("文档入口",doc["entry_path"]),"folder":entry("文档文件夹",folder),"sources":[entry("原始来源" if p==doc.get("source_path") else "补充来源 / 编辑副本",p) for p in sources]}


def relink_source(store, document_id, source, expected_version=None, expected_metadata_revision=None):
    path=Path(source).expanduser().resolve()
    if not path.exists(): raise LumenError("source_missing", "新来源路径不存在")
    with library_lock(store.root):
        before=store.document(document_id); _expected(before,expected_version,expected_metadata_revision)
        store.check_version(before,before["version"])
        history=before.get("source_history",[])
        if before.get("source_path") and before["source_path"]!=str(path): history=[*history,{"path":before["source_path"],"replaced_at":now()}]
        sources=[{**s,"path":str(path)} if s.get("path")==before.get("source_path") else s for s in before.get("sources",[]) if isinstance(s,dict)]
        if not any(s.get("path")==str(path) for s in sources): sources.insert(0,{"path":str(path),"role":"relinked-original"})
        return _write(store,before,{**before,"source_path":str(path),"sources":sources,"source_history":history})


def duplicate(store, document_id, title=None, include_notes=False, include_feedback=False, include_history=False):
    with library_lock(store.root):
        before=store.document(document_id)
        if before.get("trashed"): raise LumenError("document_trashed", "请先恢复文档再复制")
        new_id="doc-"+uuid.uuid4().hex[:20]
        root=store.document_root(new_id)
        selected=store.versions(document_id)["versions"] if include_history else [{"version":before["version"]}]
        result=None
        try:
            from .rendering import enhance_html, render_document
            for i,old_version in enumerate(reversed(selected),1):
                old=store.version_root(document_id,old_version["version"])
                stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=store.root))
                try:
                    shutil.copytree(old,stage,dirs_exist_ok=True)
                    feedback=read_json(stage/"feedback.json",{}) if include_feedback else {}
                    doc={**before,"id":new_id,"version":i,"title":title or before["title"]+" 副本","title_override":title or before["title"]+" 副本","favorite":False,"archived":False,"trashed":False,"deleted_at":None,
                         "notes":before.get("notes","") if include_notes else "","metadata_revision":0,"created_at":now(),"updated_at":now(),"copied_from":{"document_id":document_id,"version":old_version["version"]}}
                    content=read_json(stage/"content.json")
                    pages=read_json(stage/"pages.json",[])
                    if content is not None:
                        shutil.rmtree(stage/"site"); rendered=render_document(content,stage/"site",new_id,i,feedback=feedback,labels=store._labels()); pages=rendered["pages"]
                    else:
                        for p in pages:
                            path=stage/"site"/p["relative_path"]
                            path.write_text(enhance_html(path.read_text(),new_id,p["id"],i,labels=store._labels(),feedback=feedback.get(p["id"])),encoding="utf-8")
                    atomic_json(stage/"feedback.json",feedback)
                    result=store._publish(doc,stage,pages,"duplicate")
                finally:
                    if stage.exists(): shutil.rmtree(stage)
            return result
        except Exception:
            # Leave complete published versions recoverable if an optional later
            # historical copy fails; do not erase already-published user content.
            raise


def rename_file(store, document_id, page_id, filename, expected_version, expected_metadata_revision=None):
    if not isinstance(filename,str) or Path(filename).name!=filename or filename in {".",".."} or not re.search(r"\.html?$",filename,re.I) or len(filename.encode())>240:
        raise LumenError("invalid_filename", "请输入不带目录的 HTML 文件名（.html 或 .htm）")
    with library_lock(store.root):
        before=store.document(document_id); _expected(before,expected_version,expected_metadata_revision); store.check_version(before,expected_version)
        old=store.version_root(document_id,before["version"])
        pages=read_json(old/"pages.json",[])
        page=next((p for p in pages if p["id"]==page_id),None)
        if not page: raise LumenError("invalid_page", "该章节不属于当前文档")
        old_relative=page["relative_path"]; new_relative=str(Path(old_relative).with_name(filename))
        if new_relative==old_relative: return {"document":before}
        if (old/"site"/new_relative).exists(): raise LumenError("file_exists", "同目录文件名已存在，未覆盖")
        stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=store.root))
        try:
            shutil.copytree(old,stage,dirs_exist_ok=True)
            source=(old/"site"/old_relative).resolve()
            destination=stage/"site"/new_relative
            (stage/"site"/old_relative).rename(destination)
            # Rewriting only HTML URL attributes; same-folder rename leaves all
            # CSS/JS/image dependencies unchanged. IDs and anchors remain stable.
            attribute=re.compile(r"(\b(?:href|src|action)\s*=\s*)([\"'])(.*?)(\2)",re.I|re.S)
            for target in (stage/"site").rglob("*.htm*"):
                relative=target.relative_to(stage/"site")
                original=old/"site"/(old_relative if str(relative)==new_relative else relative)
                def rewrite(match):
                    value=unescape(match[3]); url=urlsplit(value)
                    if url.scheme or url.netloc or not url.path: return match[0]
                    resolved=(original.parent/unquote(url.path)).resolve()
                    if resolved!=source: return match[0]
                    path=os.path.relpath(destination,target.parent)
                    new=quote(path,safe="/.")+("?"+url.query if url.query else "")+("#"+url.fragment if url.fragment else "")
                    return match[1]+match[2]+escape(new,quote=True)+match[4]
                markup=attribute.sub(rewrite,target.read_text(encoding="utf-8"))
                target.write_text(markup,encoding="utf-8")
            page["relative_path"]=new_relative
            alias=stage/"site"/old_relative
            redirect=quote(os.path.relpath(destination,alias.parent),safe="/.")
            alias.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+escape(redirect,quote=True)+'"><a href="'+escape(redirect,quote=True)+'">文件已重命名，打开当前页面</a>',encoding="utf-8")
            document={**before,"version":before["version"]+1,"updated_at":now()}
            if before.get("entry_relative")==old_relative: document["entry_relative"]=new_relative
            document["page_path_overrides"]={**before.get("page_path_overrides",{}),page_id:new_relative}
            from .rendering import enhance_html
            feedback=read_json(stage/"feedback.json",{})
            for p in pages:
                target=stage/"site"/p["relative_path"]
                target.write_text(enhance_html(target.read_text(),document_id,p["id"],document["version"],labels=store._labels(),feedback=feedback.get(p["id"])),encoding="utf-8")
            return store._publish(document,stage,pages,"rename-file")
        finally:
            if stage.exists(): shutil.rmtree(stage)


def apply_page_overrides(document, site, pages):
    """Keep explicit filenames across structured rebuilds; retain old URL aliases."""
    overrides=document.get("page_path_overrides",{})
    for page in pages:
        target=overrides.get(page["id"])
        if not target or target==page["relative_path"]: continue
        old=site/page["relative_path"]; new=site/target
        if not new.resolve().is_relative_to(site.resolve()) or old.parent!=new.parent:
            raise LumenError("invalid_filename", "保存的文件名超出当前章节目录")
        if new.exists(): raise LumenError("file_exists", "章节文件名冲突，未发布新版本")
        old.rename(new)
        redirect=quote(new.name,safe=".")
        old.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url='+escape(redirect,quote=True)+'"><a href="'+escape(redirect,quote=True)+'">打开当前页面</a>',encoding="utf-8")
        if document.get("entry_relative")==page["relative_path"]: document["entry_relative"]=target
        page["relative_path"]=target


def batch(store, document_ids, action, values=None):
    if not isinstance(document_ids,list) or not document_ids or len(document_ids)>500 or any(not isinstance(i,str) for i in document_ids):
        raise LumenError("invalid_batch", "批量操作需要 1 至 500 个文档编号")
    if action not in {"trash","untrash","metadata"}: raise LumenError("invalid_batch", "批量操作支持 trash/untrash/metadata")
    values=values or {}
    if not isinstance(values,dict) or set(values)-{"project","tags","archived","favorite"}: raise LumenError("invalid_batch", "批量属性仅支持项目、标签、归档和收藏")
    results=[]
    for document_id in dict.fromkeys(document_ids):
        try:
            doc=store.document(document_id)
            expected={"expected_version":doc["version"],"expected_metadata_revision":doc["metadata_revision"]}
            result=store.metadata(document_id,**expected,**values) if action=="metadata" else globals()[action](store,document_id,**expected)
            results.append({"document_id":document_id,"ok":True,**result})
        except LumenError as error: results.append({"document_id":document_id,"ok":False,"error":error.as_dict()})
    return {"results":results,"succeeded":sum(r["ok"] for r in results),"failed":sum(not r["ok"] for r in results)}


ACTIONS={"rename":rename,"trash":trash,"untrash":untrash,"purge":purge,"paths":paths,"relink-source":relink_source,"duplicate":duplicate,"rename-file":rename_file,"batch":batch}
