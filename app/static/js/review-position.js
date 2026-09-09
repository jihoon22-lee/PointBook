(function () {
  'use strict';
  var form = document.getElementById('review-form');
  var table = document.getElementById('rows');
  if (!form || !table) return;
  var wrap = table.closest('.table-wrap');
  var filter = document.getElementById('row-filter');
  var sort = document.getElementById('row-sort');
  var focusMode = document.getElementById('focus-input');
  var lastField = null;
  form.addEventListener('focusin', function (event) {
    if (event.target.hasAttribute('data-field') && event.target.type !== 'hidden') lastField = event.target;
  });
  function key() { return 'pointbook-review-view:' + form.elements.namedItem('request_key').value; }
  function findRow(id) {
    var rows = table.tBodies[0].rows;
    for (var i = 0; i < rows.length; i++) if (rows[i].getAttribute('data-row-id') === id) return rows[i];
    return null;
  }
  function capture(button) {
    var action = button && button.getAttribute('formaction');
    if (['/monthly/review', '/monthly/link', '/monthly/choose', '/monthly/new'].indexOf(action) < 0) return;
    var row = button.closest('tr') || (lastField && lastField.closest('tr'));
    if (!row) {
      var rows = table.tBodies[0].rows;
      for (var i = 0; i < rows.length; i++) {
        if (!rows[i].hidden && rows[i].getBoundingClientRect().bottom > 0) { row = rows[i]; break; }
      }
    }
    var field = lastField && lastField.closest('tr') === row ? lastField.getAttribute('data-field') : '';
    // Keep only view coordinates and field names; request contents stay on the server.
    var state = {rowId: row ? row.getAttribute('data-row-id') : '', offset: row ? row.getBoundingClientRect().top : 0,
      x: window.scrollX, y: window.scrollY, left: wrap.scrollLeft, field: field,
      filter: filter.value, sort: sort.value, focus: focusMode.checked,
      buttonName: button.name || '', buttonValue: button.value || '',
      details: row ? Array.prototype.map.call(row.querySelectorAll('details'), function (detail) { return detail.open; }) : [],
      time: Date.now()};
    try { window.sessionStorage.setItem(key(), JSON.stringify(state)); } catch (error) { /* Storage may be disabled. */ }
  }
  window.pointbookReviewView = {capture: capture};
  var state;
  try {
    var saved = window.sessionStorage.getItem(key());
    window.sessionStorage.removeItem(key());
    state = saved ? JSON.parse(saved) : null;
  } catch (error) { return; }
  if (!state || Date.now() - state.time > 300000) return;
  filter.value = state.filter;
  sort.value = state.sort;
  focusMode.checked = state.focus;
  table.classList.toggle('focus-input', state.focus);
  if (window.pointbookReviewInputs) window.pointbookReviewInputs.applyView();
  var row = findRow(state.rowId);
  if (row && row.hidden) {
    // A resolved row can stop matching its former filter; keep the action's row visible.
    filter.value = 'all';
    if (window.pointbookReviewInputs) window.pointbookReviewInputs.applyView();
  }
  if (row && state.details) {
    Array.prototype.forEach.call(row.querySelectorAll('details'), function (detail, index) {
      if (typeof state.details[index] === 'boolean') detail.open = state.details[index];
    });
  }
  function restore() {
    window.pointbookReviewView.restoring = true;
    if (row && state.field) {
      var inputs = row.querySelectorAll('[data-field]');
      for (var i = 0; i < inputs.length; i++) {
        if (inputs[i].getAttribute('data-field') === state.field && inputs[i].type !== 'hidden') {
          inputs[i].focus({preventScroll: true}); break;
        }
      }
    }
    if (row && !state.field && state.buttonName) {
      var buttons = row.querySelectorAll('button');
      for (var j = 0; j < buttons.length; j++) {
        if (buttons[j].name === state.buttonName && buttons[j].value === state.buttonValue && !buttons[j].disabled) {
          buttons[j].focus({preventScroll: true}); break;
        }
      }
    }
    wrap.scrollLeft = state.left;
    var y = row ? window.scrollY + row.getBoundingClientRect().top - state.offset : state.y;
    window.scrollTo(state.x, y);
    window.pointbookReviewView.restoring = false;
  }
  window.requestAnimationFrame(restore);
})();
