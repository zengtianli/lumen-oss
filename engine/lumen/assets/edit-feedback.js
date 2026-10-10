/* Lumen's editable local reader. File drafts and native commits share one payload. */
(() => {
  'use strict';
  if (window.LumenReader) return;
  const json = value => JSON.stringify(value).replace(/</g, '\\u003c').replace(/--/g, '\\u002d\\u002d');
  const download = (text, name, type) => {
    const a = document.createElement('a'), url = URL.createObjectURL(new Blob([text], {type: type + ';charset=utf-8'}));
    a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const plainText = el => (el.innerText ?? el.textContent ?? '').replace(/\r\n/g, '\n');
  const pack = (comment, round) => {
    const thread = [...(comment.thread || [])];
    if (comment.draft?.trim()) thread.push({who: '我', text: comment.draft.trim(), round});
    const out = {...comment, thread}; delete out.draft; return out;
  };
  const inlineHtml = source => {
    const template = document.createElement('template'); template.innerHTML = source;
    const allowed = new Set(['B','STRONG','I','EM','U','S','DEL','SUP','SUB','CODE','BR','SPAN','A','DIV','P']);
    const clean = parent => [...parent.childNodes].forEach(n => {
      if (n.nodeType === 3) return;
      if (n.nodeType !== 1) { n.remove(); return; }
      if (!allowed.has(n.tagName)) { n.replaceWith(document.createTextNode(n.textContent)); return; }
      for (const attr of [...n.attributes]) {
        const href = attr.value.trim();
        const safeLink = /^(https?:|mailto:|tel:)/i.test(href) || (!/[\x00-\x20:]/.test(href) && !href.startsWith('\\'));
        const safe = ['class','id','title','aria-label'].includes(attr.name) || (n.tagName === 'A' && attr.name === 'href' && safeLink);
        if (!safe) n.removeAttribute(attr.name);
      }
      clean(n);
    });
    clean(template.content); return template.innerHTML;
  };
  const discussion = s => {
    const comments = [...(s.global || []), ...(s.comments || [])].map(c =>
      '- [' + c.id + ' · ' + c.st + '] ' + c.head + '\n  定位：' + (c.page_id || s.page_id) + ' / ' +
      (c.block_id || '全文') + '\n  原文：' + (c.anchor || '') + '\n  意见：' + (c.body || '') + '\n' +
      (c.thread || []).map(t => '  - ' + t.who + '：' + t.text).join('\n')).join('\n\n');
    return '# 审阅讨论稿：' + s.title + '\n\n' + comments + '\n\n## 修改后正文\n\n' +
      (s.article_md || '') + '\n\n<!-- annotate-state\n' + json(s) + '\nannotate-state -->\n';
  };
  window.AnnotateFeedback = {json, download, pack, discussion};
  const start = () => {
    const initEl = document.getElementById('html-feedback-init');
    if (!initEl || document.getElementById('html-feedback-ui')) return;
    const init = JSON.parse(initEl.textContent), labels = init.labels || {};
    const label = (key, fallback) => typeof labels[key] === 'string' ? labels[key] : fallback;
    const embedded = document.getElementById('html-feedback-state');
    const draftKey = 'lumen:draft:' + init.document_id + ':' + init.page_id;
    let state = null, usingDraft = false;
    try { if (embedded) state = JSON.parse(embedded.textContent); } catch (_) {}
    try {
      const draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
      if (draft?.build === init.build && (!state || (draft.updated_at || 0) > (state.updated_at || 0))) { state = draft; usingDraft = true; }
    } catch (_) {}
    state ||= {title: init.title, src: init.src, round: 1, key: init.page_id, page_id: init.page_id,
      document_id: init.document_id, build: init.build, global: [], comments: [], blocks: {}, seq: 0};
    state.global ||= []; state.comments ||= []; state.blocks ||= {};
    state.page_id = init.page_id; state.document_id = init.document_id; state.build = init.build;
    const blocks = new Map(), sources = new Map(); let order = 0;
    const discover = () => {
      for (const [id, b] of blocks) if (!b.el.isConnected || b.el.dataset.feedbackBlock !== id) blocks.delete(id);
      const explicit = [...document.querySelectorAll('[data-html-editable],[data-feedback-block],[data-feedback-object],[data-lumen-source]')];
      const semantic = [...document.querySelectorAll('main p,main h1,main h2,main h3,main h4,main li,main blockquote,main td,main th,main figure,article p,article h1,article h2,article h3,article li,article blockquote,article td,article th,article figure')];
      const candidates = [...new Set([...explicit, ...semantic])];
      if (!candidates.length) candidates.push(...document.querySelectorAll('body > p,body > h1,body > h2,body > h3,body > blockquote,body > figure'));
      const excludes = 'nav,footer,form,button,input,select,textarea,script,style,[role="navigation"],[role="toolbar"],[data-feedback-ignore],[data-computed],[data-html-editable="false"]';
      for (const el of candidates) {
        const explicitObject = el.hasAttribute('data-feedback-object');
        if (el.closest('#html-feedback-ui') || (!explicitObject && el.closest(excludes)) ||
            (!el.hasAttribute('data-lumen-source') && el.closest('header') && !el.closest('main,article')) ||
            (!explicitObject && el.querySelector('button,input,select,textarea,script,[data-computed]'))) continue;
        if (!el.matches('figure,[data-feedback-object]') && candidates.some(other => other !== el && el.contains(other))) continue;
        const id = el.dataset.feedbackBlock || el.id || 'block-' + (++order);
        if (blocks.has(id)) continue;
        el.dataset.feedbackBlock = id;
        const nativeOwned = Boolean(init.native_editor) || (!el.hasAttribute('data-lumen-owned-editor') && el.hasAttribute('contenteditable') && el.getAttribute('contenteditable') !== 'false');
        const editable = !nativeOwned && !el.matches('figure,svg,canvas,img,[data-feedback-object]') && !el.querySelector('svg,canvas,img,video,iframe');
        const initial = plainText(el), original = state.blocks[id]?.original_text ?? initial;
        blocks.set(id, {el, editable, nativeOwned, original, initial});
        if (editable && state.blocks[id]?.changed && state.blocks[id]?.text != null && (usingDraft || !el.hasAttribute('data-lumen-source'))) {
          if (state.blocks[id].html != null) el.innerHTML = inlineHtml(state.blocks[id].html); else el.textContent = state.blocks[id].text;
        }
        if (!nativeOwned) { el.setAttribute('data-lumen-owned-editor',''); el.setAttribute('contenteditable', 'false'); }
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
        if (el.dataset.lumenSource) {
          const path = el.dataset.lumenSource;
          if (!sources.has(path)) {
            let original; try { original = JSON.parse(el.dataset.lumenOriginal); } catch (_) { original = initial; }
            sources.set(path, {original, entries: []});
          }
          const entries = sources.get(path).entries;
          if (!entries.some(entry => entry.el === el)) entries.push({el, initial, part: Number(el.dataset.lumenPart || 0)});
        }
      }
    };
    discover();
    const ui = document.createElement('section'); ui.id = 'html-feedback-ui'; ui.setAttribute('aria-label', label('editor_label', '页面编辑与讨论'));
    ui.innerHTML = '<div class="hf-bar"><button data-action="edit"></button><button data-action="save"></button><button data-action="comment"></button><button data-action="panel"></button><button data-action="html"></button><button data-action="md"></button><span class="hf-saved" role="status"></span></div><aside class="hf-panel" hidden><h2></h2><p></p><div class="hf-cards"></div><button data-action="close"></button></aside>';
    document.body.append(ui);
    const texts = {edit:label('edit', '编辑正文'), save:label('save', '保存到知识库'), comment:label('comment', '添加批注'),
      panel:label('feedback', '意见'), html:label('download_html', '下载修改稿'), md:label('export_discussion', '导出讨论稿'), close:label('close', '收起意见')};
    for (const [action,text] of Object.entries(texts)) ui.querySelector('[data-action="' + action + '"]').textContent = text;
    if (init.native_editor) ui.querySelector('[data-action="edit"]').hidden = true;
    if (window.webkit?.messageHandlers?.lumenSave) {
      for (const action of ['edit','save','html']) ui.querySelector('[data-action="' + action + '"]').hidden = true;
    }
    ui.querySelector('h2').textContent = label('page_feedback', '本页意见');
    ui.querySelector('.hf-panel > p').textContent = label('feedback_help', '选择正文或图片添加意见，保存后可交给 agent 继续处理。');
    const panel = ui.querySelector('.hf-panel'), cards = ui.querySelector('.hf-cards'), savedStatus = ui.querySelector('.hf-saved');
    savedStatus.textContent = label('reading_status', '阅读模式');
    let active = null, selection = '', editing = false, busy = false, pendingPayload = null;
    const collect = () => {
      for (const [id,b] of blocks) state.blocks[id] = {id, kind:b.el.tagName.toLowerCase(), editable:b.editable,
        original_text:b.original, text:plainText(b.el), changed:plainText(b.el) !== b.original,
        ...(b.el.dataset.lumenSource ? {source_path:b.el.dataset.lumenSource} : {}),
        ...(b.editable ? {html:inlineHtml(b.el.innerHTML)} : {})};
      state.updated_at = Date.now();
    };
    const draft = () => {
      collect();
      try { localStorage.setItem(draftKey, JSON.stringify(state)); savedStatus.textContent = label('draft_saved', '草稿已保留 · 保存后写入知识库'); }
      catch (_) { savedStatus.textContent = label('draft_unavailable', '草稿存储不可用，请保存或下载修改稿'); }
    };
    const choose = el => {
      const block = el?.closest?.('[data-feedback-block]');
      if (!block || !blocks.has(block.dataset.feedbackBlock)) return;
      for (const b of blocks.values()) b.el.classList.remove('hf-active');
      if (active !== block.dataset.feedbackBlock) selection = '';
      active = block.dataset.feedbackBlock; block.classList.add('hf-active');
    };
    const patches = () => {
      const result = [];
      for (const [path,group] of sources) {
        const entries = group.entries.filter(e => e.el.isConnected).sort((a,b) => a.part-b.part);
        if (entries.some(e => plainText(e.el) !== e.initial)) {
          let value = entries.map(e => plainText(e.el)).join('\n\n');
          if (typeof group.original === 'number') {
            const raw = value.trim();
            if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(raw) || !Number.isFinite(Number(raw)))
              throw new Error(label('invalid_number', '数值单元格只能填写有效数字') + '：' + path);
            value = Number(raw);
          }
          result.push({path, value, original:group.original});
        }
      }
      return result;
    };
    const feedbackSnapshot = () => {
      collect();
      return {...state, title:document.querySelector('[data-lumen-source$="/title"]')?.textContent || state.title,
        global:state.global.map(c => pack(c,state.round)), comments:state.comments.map(c => pack(c,state.round)),
        article_md:'# ' + state.title + '\n\n' + [...blocks.values()].filter(b => b.editable).map(b => plainText(b.el)).join('\n\n'),
        pages:[{id:init.page_id,src:init.src,blocks:Object.values(state.blocks)}]};
    };
    const saveHtml = (withBase=false) => {
      const exported = feedbackSnapshot(), clone = document.documentElement.cloneNode(true);
      clone.querySelector('#html-feedback-ui')?.remove(); clone.querySelector('#html-feedback-state')?.remove();
      clone.querySelectorAll('[data-feedback-block]').forEach(el => {
        if (!blocks.get(el.dataset.feedbackBlock)?.nativeOwned) el.setAttribute('contenteditable','false');
        el.classList.remove('hf-active');
      });
      if (withBase) {
        let base = clone.querySelector('base');
        if (!base) { base = document.createElement('base'); clone.querySelector('head')?.append(base); }
        base.href = document.baseURI;
      }
      if (init.native_editor) {
        clone.querySelector('body').classList.remove('presenting');
        clone.querySelectorAll('.edit').forEach(el => el.setAttribute('contenteditable','true'));
        clone.querySelectorAll('[data-video]').forEach(el => el.removeAttribute('src'));
        clone.querySelector('#lightbox')?.removeAttribute('open');
        clone.querySelector('#light-image')?.removeAttribute('src');
      }
      const st = document.createElement('script'); st.id = 'html-feedback-state'; st.type = 'application/json'; st.textContent = json(exported);
      clone.querySelector('body').append(st);
      return '<!doctype html>\n' + clone.outerHTML;
    };
    const snapshot = () => ({document_id:init.document_id,page_id:init.page_id,expected_version:init.version,
      patches:patches(),feedback:feedbackSnapshot(),html:saveHtml()});
    const saved = result => {
      const value = result?.result || result;
      if (result?.ok === false || value?.error) {
        busy = false; pendingPayload = null; savedStatus.textContent = result?.error?.message || value?.error?.message || label('save_failed','保存失败');
        return;
      }
      if (value?.document?.version) init.version = value.document.version;
      initEl.textContent = json(init);
      for (const [path, group] of sources) {
        if (group.entries.some(e => plainText(e.el) !== e.initial)) {
          const accepted = pendingPayload?.patches?.find(patch => patch.path === path);
          group.original = accepted ? accepted.value : group.entries.map(e => plainText(e.el)).join('\n\n');
        }
        for (const e of group.entries) { e.initial = plainText(e.el); e.el.dataset.lumenOriginal = JSON.stringify(group.original); }
      }
      try { localStorage.removeItem(draftKey); } catch (_) {}
      busy = false; pendingPayload = null; savedStatus.textContent = label('committed', '已保存到知识库');
      window.dispatchEvent(new CustomEvent('lumen-reader-saved', {detail:value}));
    };
    const save = () => {
      let payload;
      try { payload = snapshot(); }
      catch (error) { savedStatus.textContent = error.message; return null; }
      if (busy) return payload;
      pendingPayload = payload;
      const bridge = window.webkit?.messageHandlers?.lumenSave;
      if (bridge) {
        busy = true; savedStatus.textContent = label('saving','正在保存…');
        try { bridge.postMessage(payload); } catch (error) { busy=false; savedStatus.textContent=error.message; }
      } else {
        download(payload.html, init.page_id + '_修改稿.html', 'text/html');
        savedStatus.textContent = label('standalone_saved','已下载修改稿 · 在 Lumen 中导入可写入知识库');
      }
      return payload;
    };
    const setEditing = enabled => {
      editing = Boolean(enabled);
      for (const b of blocks.values()) if (b.editable) b.el.setAttribute('contenteditable',String(editing));
      ui.querySelector('[data-action="edit"]').textContent = editing ? label('read','返回阅读') : texts.edit;
      savedStatus.textContent = editing ? label('editing_status','编辑模式 · ⌘S 保存') : label('reading_status','阅读模式');
      return editing;
    };
    const button = (text,fn) => { const b=document.createElement('button'); b.type='button'; b.textContent=text; b.onclick=fn; return b; };
    const render = () => {
      cards.replaceChildren(); ui.querySelector('[data-action="panel"]').textContent = texts.panel + ' ' + state.comments.length;
      for (const c of state.comments) {
        const card=document.createElement('section'); card.className='hf-card'; card.dataset.id=c.id;
        const locate=button((c.block_id || '全文') + ' · ' + c.head, () => {
          window.dispatchEvent(new CustomEvent('lumen-feedback-locate',{detail:{block_id:c.block_id,page_id:init.page_id}}));
          const b=blocks.get(c.block_id); if(b){choose(b.el);b.el.scrollIntoView({block:'center'});}
        });
        const quote=document.createElement('blockquote'); quote.textContent=c.anchor || '';
        const body=document.createElement('textarea'); body.setAttribute('aria-label',label('comment_body','批注内容'));
        body.value=c.body || ''; body.oninput=()=>{c.body=body.value;draft();};
        const status=document.createElement('select'); status.setAttribute('aria-label',label('comment_status','讨论状态'));
        for(const value of ['待讨论','同意改','不同意','已改','保留']){
          const o=document.createElement('option');o.value=o.textContent=value;o.selected=value===c.st;status.append(o);
        }
        status.onchange=()=>{c.st=status.value;draft();};
        const thread=document.createElement('div');
        for(const t of c.thread || []){const p=document.createElement('p');p.textContent=t.who + '：' + t.text;thread.append(p);}
        const reply=document.createElement('textarea');reply.placeholder=label('reply_placeholder','回复 / 下一轮处理意见');reply.value=c.draft || '';reply.oninput=()=>{c.draft=reply.value;draft();};
        card.append(locate,quote,body,status,thread,reply,button(label('delete_comment','删除批注'),()=>{
          state.comments=state.comments.filter(x=>x.id!==c.id);draft();render();
        }));cards.append(card);
      }
    };
    const addComment = (blockId,text='') => {
      const b=blocks.get(blockId || active); if(!b){savedStatus.textContent=label('choose_block','先选择一个正文区块或图片');return null;}
      choose(b.el);
      const comment={id:'u'+state.round+'_'+(++state.seq),page_id:init.page_id,block_id:active,
        anchor:selection || plainText(b.el).trim() || b.el.getAttribute('aria-label') || active,
        lv:'me',head:label('my_comment','我的批注'),body:String(text),st:'待讨论',thread:[],anchored:true};
      state.comments.push(comment);selection='';draft();render();panel.hidden=false;return comment.id;
    };
    document.addEventListener('click',e=>{
      if(!ui.contains(e.target))choose(e.target);
      if(editing && e.target.closest?.('[data-feedback-block]') && e.target.closest?.('a'))e.preventDefault();
    });
    document.addEventListener('focusin',e=>{if(!ui.contains(e.target))choose(e.target);});
    document.addEventListener('selectionchange',()=>{
      const s=window.getSelection();if(!s?.rangeCount||s.isCollapsed)return;
      const n=s.anchorNode?.nodeType===1?s.anchorNode:s.anchorNode?.parentElement;choose(n);
      const b=blocks.get(active);if(b&&b.el.contains(s.anchorNode)&&b.el.contains(s.focusNode))selection=s.toString();
    });
    document.addEventListener('input',e=>{if(e.target.closest?.('[data-feedback-block]')&&!ui.contains(e.target))draft();});
    document.addEventListener('paste',e=>{
      const el=e.target.closest?.('[data-feedback-block]');if(!editing||!el||ui.contains(el)||blocks.get(el.dataset.feedbackBlock)?.nativeOwned)return;
      e.preventDefault();const text=e.clipboardData?.getData('text/plain') || '',s=getSelection();if(!s?.rangeCount)return;
      const r=s.getRangeAt(0);if(!el.contains(r.commonAncestorContainer))return;
      r.deleteContents();const t=document.createTextNode(text);r.insertNode(t);r.setStartAfter(t);r.collapse(true);s.removeAllRanges();s.addRange(r);draft();
    });
    document.addEventListener('drop',e=>{if(editing&&e.target.closest?.('[data-feedback-block]'))e.preventDefault();});
    document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'){e.preventDefault();save();}});
    let discoveryTimer;
    new MutationObserver(changes=>{
      if(changes.every(c=>ui.contains(c.target)))return;
      clearTimeout(discoveryTimer);discoveryTimer=setTimeout(()=>{discover();for(const b of blocks.values())if(b.editable)b.el.setAttribute('contenteditable',String(editing));},80);
    }).observe(document.body,{childList:true,subtree:true});
    ui.addEventListener('mousedown',e=>{if(e.target.closest('[data-action="comment"]'))e.preventDefault();});
    ui.addEventListener('click',e=>{
      const action=e.target.closest('[data-action]')?.dataset.action;
      if(action==='edit')setEditing(!editing);
      if(action==='save')save();
      if(action==='panel'||action==='close')panel.hidden=action==='close'?true:!panel.hidden;
      if(action==='comment'){if(addComment(active)!==null)cards.lastElementChild?.querySelector('textarea')?.focus();}
      if(action==='md')download(discussion(feedbackSnapshot()),'审阅讨论稿_'+init.page_id+'_r'+state.round+'.md','text/markdown');
      if(action==='html')download(saveHtml(true),init.page_id+'_修改稿.html','text/html');
    });
    render();
    window.AnnotateFeedback.page={snapshot:feedbackSnapshot,saveHtml,pageId:init.page_id};
    window.LumenReader={snapshot,patches,save,saved,setEditing,addComment,feedback:feedbackSnapshot,pageId:init.page_id,
      get pendingSave(){return pendingPayload;},getPendingSave:()=>pendingPayload};
    window.dispatchEvent(new CustomEvent('lumen-reader-ready'));
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
