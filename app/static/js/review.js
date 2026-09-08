(function () {
  'use strict';
  var form = document.getElementById('review-form');
  var table = document.getElementById('rows');
  if (!form || !table) return;
  var confirmButton = form.querySelector('.confirm-monthly');
  function changed() {
    document.getElementById('review-token').value = '';
    confirmButton.disabled = true;
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
    ['personal_no','point_no','team','grade','amount','carry','note'].forEach(function (key) { field(tr.insertCell(), key); });
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
    if (!document.getElementById('review-token').value || !window.confirm('이 내용으로 확정할까요?')) {
      event.preventDefault(); return;
    }
    if (window.pointbookDraft) { event.preventDefault(); window.pointbookDraft.submit(submitter); return; }
    confirmButton.disabled = true;
    confirmButton.textContent = '확정 중…';
  });
})();
