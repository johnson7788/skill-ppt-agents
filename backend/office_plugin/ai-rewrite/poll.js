(function (window) {
  // P6.3 broker 桥·插件后台常驻端：轮询后端信箱，取走助手侧栏经 agent 投递的 op，
  // 用 callCommand（Builder API）在 live 会话里落地。这样助手侧栏无需跨 iframe 就能改文档。
  // ponytail: localhost 指向用户机器上的后端；容器/生产部署改成可达地址。
  var BACKEND = "http://localhost:8585/office/pending";
  var POLL_MS = 2000;

  function userId() {
    // 后端 /office/config 把 editorConfig.user.id 设为 user_id；插件读得到就多用户可用，
    // 读不到退回 default_user（本地单用户够用）。
    // ponytail: 多用户上线时确认 Asc.plugin.info.userId 能拿到，否则改由前端把 user_id 传进插件。
    var info = window.Asc.plugin.info || {};
    return info.userId || "default_user";
  }

  function applyOp(op) {
    // 后台没有选区上下文，只处理整页/整篇的 callCommand 类 op（背景/查找替换）。
    if (op.type !== "set_slide_background" && op.type !== "replace_text") return;
    window.Asc.scope = window.Asc.scope || {};
    window.Asc.scope.op = op;
    window.Asc.plugin.callCommand(function () {
      var op = Asc.scope.op;
      if (op.type === "set_slide_background") {
        var c = op.color;
        var r = parseInt(c.substr(1, 2), 16),
            g = parseInt(c.substr(3, 2), 16),
            b = parseInt(c.substr(5, 2), 16);
        var oSlide = Api.GetPresentation().GetSlideByIndex(op.slide);
        if (oSlide) oSlide.SetBackground(Api.CreateSolidFill(Api.CreateRGBColor(r, g, b)));
      } else if (op.type === "replace_text") {
        Api.GetDocument().SearchAndReplace({ searchString: op.find, replaceString: op.replace });
      }
    }, false, false, function () {});
  }

  function poll() {
    fetch(BACKEND + "?user_id=" + encodeURIComponent(userId()))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var ops = (data && data.ops) || [];
        for (var i = 0; i < ops.length; i++) applyOp(ops[i]);
      })
      .catch(function () { /* 后端不可达时静默重试 */ });
  }

  window.Asc.plugin.init = function () {
    setInterval(poll, POLL_MS);
  };

  window.Asc.plugin.button = function () {};
})(window);
