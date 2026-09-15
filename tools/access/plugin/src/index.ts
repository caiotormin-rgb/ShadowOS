import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { AccessClient } from "./client.js";
import { accessCommand, meCommand } from "./commands.js";
import { enforce, monitor } from "./policy.js";
import { DEFAULT_DB_PATH, initStore } from "./store.js";

export { AccessClient } from "./client.js";

export default definePluginEntry({
  id: "access",
  name: "Household Access",
  description: "People, identities and grants for household tools. P1: monitor-mode tool policy, /access who, /me.",
  register(api) {
    const config = (api.pluginConfig ?? {}) as { dbPath?: string; enforceAgents?: string[] };
    const dbPath = config.dbPath ?? DEFAULT_DB_PATH;
    try {
      initStore(dbPath);
    } catch (error) {
      // Not fatal: every check below then denies (and in monitor mode, logs).
      api.logger.warn(`access: store init failed at ${dbPath}: ${String(error)}`);
    }
    const client = new AccessClient(dbPath);

    api.registerTrustedToolPolicy({
      id: "access-monitor",
      description: "Household grants: enforce selected agents, monitor other surfaces.",
      evaluate(event, ctx) {
        if (ctx.agentId && config.enforceAgents?.includes(ctx.agentId)) {
          return enforce(client, api.logger, event.toolName, ctx.agentId, ctx.requester, event.params);
        }
        monitor(client, api.logger, event.toolName, ctx.agentId, ctx.requester);
        return undefined;
      },
    });

    api.registerCommand({
      name: "access",
      description: "Household access admin (owner only).",
      acceptsArgs: true,
      requireAuth: true,
      // Host 2026.9.4 passes ctx.senderIsOwner to an installed plugin's handler
      // only when the command declares requiredScopes; on chat surfaces the
      // scope is then satisfied only by a sender the host resolves as owner.
      requiredScopes: ["operator.admin"],
      handler: async (ctx) => accessCommand(client, ctx),
    });

    api.registerCommand({
      name: "me",
      description: "Show your own household access.",
      acceptsArgs: false,
      requireAuth: true,
      handler: async (ctx) => meCommand(client, ctx),
    });
  },
});
