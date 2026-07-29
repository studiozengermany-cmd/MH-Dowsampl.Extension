(function () {
  "use strict";

  const api = "http://127.0.0.1:8765";
  const askEachTime = document.querySelector("#ask-each-time");
  const changeFolder = document.querySelector("#btn-change-folder");
  const serverTextElement = document.querySelector("#server-text");
  const messageElement = document.querySelector("#message");

  if (!askEachTime || !changeFolder) {
    return;
  }

  function message(text, kind) {
    if (!messageElement) {
      return;
    }
    messageElement.textContent = text || "";
    messageElement.className = "message" + (kind ? " message--" + kind : "");
  }

  async function settingsRequest(path, body) {
    const response = await fetch(api + path, {
      method: body ? "POST" : "GET",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
    const payload = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      throw new Error(payload.error || "Không cập nhật được cài đặt nơi lưu");
    }
    return payload;
  }

  function renderSettings(payload) {
    askEachTime.checked = payload.ask_each_time === true;
    const folder = payload.download_root || "Chưa chọn thư mục lưu";
    if (serverTextElement) {
      const current = serverTextElement.textContent || "Server";
      const prefix = payload.version
        ? "Server " + payload.version
        : (current.includes(" · ") ? current.split(" · ")[0] : current);
      serverTextElement.textContent = prefix + " · " + folder;
    }
  }

  askEachTime.addEventListener("change", async function () {
    const requested = askEachTime.checked;
    askEachTime.disabled = true;
    try {
      const payload = await settingsRequest("/settings/download-root", {
        ask_each_time: requested
      });
      renderSettings(payload);
      message(requested ? "Đã bật hỏi nơi lưu riêng cho từng file." : "Đã dùng thư mục lưu mặc định.", "success");
    } catch (error) {
      askEachTime.checked = !requested;
      message(error.message, "error");
    } finally {
      askEachTime.disabled = false;
    }
  });

  changeFolder.addEventListener("click", async function () {
    changeFolder.disabled = true;
    try {
      const payload = await settingsRequest("/settings/download-root", { select: true });
      renderSettings(payload);
      message("Đã cập nhật thư mục lưu mặc định.", "success");
    } catch (error) {
      message(error.message, "error");
    } finally {
      changeFolder.disabled = false;
    }
  });

  settingsRequest("/settings")
    .then(renderSettings)
    .catch(function () {
      // popup-core.js already owns the primary offline state.
    });
})();
