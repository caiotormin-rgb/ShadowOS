import { AccessClient, type Principal } from "./client.js";

/** The subset of PluginCommandContext these handlers read. */
export type CommandCtx = {
  senderId?: string;
  from?: string;
  channel: string;
  channelId?: string;
  accountId?: string;
  senderIsOwner?: boolean;
  args?: string;
};

type Lang = "en" | "pt";

const TEXT = {
  refused: {
    en: "Only the household admin can use /access.",
    pt: "Somente o administrador da casa pode usar /access.",
  },
  unavailable: {
    en: "Access data is unavailable right now.",
    pt: "Os dados de acesso estão indisponíveis agora.",
  },
  unknown: {
    en: "I don't have you on the household list yet.",
    pt: "Você ainda não está na lista da casa.",
  },
  usage: { en: "Usage: /access who", pt: "Uso: /access quem" },
  whoTitle: { en: "Household members:", pt: "Membros da casa:" },
  role: { en: { owner: "admin", member: "member" }, pt: { owner: "administrador", member: "membro" } },
  meRole: { en: "Role", pt: "Papel" },
  meGrants: { en: "Access", pt: "Acesso" },
  meEmail: { en: "Verified email", pt: "Email verificado" },
  none: { en: "none", pt: "nenhum" },
} as const;

// Unknown senders have no stored language, so they get both.
const both = (t: { en: string; pt: string }) => `${t.en}\n${t.pt}`;

function identity(ctx: CommandCtx) {
  const channel = ctx.channelId ?? ctx.channel;
  const senderId = ctx.senderId ?? ctx.from ?? "";
  return { channel, senderId, accountId: ctx.accountId };
}

function lookup(client: AccessClient, ctx: CommandCtx): Principal | null | "unavailable" {
  const { channel, senderId, accountId } = identity(ctx);
  if (!senderId) return null;
  try {
    return client.resolvePrincipal(channel, accountId, senderId);
  } catch {
    return "unavailable";
  }
}

function tryAudit(client: AccessClient, entry: Parameters<AccessClient["audit"]>[0]) {
  try {
    client.audit(entry);
  } catch {
    /* store unavailable: the reply already refuses */
  }
}

/**
 * /access: owner only. Authorization runs before the subcommand is parsed, so
 * a member or unknown sender gets the same refusal for every subcommand.
 * Owner = host says senderIsOwner AND the store says role owner.
 */
export function accessCommand(client: AccessClient, ctx: CommandCtx): { text: string } {
  const principal = lookup(client, ctx);
  const { channel, senderId } = identity(ctx);
  const sub = (ctx.args ?? "").trim().split(/\s+/)[0]?.toLowerCase() ?? "";
  if (principal === "unavailable") return { text: both(TEXT.unavailable) };
  if (!principal || principal.role !== "owner" || ctx.senderIsOwner !== true) {
    tryAudit(client, {
      event: "command.denied",
      decision: "deny",
      reason: principal ? "not_owner" : "unknown_sender",
      personId: principal?.personId,
      channel,
      senderId,
      tool: `/access ${sub}`.trim(),
    });
    return { text: principal ? TEXT.refused[principal.lang] : both(TEXT.refused) };
  }
  const lang: Lang = principal.lang;
  if (sub !== "who" && sub !== "quem") return { text: TEXT.usage[lang] };
  try {
    const lines = client
      .roster()
      .map((p) => `- ${p.name} (${TEXT.role[lang][p.role]}): ${p.identities.join(", ") || TEXT.none[lang]}`);
    tryAudit(client, { event: "command.access_who", decision: "allow", personId: principal.personId, channel });
    return { text: [TEXT.whoTitle[lang], ...lines].join("\n") };
  } catch {
    return { text: TEXT.unavailable[lang] };
  }
}

/** /me: the sender's own role, grants and verified email. Never anyone else's. */
export function meCommand(client: AccessClient, ctx: CommandCtx): { text: string } {
  const principal = lookup(client, ctx);
  if (principal === "unavailable") return { text: both(TEXT.unavailable) };
  if (!principal) return { text: both(TEXT.unknown) };
  const lang = principal.lang;
  try {
    const grants = client.grants(principal.personId).map((g) => `${g.resource}.${g.action}`);
    const email = client.verifiedEmail(principal.personId);
    return {
      text: [
        principal.name,
        `${TEXT.meRole[lang]}: ${TEXT.role[lang][principal.role]}`,
        `${TEXT.meGrants[lang]}: ${principal.role === "owner" ? "*" : grants.join(", ") || TEXT.none[lang]}`,
        `${TEXT.meEmail[lang]}: ${email ?? TEXT.none[lang]}`,
      ].join("\n"),
    };
  } catch {
    return { text: TEXT.unavailable[lang] };
  }
}
