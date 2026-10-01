import "./publishing.css";
import { advertising } from "./publisherSettings";

if (!advertising.enabled) {
  const status = document.getElementById("advertising-status");
  if (status) status.textContent = "Google advertising is currently disabled on this site.";
}
