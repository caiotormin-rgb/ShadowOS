import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { registerRouter } from "./router.mjs";

export default definePluginEntry({
  id: "household-router",
  name: "Household modes",
  description: "Explicit requester-scoped Grocery and Doctor modes with call-time domain restrictions.",
  register: registerRouter,
});
