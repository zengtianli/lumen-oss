/* Lumen adapter for Folio's static graph. Folio remains scan/layout owner. */
(() => {
  'use strict';
  if (typeof rebuild !== 'function' || typeof render !== 'function' || typeof N === 'undefined' || !document.getElementById('data')) return;
  const originalRebuild = rebuild, originalRender = render;
  const encode = value => JSON.stringify(value).replace(/</g,'\\u003c').replace(/--/g,'\\u002d\\u002d');
  const idPrefix = 'graph-node:';
  const fieldId = (id,field) => idPrefix + id + ':' + field;
  const mark = () => {
    for (const node of document.querySelectorAll('.graph-node[data-node]')) {
      node.setAttribute('data-feedback-object','');
      node.dataset.feedbackBlock = fieldId(node.dataset.node,'object');
    }
    for (const [domId,field] of [['detail-title','name'],['description','desc']]) {
      const el=document.getElementById(domId);
      if(el){el.setAttribute('data-html-editable','');el.dataset.feedbackBlock=fieldId(state.id,field);}
    }
    for(const domId of ['map-title','map-subtitle','scope-note','relations-title','relation-count'])
      document.getElementById(domId)?.setAttribute('data-computed','');
  };
  rebuild = function(data) {
    originalRebuild(data);
    for(const [id,values] of Object.entries(D.lumen_overrides || {})) {
      if(!N[id])continue;
      for(const field of ['name','desc'])if(typeof values[field]==='string')N[id][field]=values[field];
    }
  };
  render = function() { originalRender(); mark(); };
  document.addEventListener('input',event=>{
    const field=event.target.id==='detail-title'?'name':event.target.id==='description'?'desc':null;
    if(!field||!N[state.id])return;
    const value=event.target.innerText ?? event.target.textContent;
    D.lumen_overrides ||= {}; D.lumen_overrides[state.id] ||= {};
    D.lumen_overrides[state.id][field]=value;N[state.id][field]=value;
    document.getElementById('data').textContent=encode(D);
    if(field==='name'){
      const node=[...document.querySelectorAll('.graph-node[data-node]')].find(node=>node.dataset.node===state.id);
      if(node?.querySelector('.node-name'))node.querySelector('.node-name').textContent=value;
      document.getElementById('map-title').textContent=value;
    }
  });
  window.addEventListener('lumen-feedback-locate',event=>{
    const identity=event.detail?.block_id || '';
    if(!identity.startsWith(idPrefix))return;
    const rest=identity.slice(idPrefix.length),last=rest.lastIndexOf(':'),id=rest.slice(0,last);
    if(N[id])navigate(id);
  });
  rebuild(D);render();
})();

