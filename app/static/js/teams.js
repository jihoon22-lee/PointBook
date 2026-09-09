/* 팀 색상 선택이 끝나면 저장하고 실패 시 같은 값을 다시 선택할 수 있게 한다. */
(function () {
  "use strict";
  document.querySelectorAll(".team-color-form").forEach(function (form) {
    var input = form.querySelector("input[type=color]");
    var code = form.querySelector("code");
    var status = form.querySelector(".team-color-status");
    var saved = input.value;
    input.addEventListener("change", async function () {
      var selected = input.value;
      var data = new FormData(form);
      input.disabled = true;
      status.className = "team-color-status muted";
      status.textContent = "저장 중…";
      try {
        var response = await fetch(form.action, {
          method: "POST", body: data, headers: {Accept: "application/json"}
        });
        if (!response.ok || response.redirected) throw new Error("save failed");
        var result = await response.json();
        if (result.color !== selected) throw new Error("unexpected response");
        saved = result.color;
        code.textContent = saved;
        status.textContent = "저장됨";
      } catch (error) {
        input.value = saved;
        status.className = "team-color-status alert-error";
        status.textContent = "저장하지 못했습니다. 색상을 다시 선택해 주세요.";
      } finally {
        input.disabled = false;
      }
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); });
  });
}());
