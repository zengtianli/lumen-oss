const slides=[...document.querySelectorAll('.slide')];
const assets=JSON.parse(document.getElementById('media-data').textContent);
let index=0,dirty=false,imageTarget=null,clickTimer=null;
const hint='点文字修改 · 点图片放大／双击换图 · 修改后保存副本';
const counter=document.getElementById('counter'),selector=document.getElementById('page-select'),status=document.getElementById('save-status');
const box=document.getElementById('lightbox'),lightImage=document.getElementById('light-image');
function changed(){dirty=true;status.textContent='有修改，请点击「保存副本」';status.classList.add('changed')}
function show(n){
  document.querySelectorAll('video,audio').forEach(v=>v.pause());
  index=Math.max(0,Math.min(n,slides.length-1));
  slides.forEach((s,i)=>s.classList.toggle('active',i===index));
  counter.textContent=`${index+1} / ${slides.length}`;selector.value=String(index);
  slides[index].querySelectorAll('[data-video]').forEach(v=>{if(!v.getAttribute('src'))v.src=assets[v.dataset.video]});
  requestAnimationFrame(()=>{const s=slides[index],note=s.querySelector('.overflow-note');if(note)note.hidden=![...s.querySelectorAll('.scroll-zone')].some(z=>z.scrollHeight>z.clientHeight+6)});
  window.scrollTo({top:0,behavior:'instant'});
}
function present(on){document.body.classList.toggle('presenting',on);document.querySelectorAll('.edit').forEach(el=>el.contentEditable=String(!on));if(on)document.activeElement?.blur()}
document.getElementById('prev').onclick=()=>show(index-1);document.getElementById('next').onclick=()=>show(index+1);
selector.onchange=()=>show(Number(selector.value));
document.getElementById('present').onclick=()=>present(true);document.getElementById('present-exit').onclick=()=>present(false);
document.getElementById('present-prev').onclick=()=>show(index-1);document.getElementById('present-next').onclick=()=>show(index+1);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){if(box.open)box.close();else present(false);return}
  if(box.open||e.target.isContentEditable||['INPUT','TEXTAREA','SELECT','VIDEO'].includes(e.target.tagName))return;
  if(['ArrowRight','PageDown','ArrowLeft','PageUp','Home','End'].includes(e.key))e.preventDefault();
  if(['ArrowRight','PageDown'].includes(e.key))show(index+1);
  if(['ArrowLeft','PageUp'].includes(e.key))show(index-1);
  if(e.key==='Home')window.addEventListener('lumen-reader-saved',()=>{dirty=false;status.textContent='已保存到知识库';status.classList.remove('changed')});
show(0);if(e.key==='End')show(slides.length-1);
});
document.querySelectorAll('.edit').forEach(el=>{
  el.addEventListener('input',changed);
  el.addEventListener('paste',e=>{
    const text=e.clipboardData?.getData('text/plain');if(text===undefined)return;e.preventDefault();
    const selection=window.getSelection();if(!selection.rangeCount)return;
    const range=selection.getRangeAt(0);range.deleteContents();const node=document.createTextNode(text);range.insertNode(node);range.setStartAfter(node);range.collapse(true);selection.removeAllRanges();selection.addRange(range);changed();
  });
});
function openImage(img){imageTarget=img;lightImage.src=img.src;lightImage.classList.add('fit');document.getElementById('light-title').textContent=img.alt;box.showModal()}
document.querySelectorAll('.photo').forEach(img=>{
  img.onclick=()=>{clearTimeout(clickTimer);clickTimer=setTimeout(()=>openImage(img),230)};
  img.ondblclick=()=>{clearTimeout(clickTimer);if(document.body.classList.contains('presenting'))return;imageTarget=img;document.getElementById('replace-image').click()};
});
document.getElementById('light-close').onclick=()=>box.close();
document.getElementById('light-fit').onclick=()=>lightImage.classList.toggle('fit');
document.getElementById('light-replace').onclick=()=>document.getElementById('replace-image').click();
document.getElementById('replace-image').onchange=e=>{
  const file=e.target.files[0];if(!file||!imageTarget)return;
  if(!file.type.startsWith('image/')){status.textContent='请选择图片文件';return}
  const target=imageTarget,reader=new FileReader();reader.onload=()=>{target.src=reader.result;target.alt='替换后的课程图片';if(box.open)lightImage.src=reader.result;changed()};reader.readAsDataURL(file);e.target.value='';
};
document.getElementById('save').onclick=()=>{
  if(window.webkit?.messageHandlers?.lumenSave && window.LumenReader){window.LumenReader.save();return}
  const clone=document.documentElement.cloneNode(true);
  clone.querySelectorAll('.slide').forEach((s,i)=>s.classList.toggle('active',i===0));
  clone.querySelector('body').classList.remove('presenting');clone.querySelectorAll('.edit').forEach(el=>el.contentEditable='true');
  clone.querySelectorAll('[data-video]').forEach(v=>v.removeAttribute('src'));
  clone.querySelector('#lightbox').removeAttribute('open');clone.querySelector('#light-image').removeAttribute('src');
  clone.querySelector('#counter').textContent=`1 / ${slides.length}`;
  clone.querySelector('#page-select').querySelectorAll('option').forEach((o,i)=>o.selected=i===0);
  clone.querySelector('#save-status').textContent=hint;clone.querySelector('#save-status').classList.remove('changed');
  const blob=new Blob(['<!doctype html>\n',clone.outerHTML],{type:'text/html;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=document.body.dataset.filename.replace(/\.html$/,'_修改版.html');document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),30000);
  dirty=false;status.textContent='已生成修改版，请检查浏览器下载并把 HTML 发回';status.classList.remove('changed');
};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
show(0);

