// Mockup-only behaviour. Build notes toggle, popovers, option groups that fill, time picker, toast, review shortcuts.
(function(){
  var html=document.documentElement,q=new URLSearchParams(location.search);
  if(q.get('notes')==='1')localStorage.setItem('ov-notes','1'); if(q.get('notes')==='0')localStorage.removeItem('ov-notes');
  if(localStorage.getItem('ov-notes'))html.classList.add('show-notes');
  window.toggleNotes=function(){html.classList.toggle('show-notes');html.classList.contains('show-notes')?localStorage.setItem('ov-notes','1'):localStorage.removeItem('ov-notes');};
  window.pop=function(id,ev){if(ev)ev.stopPropagation();document.querySelectorAll('.pop.open').forEach(function(p){if(p.id!==id)p.classList.remove('open')});var el=document.getElementById(id);if(el)el.classList.toggle('open');};
  window.toast=function(m){var t=document.getElementById('toast');if(!t)return;t.querySelector('span').textContent=m;t.classList.add('show');clearTimeout(window.__t);window.__t=setTimeout(function(){t.classList.remove('show')},4000);};
  document.addEventListener('click',function(e){
    var o=e.target.closest('.opt,.card-opt');
    if(o&&o.parentElement){[].forEach.call(o.parentElement.children,function(c){c.classList.remove('on')});o.classList.add('on');
      var tp=o.closest('.timepick');if(tp){tp.querySelector('.timebtn .v').textContent=o.textContent.trim();tp.querySelector('.pop').classList.remove('open');}}
    var d=e.target.closest('[data-done]');if(d){e.preventDefault();toast(d.getAttribute('data-done')||'Marked done');}
    var s=e.target.closest('.switch');if(s)s.classList.toggle('on');
    if(!e.target.closest('.pop')&&!e.target.closest('[data-pop]'))document.querySelectorAll('.pop.open').forEach(function(p){p.classList.remove('open')});
  });
  document.addEventListener('keydown',function(e){
    if(!document.body.hasAttribute('data-review')||/INPUT|TEXTAREA/.test(e.target.tagName))return;
    var k=e.key.toLowerCase();
    if(k==='d')toast('Done · next post'); else if(k==='s')toast('Skipped · back tomorrow'); else if(k==='c')toast('Reply copied'); else if(k==='o')toast('Opening thread ↗'); else if(k==='?')pop('keys');
  });
  document.addEventListener('DOMContentLoaded',function(){if(q.get('open'))pop(q.get('open'));});
})();
