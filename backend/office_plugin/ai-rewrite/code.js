(function (window) {
  // 后端改写端点。插件在用户浏览器里运行，localhost 指向用户机器上的后端。
  // ponytail: 生产/容器部署时改成可达的后端地址（可由 config 注入）
  var BACKEND = "http://localhost:8585/office/edit";

  var selText = "";
  var selEl, instEl, goEl, statusEl;

  function setStatus(s) { if (statusEl) statusEl.textContent = s || ""; }

  // 打开插件时 ONLYOFFICE 把当前选区文本作为 initData 传入（config.initDataType=text）
  window.Asc.plugin.init = function (text) {
    selText = text || "";
    selEl = document.getElementById("sel");
    instEl = document.getElementById("inst");
    goEl = document.getElementById("go");
    statusEl = document.getElementById("status");
    selEl.textContent = selText || "（未选中文本）";
    goEl.onclick = onRewrite;
  };

  function onRewrite() {
    if (!selText.trim()) { setStatus("请先在文档里选中一段文本，再打开插件"); return; }
    var instruction = (instEl.value || "").trim();
    if (!instruction) { setStatus("请输入改写指令"); return; }
    goEl.disabled = true;
    setStatus("改写中…");
    fetch(BACKEND, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: selText, instruction: instruction }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.text) throw new Error(data && data.error ? data.error : "空返回");
        // PasteText 用返回文本替换当前选区；写回后由 ONLYOFFICE 自身 forcesave→后端 callback 落盘
        window.Asc.plugin.executeMethod("PasteText", [data.text], function () {
          selText = data.text;
          selEl.textContent = selText;
          setStatus("已替换选区");
          goEl.disabled = false;
        });
      })
      .catch(function (e) {
        setStatus("失败：" + (e.message || e));
        goEl.disabled = false;
      });
  }

  window.Asc.plugin.button = function () {
    window.Asc.plugin.executeCommand("close", "");
  };
})(window);
