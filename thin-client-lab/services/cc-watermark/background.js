// Asks the local helper (native messaging host com.cc.watermark) who is signed in
// on this machine. Chrome starts the helper as the desktop's own user, so the name
// cannot be set by a web page or by the user.
"use strict";
let who = null;

async function whoami() {
  if (who) return who;
  try {
    who = await chrome.runtime.sendNativeMessage("com.cc.watermark", { q: "whoami" });
  } catch (e) {
    who = { user: "unknown user", host: "", error: String(e) };
  }
  return who;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.q === "whoami") {
    whoami().then(reply);
    return true;          // reply asynchronously
  }
});
