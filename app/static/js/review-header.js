(function () {
  'use strict';
  var table = document.getElementById('rows');
  if (!table) return;
  var wrap = table.closest('.table-wrap');
  var topbar = document.querySelector('.topbar');
  var floating = document.createElement('div');
  floating.id = 'review-floating-header';
  floating.hidden = true;
  floating.setAttribute('aria-hidden', 'true');
  var copy = document.createElement('table');
  copy.className = table.className;
  copy.appendChild(table.tHead.cloneNode(true));
  floating.appendChild(copy);
  document.body.appendChild(floating);
  var pending = false;
  function update() {
    pending = false;
    var top = topbar ? Math.max(0, topbar.getBoundingClientRect().bottom) : 0;
    var box = table.getBoundingClientRect(), bounds = wrap.getBoundingClientRect();
    var height = table.tHead.getBoundingClientRect().height;
    floating.hidden = box.top >= top || box.bottom <= top;
    if (floating.hidden) return;
    floating.style.top = Math.min(top, box.bottom - height) + 'px';
    floating.style.left = bounds.left + 'px';
    floating.style.width = wrap.clientWidth + 'px';
    copy.className = table.className;
    copy.style.width = box.width + 'px';
    var source = table.tHead.rows[0].cells, target = copy.tHead.rows[0].cells;
    for (var i = 0; i < source.length; i++) target[i].style.width = source[i].getBoundingClientRect().width + 'px';
    floating.scrollLeft = wrap.scrollLeft;
  }
  function schedule() {
    if (!pending) { pending = true; window.requestAnimationFrame(update); }
  }
  window.addEventListener('scroll', schedule, {passive: true});
  window.addEventListener('resize', schedule);
  wrap.addEventListener('scroll', schedule, {passive: true});
  document.getElementById('review-form').addEventListener('change', schedule);
  new ResizeObserver(schedule).observe(table);
  if (topbar) new ResizeObserver(schedule).observe(topbar);
  schedule();
})();
