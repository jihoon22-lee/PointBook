(function () {
  'use strict';
  var form = document.getElementById('review-form');
  var table = document.getElementById('rows');
  if (!form || !table) return;
  var filter = document.getElementById('row-filter');
  var sort = document.getElementById('row-sort');
  var counter = table.tBodies[0].rows.length;
  function rows() { return Array.prototype.slice.call(table.tBodies[0].rows); }
  function value(row, name) {
    var input = row.querySelector('[data-field="' + name + '"]');
    return input ? input.value : '';
  }
  function carries() { return Array.prototype.slice.call(form.querySelectorAll('input[data-field="carry"],input[name^="deactivated_carry_"]:not([type="hidden"])')); }
  function summary() {
    var all = rows();
    var missing = carries().filter(function (input) { return !input.value.trim(); }).length;
    var visible = all.filter(function (row) { return !row.hidden; }).length;
    document.getElementById('input-summary').textContent = '전체 ' + all.length + '행 · 표시 ' + visible + '행 · 전체 처리 대상 잔액 미입력 ' + missing + '명';
  }
  function applyView() {
    var all = rows();
    all.forEach(function (row) {
      if (!row.hasAttribute('data-original-order')) row.setAttribute('data-original-order', counter++);
      var mode = filter.value;
      if (mode === 'missing') row.hidden = !!value(row, 'carry').trim();
      else if (mode === 'errors') row.hidden = row.getAttribute('data-has-error') !== 'yes';
      else if (mode === 'team_changed' || mode === 'profile_changed') row.hidden = row.getAttribute('data-' + mode.replace('_','-')) !== 'yes';
      else row.hidden = mode !== 'all' && row.getAttribute('data-action') !== mode;
    });
    all.sort(function (left, right) {
      if (sort.value === 'original') return Number(left.getAttribute('data-original-order')) - Number(right.getAttribute('data-original-order'));
      if (sort.value === 'amount') {
        var a = value(left, 'amount').replace(/,/g, ''), b = value(right, 'amount').replace(/,/g, '');
        if (/^[0-9]+$/.test(a) && /^[0-9]+$/.test(b)) return Number(a) - Number(b);
      }
      return value(left, sort.value).localeCompare(value(right, sort.value), 'ko');
    });
    all.forEach(function (row) { table.tBodies[0].appendChild(row); });
    summary();
  }
  document.getElementById('focus-input').addEventListener('change', function (event) { table.classList.toggle('focus-input', event.target.checked); });
  filter.addEventListener('change', applyView);
  sort.addEventListener('change', applyView);
  form.addEventListener('input', summary);
  function keepFieldVisible(input) {
    if (!input || !input.closest('#rows') || input.closest('td').cellIndex === 0) return;
    var wrap = table.closest('.table-wrap');
    var bounds = wrap.getBoundingClientRect();
    var fixedWidth = table.tHead.rows[0].cells[0].getBoundingClientRect().width;
    var box = input.getBoundingClientRect();
    var left = bounds.left + fixedWidth + 4, right = bounds.left + wrap.clientWidth - 4;
    if (box.width > right - left) return;
    if (box.left < left) wrap.scrollLeft -= left - box.left;
    else if (box.right > right) wrap.scrollLeft += box.right - right;
  }
  form.addEventListener('focusin', function (event) {
    if (window.pointbookReviewView && window.pointbookReviewView.restoring) return;
    window.requestAnimationFrame(function () { keepFieldVisible(event.target); });
  });
  form.addEventListener('invalid', function (event) {
    var row = event.target.closest('tr');
    if (row && row.hidden) { filter.value = 'all'; applyView(); }
  }, true);
  form.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
    var inputs = carries().filter(function (input) { var row = input.closest('tr'); return !row || !row.hidden; });
    var index = inputs.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    if (index + 1 < inputs.length) { inputs[index + 1].focus(); inputs[index + 1].select(); }
  });
  document.getElementById('next-missing').addEventListener('click', function () {
    var missing = carries().filter(function (input) { return !input.value.trim(); });
    if (!missing.length) { summary(); return; }
    filter.value = 'all'; applyView();
    missing[0].focus(); missing[0].scrollIntoView({block: 'center', inline: 'center'});
  });
  document.getElementById('add-row').addEventListener('click', applyView);
  table.addEventListener('click', function (event) { if (event.target.classList.contains('row-del')) summary(); });
  window.pointbookReviewInputs = {applyView: applyView, keepFieldVisible: keepFieldVisible};
  summary();
})();
