(function () {
  'use strict';
  var form = document.getElementById('review-form');
  var table = document.getElementById('rows');
  if (!form || !table) return;
  var filter = document.getElementById('row-filter');
  var sort = document.getElementById('row-sort');
  var counter = table.tBodies[0].rows.length;
  var lastCarry = null;
  function rows() { return Array.prototype.slice.call(table.tBodies[0].rows); }
  function value(row, name) {
    var input = row.querySelector('[data-field="' + name + '"]');
    return input ? input.value : '';
  }
  function carries() { return Array.prototype.slice.call(form.querySelectorAll('input[data-field="carry"],input[name^="deactivated_carry_"]:not([type="hidden"])')); }
  var maxCarry = Number(form.getAttribute('data-carry-max'));
  function money(value) { return value.toLocaleString('ko-KR'); }
  function readCarry(input) {
    var raw = input.value.trim();
    if (!raw) return {state: 'missing', message: ''};
    var cleaned = raw.replace(/^₩/, '').replace(/원$/, '');
    if (!/^(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$/.test(cleaned)) {
      return {state: 'error', message: '이월 잔액: 0 이상의 정수 원화 금액을 입력해 주세요.'};
    }
    var amount = Number(cleaned.replace(/,/g, ''));
    if (!Number.isSafeInteger(amount) || amount > maxCarry) {
      return {state: 'error', message: '이월 잔액: 0원 이상 ' + money(maxCarry) + '원 이하로 입력해 주세요.'};
    }
    var previous = input.getAttribute('data-previous-total');
    if (input.getAttribute('data-limit-previous') === 'yes' && previous !== null && previous !== '' && amount > Number(previous)) {
      return {state: 'error', message: '이월 잔액이 직전 총 잔액보다 ' + money(amount - Number(previous)) + '원 많습니다. 직전 총 잔액 ' + money(Number(previous)) + '원 / 입력 금액 ' + money(amount) + '원. 원본 금액과 이전 장부를 확인해 주세요.'};
    }
    return {state: 'complete', message: ''};
  }
  function refreshCarry(input) {
    var control = input.closest('.carry-control');
    if (!control) {
      control = document.createElement('div'); control.className = 'carry-control';
      input.parentNode.insertBefore(control, input); control.appendChild(input);
      var help = document.createElement('div'); help.className = 'carry-help';
      help.id = 'carry-help-row-' + input.closest('tr').getAttribute('data-row-id');
      help.innerHTML = '<small class="carry-reference">이전 기록 없음</small><button type="button" class="btn btn-sm use-previous-carry" disabled>직전 잔액 사용</button><small class="carry-state"></small><small class="carry-error" hidden></small>';
      control.appendChild(help); input.setAttribute('aria-describedby', help.id);
      input.placeholder = '이월 잔액 입력';
    }
    var result = readCarry(input);
    input.setAttribute('data-carry-state', result.state);
    input.setAttribute('aria-invalid', result.state === 'error' ? 'true' : 'false');
    input.setCustomValidity(result.message);
    var state = control.querySelector('.carry-state');
    state.setAttribute('data-state', result.state);
    state.textContent = {missing: '미입력', complete: '입력 완료', error: '금액 확인 필요'}[result.state];
    var error = control.querySelector('.carry-error');
    error.textContent = result.message; error.hidden = !result.message;
    return result;
  }
  function summary() {
    var all = rows();
    var counts = {missing: 0, complete: 0, error: 0};
    carries().forEach(function (input) { counts[input.getAttribute('data-carry-state')]++; });
    var visible = all.filter(function (row) { return !row.hidden; }).length;
    var reviewed = !!document.getElementById('review-token').value;
    document.getElementById('input-summary').textContent = '전체 ' + all.length + '행 · 표시 ' + visible + '행 · 입력 완료 ' + counts.complete + '명 · 미입력 ' + counts.missing + '명 · 금액 확인 필요 ' + counts.error + '명' + (reviewed ? '' : ' · 전체 처리 대상은 연결·검수 후 확인');
    var reason = document.getElementById('carry-confirm-reason');
    reason.hidden = !counts.error;
    reason.textContent = '이월 잔액 오류 ' + counts.error + '건을 수정해야 확정할 수 있습니다.';
    form.querySelector('.confirm-monthly').disabled = !reviewed || counts.error > 0;
  }
  function applyView() {
    var all = rows();
    all.forEach(function (row) {
      if (!row.hasAttribute('data-original-order')) row.setAttribute('data-original-order', counter++);
      var mode = filter.value;
      if (mode === 'missing') row.hidden = !!value(row, 'carry').trim();
      else if (mode === 'carry-error') row.hidden = row.querySelector('[data-field="carry"]').getAttribute('data-carry-state') !== 'error';
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
    form.querySelectorAll('.balance-item.carry-control').forEach(function (control) {
      var state = control.querySelector('input:not([type="hidden"])').getAttribute('data-carry-state');
      control.hidden = filter.value === 'missing' ? state !== 'missing' : filter.value === 'carry-error' && state !== 'error';
    });
    summary();
  }
  document.getElementById('focus-input').addEventListener('change', function (event) { table.classList.toggle('focus-input', event.target.checked); });
  filter.addEventListener('change', applyView);
  sort.addEventListener('change', applyView);
  form.addEventListener('input', function (event) {
    var input = event.target;
    if (input.matches('input[data-field="carry"],input[name^="deactivated_carry_"]:not([type="hidden"])')) {
      refreshCarry(input);
    } else if (['name', 'personal_no', 'point_no', 'account_type'].indexOf(input.getAttribute('data-field')) >= 0 || input.name === 'month') {
      // 기준 인원·월을 바꾼 뒤 이전 화면의 참고 금액을 복사하지 않는다.
      var targetRow = input.closest('tr');
      var affected = targetRow ? [targetRow.querySelector('[data-field="carry"]')] : carries();
      affected.forEach(function (carry) {
        if (!carry) return;
        var control = carry.closest('.carry-control');
        carry.removeAttribute('data-previous-total');
        control.querySelector('.use-previous-carry').disabled = true;
        control.querySelector('.carry-reference').textContent = '인원·월 변경: 재검수 후 확인';
        refreshCarry(carry);
      });
    }
    summary();
  });
  form.addEventListener('change', summary);
  form.addEventListener('click', function (event) {
    var button = event.target.closest('.use-previous-carry');
    if (!button || button.disabled) return;
    var input = button.closest('.carry-control').querySelector('input:not([type="hidden"])');
    var previous = input.getAttribute('data-previous-total');
    if (previous === null || previous === '') return;
    input.value = money(Number(previous));
    // 직접 입력과 같은 자동 저장·검증 이벤트를 사용한다.
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.focus();
  });
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
    if (event.target.matches('input[data-field="carry"],input[name^="deactivated_carry_"]:not([type="hidden"])')) lastCarry = event.target;
    if (window.pointbookReviewView && window.pointbookReviewView.restoring) return;
    window.requestAnimationFrame(function () { keepFieldVisible(event.target); });
  });
  form.addEventListener('invalid', function (event) {
    var row = event.target.closest('tr');
    if (row && row.hidden) { filter.value = 'all'; applyView(); }
  }, true);
  form.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
    var inputs = carries().filter(function (input) { return !input.closest('[hidden]'); });
    var index = inputs.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    if (index + 1 < inputs.length) { inputs[index + 1].focus(); inputs[index + 1].select(); }
  });
  function nextState(state) {
    var candidates = carries().filter(function (input) { return input.getAttribute('data-carry-state') === state; });
    if (!candidates.length) { summary(); return; }
    var index = candidates.indexOf(lastCarry);
    var target = candidates[(index + 1) % candidates.length];
    filter.value = 'all'; applyView();
    target.focus(); target.scrollIntoView({block: 'center', inline: 'center'});
  }
  document.getElementById('next-missing').addEventListener('click', function () { nextState('missing'); });
  document.getElementById('next-carry-error').addEventListener('click', function () { nextState('error'); });
  document.getElementById('add-row').addEventListener('click', function () {
    carries().filter(function (input) { return !input.hasAttribute('data-carry-state'); }).forEach(refreshCarry);
    applyView();
  });
  table.addEventListener('click', function (event) { if (event.target.classList.contains('row-del')) summary(); });
  window.pointbookReviewInputs = {applyView: applyView, keepFieldVisible: keepFieldVisible, summary: summary};
  carries().forEach(refreshCarry);
  summary();
})();
