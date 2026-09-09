(function () {
  'use strict';
  var selector = 'input[inputmode="numeric"]';
  function isMoney(input) {
    return input.matches && input.matches(selector) &&
      /^(amount(?:_\d+)?|carry(?:_\d+)?|carry_balance|total|expected_amount|deactivated_carry_\d+)$/.test(input.name);
  }
  function valid(text) { return /^(?:₩)?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:원)?$/.test(text.trim()); }
  function format(input, editing) {
    var raw = input.value;
    if (!raw || (!valid(raw) && !(editing && /^[0-9,]+$/.test(raw)))) return;
    var start = input.selectionStart, end = input.selectionEnd;
    var digits = raw.replace(/[^0-9]/g, '');
    if (!digits) return;
    var formatted = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    if (formatted === raw) return;
    function position(old) {
      var count = raw.slice(0, old).replace(/[^0-9]/g, '').length;
      if (!count) return 0;
      for (var i = 0; i < formatted.length; i++) {
        if (/[0-9]/.test(formatted[i]) && --count === 0) return i + 1;
      }
      return formatted.length;
    }
    input.value = formatted;
    if (document.activeElement === input && start !== null) input.setSelectionRange(position(start), position(end));
  }
  document.querySelectorAll(selector).forEach(function (input) { if (isMoney(input)) format(input, false); });
  // Invalid pasted punctuation must reach server validation unchanged.
  document.addEventListener('paste', function (event) {
    if (isMoney(event.target)) event.target.moneyInvalidPaste = !valid(event.clipboardData.getData('text'));
  }, true);
  document.addEventListener('input', function (event) {
    var input = event.target;
    if (!isMoney(input) || event.isComposing) return;
    if (input.moneyInvalidPaste) { input.moneyInvalidPaste = false; return; }
    format(input, true);
  }, true);
  document.addEventListener('compositionend', function (event) {
    if (isMoney(event.target)) format(event.target, false);
  }, true);
  document.addEventListener('beforeinput', function (event) {
    var input = event.target;
    if (!isMoney(input) || event.isComposing || input.selectionStart !== input.selectionEnd) return;
    var caret = input.selectionStart, raw = input.value;
    if (event.inputType === 'deleteContentBackward' && raw[caret - 1] === ',' && caret > 1) {
      event.preventDefault();
      input.setRangeText('', caret - 2, caret, 'end');
    } else if (event.inputType === 'deleteContentForward' && raw[caret] === ',' && caret + 1 < raw.length) {
      event.preventDefault();
      input.setRangeText('', caret, caret + 2, 'end');
    } else return;
    input.dispatchEvent(new Event('input', {bubbles: true}));
  });
})();
