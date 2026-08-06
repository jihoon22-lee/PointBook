/* 대시보드 공통 헬퍼.
 * - 차트: <canvas data-chart="chart명"> + <script type="application/json" data-source="chart명"> 형태로
 *   HTML 블록만 추가하면 초기화되도록 추상화 (라이브러리 교체 시 이 파일만 수정)
 * - 개인별 표 정렬: th[data-sort] 클릭 시 해당 컬럼으로 정렬
 */
(function () {
  'use strict';

  /* ---------- 차트 ---------- */
  function initTrendChart() {
    var canvas = document.getElementById('trend-chart');
    var dataEl = document.getElementById('trend-data');
    if (!canvas || !dataEl || typeof Chart === 'undefined') return;
    var data = JSON.parse(dataEl.textContent);
    new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: data.labels,
        datasets: [
          { label: '충전', data: data.amount, borderColor: '#16a34a', backgroundColor: 'rgba(22,163,74,0.08)', tension: 0.25 },
          { label: '사용', data: data.usage, borderColor: '#dc2626', backgroundColor: 'rgba(220,38,38,0.08)', tension: 0.25 },
          { label: '잔액', data: data.balance, borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,0.08)', tension: 0.25 }
        ]
      },
      options: {
        responsive: true,
        plugins: { legend: { position: 'bottom' } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  /* ---------- 테이블 정렬 ---------- */
  function initSortableTable() {
    var table = document.getElementById('person-stats');
    if (!table) return;
    var tbody = table.tBodies[0];
    var headers = table.tHead.rows[0].cells;
    var state = { key: null, desc: false };

    function cellValue(row, key) {
      var idx = headerIndex[key];
      var text = row.cells[idx].textContent.trim().replace(/[,\s원명]/g, '');
      if (key === 'name' || key === 'team') return row.cells[idx].textContent.trim();
      var num = parseInt(text, 10);
      return isNaN(num) ? -Infinity : num;
    }

    var headerIndex = {};
    Array.prototype.forEach.call(headers, function (th, i) {
      var key = th.getAttribute('data-sort');
      if (!key) return;
      headerIndex[key] = i;
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () {
        if (state.key === key) state.desc = !state.desc;
        else { state.key = key; state.desc = false; }
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
          var va = cellValue(a, key);
          var vb = cellValue(b, key);
          if (va === vb) return 0;
          var r = va < vb ? -1 : 1;
          return state.desc ? -r : r;
        });
        rows.forEach(function (tr) { tbody.appendChild(tr); });
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initTrendChart();
    initSortableTable();
  });
})();
