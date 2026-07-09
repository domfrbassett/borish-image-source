export function downloadJson(filename, object) {
  const blob = new Blob([JSON.stringify(object, null, 2)], { type: "application/json" });
  downloadBlob(filename, blob);
}

export function downloadText(filename, text, mime = "text/plain") {
  downloadBlob(filename, new Blob([text], { type: mime }));
}

export function downloadBase64(filename, base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  downloadBlob(filename, new Blob([bytes], { type: mime }));
}

export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
