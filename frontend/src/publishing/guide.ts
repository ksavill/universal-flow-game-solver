import "./publishing.css";
import { advertising } from "./publisherSettings";

declare global {
  interface Window {
    adsbygoogle?: { push: (value: Record<string, never>) => unknown };
    googlefc?: { callbackQueue?: Array<{ CONSENT_API_READY: () => void }>; showRevocationMessage?: () => void };
  }
}

const aside = document.querySelector<HTMLElement>(".ad-space");
const slot = document.getElementById("guide-ad");
const intro = document.getElementById("ad-intro");
const status = document.getElementById("ad-status");
const privacy = document.getElementById("privacy-choices");
const disable = document.getElementById("disable-ad");
const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const production = location.hostname === "flowpuzzlesolver.net";
let started = false;

// Deliberately do not remember this opt-in: no Google requests or automatic
// consent overlays on a fresh page visit, and no requests at all on the solver.
if (advertising.enabled && production && aside) aside.hidden = false;
document.getElementById("enable-ad")?.addEventListener("click", () => {
  if (started || !advertising.enabled || !production || !slot || !intro || !status || !disable) return;
  started = true;
  intro.hidden = true;
  disable.hidden = false;
  status.textContent = "Loading optional advertising. You can keep reading.";
  const ad = document.createElement("ins");
  ad.className = "adsbygoogle";
  ad.style.cssText = "display:inline-block;width:300px;height:250px";
  ad.dataset.adClient = advertising.publisher;
  ad.dataset.adSlot = advertising.guideSlot;
  slot.append(ad);
  const script = document.createElement("script");
  script.async = true;
  script.crossOrigin = "anonymous";
  script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${advertising.publisher}`;
  script.onerror = () => { status.textContent = "Advertising is unavailable. All guides and puzzles remain free to use."; };
  script.onload = () => {
    // Google's published Privacy & messaging configuration is delivered by the
    // AdSense tag and handles regional consent before serving eligible ads.
    try {
      const queue = window.adsbygoogle || ([] as Array<Record<string, never>>);
      window.adsbygoogle = queue;
      queue.push({});
      status.textContent = "Ads depend on availability and your privacy choices.";
    } catch {
      status.textContent = "Advertising is unavailable. You can continue reading.";
    }
  };
  window.googlefc = window.googlefc || {};
  window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];
  window.googlefc.callbackQueue.push({ CONSENT_API_READY: () => {
    if (privacy && window.googlefc?.showRevocationMessage) privacy.hidden = false;
  } });
  document.head.append(script);
});
privacy?.addEventListener("click", () => window.googlefc?.showRevocationMessage?.());
// A fresh document unloads all ad frames and scripts; it does not erase consent.
disable?.addEventListener("click", () => location.reload());

// A local-only layout preview never requests or impersonates a real ad.
if (local && new URLSearchParams(location.search).get("previewAd") === "1") {
  if (aside && slot) {
    aside.hidden = false;
    if (intro) intro.hidden = true;
    slot.className = "ad-preview";
    slot.textContent = "Placement preview · 300 × 250";
  }
}
