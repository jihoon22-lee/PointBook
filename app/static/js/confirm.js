/* 확인 문구는 data-confirm 속성에만 두고 인라인 스크립트로 보간하지 않는다. */
(function () {
  'use strict';
  document.addEventListener('submit', function (event) {
    if (event.defaultPrevented) return;
    var submitter = event.submitter;
    var message = (submitter && submitter.getAttribute('data-confirm')) ||
      event.target.getAttribute('data-confirm');
    if (message && !window.confirm(message)) event.preventDefault();
  });
})();
