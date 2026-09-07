(function () {
  'use strict';
  var form = document.getElementById('monthly-upload');
  if (!form) return;
  form.addEventListener('submit', function () {
    var button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = '검수 목록 준비 중…';
    document.getElementById('upload-status').textContent = '처리 중입니다. 사진 인식에는 시간이 걸릴 수 있습니다.';
  });
})();
