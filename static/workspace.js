let camera;
let frame;

function stopCamera() {
  if (!camera) return;
  cancelAnimationFrame(frame);
  camera.stream.getTracks().forEach(track => track.stop());
  camera.video.srcObject = null;
  camera.video.hidden = true;
  camera.button.hidden = false;
  camera.stop.hidden = true;
  camera = undefined;
}

function enableScanning() {
  if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) return;
  document.querySelectorAll("[data-scan]").forEach(button => { button.hidden = false; });
}

document.addEventListener("submit", event => {
  const message = event.submitter?.dataset.confirmButton || event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
    return;
  }
  const label = event.target.dataset.pending;
  if (!label || !event.submitter) return;
  if (event.target.getAttribute("aria-busy") === "true") {
    event.preventDefault();
    return;
  }
  event.target.setAttribute("aria-busy", "true");
  event.submitter.dataset.originalLabel = event.submitter.textContent;
  event.submitter.textContent = label;
});

document.addEventListener("click", async event => {
  if (event.target.closest("[data-scan-stop]")) {
    stopCamera();
    return;
  }
  const button = event.target.closest("[data-scan]");
  if (!button || button.disabled) return;
  const scanner = button.closest("[data-scanner]");
  const status = scanner.querySelector("[role=status]");
  const video = scanner.querySelector("video");
  const stop = scanner.querySelector("[data-scan-stop]");
  const field = scanner.parentElement.querySelector("[data-scan-field]");
  if (!field) return;
  button.disabled = true;
  try {
    const detector = new BarcodeDetector();
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    if (!scanner.isConnected) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    camera = { stream, video, button, stop };
    video.srcObject = stream;
    video.hidden = false;
    button.hidden = true;
    stop.hidden = false;
    status.textContent = scanner.dataset.ready;
    await video.play();
    const scan = async () => {
      if (camera?.video !== video) return;
      try {
        const codes = await detector.detect(video);
        if (camera?.video !== video) return;
        if (codes.length) {
          field.value = codes[0].rawValue;
          status.textContent = "";
          stopCamera();
          field.focus();
          return;
        }
        frame = requestAnimationFrame(scan);
      } catch {
        status.textContent = scanner.dataset.unavailable;
        stopCamera();
      }
    };
    frame = requestAnimationFrame(scan);
  } catch {
    stopCamera();
    status.textContent = scanner.dataset.unavailable;
  } finally {
    button.disabled = false;
  }
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll("[data-pending][aria-busy]").forEach(form => {
    form.removeAttribute("aria-busy");
    form.querySelectorAll("[data-original-label]").forEach(button => {
      button.textContent = button.dataset.originalLabel;
      delete button.dataset.originalLabel;
    });
  });
});
window.addEventListener("pagehide", stopCamera);
document.addEventListener("htmx:beforeSwap", stopCamera);
document.addEventListener("htmx:afterSwap", enableScanning);
enableScanning();

document.addEventListener("click", event => {
  const button = event.target.closest("[data-add-procurement-row]");
  if (!button) return;
  const form = button.form;
  const total = form.querySelector("[name=lines-TOTAL_FORMS]");
  const index = Number(total.value);
  if (index >= 100) return;
  const template = form.querySelector("[data-procurement-row]");
  const clone = template.content.cloneNode(true);
  clone.querySelectorAll("[name], [id], [for]").forEach(element => {
    for (const attr of ["name", "id", "for"]) {
      if (element.hasAttribute(attr)) {
        element.setAttribute(attr, element.getAttribute(attr).replaceAll("__prefix__", String(index)));
      }
    }
  });
  form.querySelector("[data-procurement-lines]").append(clone);
  total.value = String(index + 1);
  enableScanning();
});