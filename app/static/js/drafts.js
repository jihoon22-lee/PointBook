(function () {
  'use strict';
  var form = document.getElementById('review-form');
  if (!form || !window.fetch) return;
  var status = document.getElementById('draft-status');
  var login = document.getElementById('draft-login');
  var timer = null, pending = null, dirty = false, paused = false, composing = false;
  var submitting = false;
  function field(name) { return form.elements.namedItem(name); }
  function canonicalURL() {
    if (field('draft_id').value && window.history.replaceState) window.history.replaceState(null, '', '/drafts/' + encodeURIComponent(field('draft_id').value));
  }
  canonicalURL();
  function message(text, failed) {
    status.textContent = text;
    status.className = failed ? 'alert alert-error' : 'muted';
  }
  function save(fork) {
    if (pending) return pending.then(function () { return save(fork); });
    window.clearTimeout(timer);
    var data = new FormData(form);
    if (fork) data.set('fork', 'yes');
    dirty = false;
    message('서버 초안 저장 중…', false);
    pending = fetch('/drafts/save', {method: 'POST', body: data, credentials: 'same-origin', headers: {'X-CSRF-Token': field('csrf_token').value}})
      .then(function (response) {
        if (response.redirected || response.status === 401 || response.status === 403) {
          login.hidden = false;
          paused = true;
          throw new Error('로그인이 만료되었습니다. 이 화면을 유지한 채 새 탭에서 로그인하고 지금 저장을 누르세요.');
        }
        return response.json().then(function (value) {
          if (!response.ok) {
            if (response.status === 409 || response.status === 410) paused = true;
            throw new Error(value.error || '초안을 저장하지 못했습니다.');
          }
          ['draft_id', 'draft_version', 'request_key'].forEach(function (name) { field(name).value = value[name]; });
          canonicalURL();
          login.hidden = true;
          message('서버 초안 저장 완료 · ' + new Date(value.draft_saved_at).toLocaleTimeString() + (dirty ? ' · 추가 입력 저장 대기' : ''), false);
          return value;
        });
      }).catch(function (error) {
        dirty = true;
        paused = true;
        message(error.message || '연결이 끊겨 저장하지 못했습니다. 입력은 이 화면에 남아 있습니다.', true);
        throw error;
      }).finally(function () {
        pending = null;
        if (dirty && !paused && !submitting) schedule();
      });
    return pending;
  }
  function schedule() {
    window.clearTimeout(timer);
    if (!paused && !composing && !submitting) timer = window.setTimeout(function () { save(false).catch(function () {}); }, 800);
  }
  function edited(event) {
    if (!event.target.name || event.target.type === 'hidden') return;
    dirty = true;
    if (!paused) message('입력 변경 · 저장 대기', false);
    schedule();
  }
  form.addEventListener('input', edited);
  form.addEventListener('change', edited);
  form.addEventListener('compositionstart', function () { composing = true; window.clearTimeout(timer); });
  form.addEventListener('compositionend', function () { composing = false; dirty = true; schedule(); });
  document.getElementById('rows').addEventListener('click', function (event) {
    if (event.target.classList.contains('row-del')) { dirty = true; schedule(); }
  });
  document.getElementById('add-row').addEventListener('click', function () { dirty = true; schedule(); });
  function manual(fork) {
    if (fork && !window.confirm('현재 화면의 입력으로 별도 초안을 만들까요? 기존 서버 초안은 보존됩니다.')) return;
    fetch('/drafts/session', {credentials: 'same-origin', cache: 'no-store'}).then(function (response) {
      if (response.redirected || !response.ok) throw new Error('새 탭에서 다시 로그인한 뒤 저장하세요.');
      return response.json();
    }).then(function (value) {
      field('csrf_token').value = value.csrf_token;
      paused = false;
      return save(fork);
    }).catch(function (error) { paused = true; login.hidden = false; message(error.message, true); });
  }
  document.getElementById('draft-save').addEventListener('click', function () { manual(false); });
  document.getElementById('draft-fork').addEventListener('click', function () { manual(true); });
  window.addEventListener('beforeunload', function (event) {
    if (!submitting && (dirty || pending)) { event.preventDefault(); event.returnValue = ''; }
  });
  window.pointbookDraft = {
    submit: function (button) {
      if (submitting) return;
      if (paused) { message('저장 충돌이나 로그인 만료를 해결한 뒤 진행하세요. 입력은 보존되어 있습니다.', true); return; }
      submitting = true;
      window.clearTimeout(timer);
      Promise.resolve(pending).then(function () { return save(false); }).then(function () {
        form.action = button && button.getAttribute('formaction') || '/monthly/confirm';
        var submitButtons = form.querySelectorAll('button[type="submit"]');
        for (var i = 0; i < submitButtons.length; i++) submitButtons[i].disabled = true;
        form.submit();
      }).catch(function () {
        submitting = false;
        var confirmButton = form.querySelector('.confirm-monthly');
        confirmButton.disabled = !field('review_token').value;
        confirmButton.textContent = '확정 · 동기화';
      });
    }
  };
})();
