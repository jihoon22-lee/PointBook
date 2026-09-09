(function () {
  'use strict';
  var form = document.getElementById('review-form');
  var table = document.getElementById('rows');
  if (!form || !table) return;
  var confirmButton = form.querySelector('.confirm-monthly');
  var feedback = document.getElementById('confirm-feedback');
  var jump = document.getElementById('confirm-error-jump');
  var errorTarget = null;
  function reveal(input) {
    if (!input) return;
    var row = input.closest('tr');
    if (row && row.hidden) {
      document.getElementById('row-filter').value = 'all';
      if (window.pointbookReviewInputs) window.pointbookReviewInputs.applyView();
    }
    var details = input.closest('details');
    if (details) details.open = true;
    input.focus();
    input.scrollIntoView({block: 'center', inline: 'center'});
  }
  function showError(message, input) {
    feedback.hidden = false;
    document.getElementById('confirm-message').textContent = message;
    errorTarget = input || null;
    jump.hidden = !errorTarget;
  }
  window.pointbookReviewFeedback = {
    show: showError,
    clearFor: function (input) {
      if (errorTarget !== input) return;
      errorTarget = null;
      jump.hidden = true;
      document.getElementById('confirm-message').textContent = '';
      feedback.hidden = !document.getElementById('confirm-errors').children.length;
    }
  };
  jump.addEventListener('click', function () { reveal(errorTarget); });
  document.getElementById('review-again').addEventListener('click', function () { form.requestSubmit(form.querySelector('[formaction="/monthly/review"]')); });
  var reportingInvalid = false;
  form.addEventListener('invalid', function (event) {
    event.preventDefault();
    if (reportingInvalid) return;
    reportingInvalid = true;
    window.setTimeout(function () { reportingInvalid = false; }, 0);
    var input = event.target;
    var row = input.closest('tr');
    var name = row && row.querySelector('[data-field="name"]');
    var label = name ? name.value + '의 이월 잔액' : (input.closest('label') || input).textContent.trim();
    showError((label || '필수 입력') + ': ' + input.validationMessage, input);
    reveal(input);
  }, true);
  form.querySelectorAll('.review-error-link').forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.getAttribute('data-error-key');
      var row = Array.prototype.find.call(table.tBodies[0].rows, function (item) { return item.getAttribute('data-row-id') === key; });
      reveal(form.elements.namedItem(key) || form.elements.namedItem('deactivated_carry_' + key) ||
        (row && (row.querySelector('[data-field="carry"]:invalid') || row.querySelector('[data-field="name"]'))));
    });
  });
  function changed() {
    document.getElementById('review-token').value = '';
    confirmButton.disabled = true;
    document.getElementById('confirm-reason').textContent = '목록이 변경되었습니다. 수정 내용 재검수를 눌러 주세요.';
    document.getElementById('review-status').textContent = '목록이 변경되었습니다. 수정 내용 재검수를 눌러 주세요.';
    var acknowledgement = form.querySelector('[name="ack_warnings"]');
    if (acknowledgement) acknowledgement.checked = false;
  }
  form.addEventListener('input', function (event) {
    var name = event.target.name || '';
    if (!name) return;
    if (name.indexOf('carry_') === 0 || name.indexOf('deactivated_carry_') === 0 || name === 'ack_warnings') return;
    changed();
  });
  form.addEventListener('change', function (event) {
    if (event.target.tagName === 'SELECT' && event.target.name) changed();
  });
  function reindex() {
    var rows = table.tBodies[0].rows;
    for (var i = 0; i < rows.length; i++) {
      var fields = rows[i].querySelectorAll('[data-field]');
      for (var j = 0; j < fields.length; j++) fields[j].name = fields[j].getAttribute('data-field') + '_' + i;
    }
  }
  function field(td, key, value, hidden) {
    var input = document.createElement(key === 'note' ? 'textarea' : 'input');
    if (key === 'note') input.rows = 1;
    else input.type = hidden ? 'hidden' : 'text';
    input.value = value || '';
    input.setAttribute('data-field', key); input.className = 'row-input';
    input.setAttribute('aria-label', key);
    if (key === 'amount' || key === 'carry') input.setAttribute('inputmode', 'numeric');
    if (key === 'carry') input.required = true;
    if (key === 'carry' || key === 'amount') td.className = 'money-' + key;
    td.appendChild(input);
  }
  document.getElementById('add-row').addEventListener('click', function () {
    var tr = document.createElement('tr');
    var id = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2);
    tr.setAttribute('data-row-id', id);
    tr.className = 'review-row-new';
    tr.setAttribute('data-link-status', 'new');
    tr.setAttribute('data-action', 'new');
    var first = tr.insertCell(); first.className = 'person-link-cell';
    field(first, 'row_id', id, true); field(first, 'link_state', 'new', true);
    field(first, 'profile_review', '', true); field(first, 'source_line', '', true); field(first, 'source_issue', '', true);
    var nameGroup = document.createElement('div'); nameGroup.className = 'person-name';
    field(nameGroup, 'name');
    var badge = document.createElement('span'); badge.className = 'badge link-state-new'; badge.textContent = '신규';
    nameGroup.appendChild(badge); first.appendChild(nameGroup);
    ['personal_no','point_no','team','grade','carry','amount','note'].forEach(function (key) { field(tr.insertCell(), key); });
    var typeCell = tr.insertCell();
    var select = document.createElement('select'); select.setAttribute('data-field', 'account_type');
    select.setAttribute('aria-label', '계정 유형');
    select.innerHTML = '<option value="person">일반</option><option value="shared">공용</option>';
    typeCell.appendChild(select);
    var button = document.createElement('button'); button.type = 'button';
    button.className = 'btn btn-sm btn-danger row-del'; button.textContent = '삭제';
    tr.insertCell().appendChild(button); table.tBodies[0].appendChild(tr); reindex(); changed();
  });
  table.addEventListener('click', function (event) {
    if (!event.target.classList.contains('row-del')) return;
    var tr = event.target.closest('tr');
    tr.parentNode.removeChild(tr); reindex(); changed();
  });
  form.addEventListener('submit', function (event) {
    var submitter = event.submitter || document.activeElement;
    if (submitter && ['/monthly/review', '/monthly/link', '/monthly/choose', '/monthly/new'].indexOf(submitter.getAttribute('formaction')) >= 0) {
      if (window.pointbookDraft) { event.preventDefault(); window.pointbookDraft.submit(submitter); }
      else if (window.pointbookReviewView) window.pointbookReviewView.capture(submitter);
      return;
    }
    if (!document.getElementById('review-token').value) {
      showError('연결·정보 선택과 입력 오류를 확인한 뒤 수정 내용 재검수를 눌러 주세요.', form.querySelector('[formaction="/monthly/review"]'));
      event.preventDefault(); return;
    }
    var acknowledgement = form.querySelector('[name="ack_warnings"]');
    if (acknowledgement && !acknowledgement.checked) {
      showError('처리 월과 누락·합계 경고를 확인한 뒤 확인란을 선택해 주세요.', acknowledgement);
      event.preventDefault(); return;
    }
    if (!window.confirm('이 내용으로 확정할까요?')) {
      event.preventDefault(); return;
    }
    if (window.pointbookDraft) { event.preventDefault(); window.pointbookDraft.submit(submitter); return; }
    confirmButton.disabled = true;
    confirmButton.textContent = '확정 중…';
  });
})();
