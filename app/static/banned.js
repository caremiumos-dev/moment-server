// 封禁确认弹窗：把「封禁」按钮从浏览器原生 confirm 换成站内弹窗，
// 文案里带上手表端会看到的完整提示，避免管理员误操作。
(function () {
    var backdrop = document.getElementById("banned-modal");
    if (!backdrop) {
        return;
    }
    var form = null;

    function close() {
        backdrop.hidden = true;
        form = null;
    }

    document.getElementById("banned-cancel").addEventListener("click", close);
    backdrop.addEventListener("click", function (event) {
        if (event.target === backdrop) {
            close();
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !backdrop.hidden) {
            close();
        }
    });

    document.getElementById("banned-confirm").addEventListener("click", function () {
        if (form) {
            form.submit();
        }
    });

    // 所有带 data-ban-form 的封禁按钮都改走弹窗。
    document.querySelectorAll("form[data-ban-form]").forEach(function (candidate) {
        candidate.addEventListener("submit", function (event) {
            event.preventDefault();
            form = candidate;
            backdrop.hidden = false;
        });
    });
})();
