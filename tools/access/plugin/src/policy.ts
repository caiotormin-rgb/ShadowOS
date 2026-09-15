import { AccessClient, StoreUnavailable } from "./client.js";

/** Tools the policy knows. Anything else is unmapped, which is a deny for non-owners. */
export const TOOL_GRANTS: Record<string, { resource: string; action: string }> = {
  grocery_list: { resource: "grocery", action: "use" },
  grocery_show: { resource: "grocery", action: "use" },
  grocery_add: { resource: "grocery", action: "use" },
  grocery_mark: { resource: "grocery", action: "use" },
  grocery_remove: { resource: "grocery", action: "use" },
  grocery_trip: { resource: "grocery", action: "use" },
  grocery_activity: { resource: "grocery", action: "use" },
  grocery_preferences: { resource: "grocery", action: "use" },

  doctor_search: { resource: "doctor", action: "request" },
};

/** Decode only the host's household capability IDs; never trust arbitrary IDs. */
export function householdBrokerTarget(params: unknown): string | undefined {
  if (!params || typeof params !== "object" || Array.isArray(params)) return undefined;
  const id = (params as { id?: unknown }).id;
  if (typeof id !== "string") return undefined;
  const match = /^openclaw:(grocery-list-tool|doctor-search-tool):([a-z][a-z0-9_]*)$/.exec(id);
  if (!match || match[0] !== id) return undefined;
  const name = match[2];
  if (!Object.hasOwn(TOOL_GRANTS, name)) return undefined;
  return match[1] === (name === "doctor_search" ? "doctor-search-tool" : "grocery-list-tool") ? name : undefined;
}

export type Requester = {
  channel?: string;
  accountId?: string;
  senderId?: string;
  senderIsOwner?: boolean;
};

export type Decision = { allow: boolean; reason: string; personId?: number };

/** E4 decision, fail closed. Missing requester fields are unproven, never assumed. */
export function decide(
  client: AccessClient,
  toolName: string,
  agentId: string | undefined,
  requester: Requester | undefined,
  params?: unknown,
): Decision {
  if (!requester?.channel || !requester.senderId) return { allow: false, reason: "no_requester" };
  let principal;
  try {
    principal = client.resolvePrincipal(requester.channel, requester.accountId, requester.senderId);
  } catch (error) {
    if (error instanceof StoreUnavailable) return { allow: false, reason: "store_unreadable" };
    throw error;
  }
  if (!principal) return { allow: false, reason: "unknown_sender" };
  const personId = principal.personId;
  const isOwner = principal.role === "owner" && requester.senderIsOwner === true;
  if (isOwner) return { allow: true, reason: "owner", personId };
  if (agentId === "main") return { allow: false, reason: "main_requires_owner", personId };
  // Discovery exposes the host-filtered catalog, not permission to execute it.
  // Broker describe/call checks the requested household grant here and again
  // when the host invokes the wrapped canonical tool.
  if (toolName === "tool_search") {
    const available = client.can(personId, "grocery", "use") || client.can(personId, "doctor", "request");
    return { allow: available, reason: available ? "discovery" : "no_grant", personId };
  }
  const broker = toolName === "tool_call" || toolName === "tool_describe";
  const effectiveName = broker ? householdBrokerTarget(params) : toolName;
  const mapped = effectiveName ? TOOL_GRANTS[effectiveName] : undefined;
  if (!mapped) return { allow: false, reason: "unmapped_tool", personId };
  if (!client.can(personId, mapped.resource, mapped.action)) return { allow: false, reason: "no_grant", personId };
  return { allow: true, reason: "grant", personId };
}

type Logger = { warn: (message: string) => void };

/**
 * MONITOR mode: a deny is audited as `tool.would_deny` and logged, and the
 * call proceeds. Nothing is ever blocked in P1.
 */
export function monitor(
  client: AccessClient,
  logger: Logger,
  toolName: string,
  agentId: string | undefined,
  requester: Requester | undefined,
): Decision {
  const decision = decide(client, toolName, agentId, requester);
  if (!decision.allow) {
    const line = `access: would-deny tool=${toolName} agent=${agentId ?? "-"} reason=${decision.reason}`;
    logger.warn(line);
    try {
      client.audit({
        event: "tool.would_deny",
        decision: "would_deny",
        reason: decision.reason,
        personId: decision.personId,
        channel: requester?.channel,
        senderId: requester?.senderId,
        agentId,
        tool: toolName,
      });
    } catch {
      logger.warn(`${line} (audit write failed: store unavailable)`);
    }
  }
  return decision;
}

/** Enforce only explicitly selected agents. Other surfaces retain monitor behavior. */
export function enforce(
  client: AccessClient, logger: Logger, toolName: string,
  agentId: string | undefined, requester: Requester | undefined, params?: unknown,
): { allow: false; reason: string } | undefined {
  const decision = decide(client, toolName, agentId, requester, params);
  if (decision.allow) return undefined; // Never expands another policy's permissions.
  logger.warn(`access: denied tool=${toolName} agent=${agentId ?? "-"} reason=${decision.reason}`);
  try {
    client.audit({event: "tool.denied", decision: "deny", reason: decision.reason,
      personId: decision.personId, channel: requester?.channel,
      senderId: requester?.senderId, agentId, tool: toolName});
  } catch {
    logger.warn("access: deny audit unavailable; request remains blocked");
  }
  return {allow: false, reason: "This action is not available for this account. / Esta ação não está disponível para esta conta."};
}
