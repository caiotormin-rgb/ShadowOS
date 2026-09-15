import { execFile } from "node:child_process";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const execFileAsync = promisify(execFile);

export type Exec = (
  file: string,
  args: string[],
  options: { timeout: number; maxBuffer: number },
) => Promise<{ stdout: string }>;

export type DoctorConfig = {
  pythonPath: string;
  scriptPath: string;
  dbPath: string;
  whatsappAccountId: string;
  allowedRequesters: string[];
  accessDbPath?: string;
};

export const ACTIONS = [
  "start", "list", "intake", "confirm_intake",
  "search", "widen", "shortlist", "choose", "summary",
  "verify_email", "confirm_email", "draft_email", "email_status", "replies", "close",
] as const;
export type Action = (typeof ACTIONS)[number];

export type DoctorParams = {
  action: Action;
  requestId?: string;
  lang?: "en" | "pt";
  fields?: Record<string, unknown>;
  ranks?: number[];
  rank?: number;
  email?: string;
  code?: string;
  to?: string;
  name?: string;
  cc?: string;
  note?: string;
};

const intakeFields = Type.Object(
  {
    patient: Type.Optional(Type.String({ maxLength: 120, description: "Who the visit is for, e.g. 'me' or 'son, 9'." })),
    zip: Type.Optional(Type.String({ pattern: "^\\d{5}$" })),
    miles: Type.Optional(Type.Number({ minimum: 1, maximum: 60 })),
    max_miles: Type.Optional(Type.Number({ minimum: 1, maximum: 60 })),
    availability: Type.Optional(Type.String({ maxLength: 200 })),
    visit_type: Type.Optional(Type.Union([Type.Literal("visit"), Type.Literal("urgent"), Type.Literal("lab")])),
    specialty_text: Type.Optional(
      Type.String({
        maxLength: 160,
        description: "What they are looking for, in their own words with every detail, e.g. 'psychologist specialized in children with autism'.",
      }),
    ),
    context: Type.Optional(Type.String({ maxLength: 300 })),
    cc: Type.Optional(Type.String({ maxLength: 80 })),
    plan_name: Type.Optional(Type.String({ maxLength: 80, description: "Plan name only, e.g. ExamplePlan. Never a member ID." })),
    language_pref: Type.Optional(Type.String({ maxLength: 40, description: "Language the doctor should speak." })),
  },
  { additionalProperties: false },
);

export const parameters = Type.Object(
  {
    action: Type.Union(ACTIONS.map((a) => Type.Literal(a))),
    requestId: Type.Optional(Type.String({ pattern: "^REQ-[0-9A-F]{6}$" })),
    lang: Type.Optional(Type.Union([Type.Literal("en"), Type.Literal("pt")])),
    fields: Type.Optional(intakeFields),
    ranks: Type.Optional(Type.Array(Type.Integer({ minimum: 1, maximum: 15 }), { minItems: 1, maxItems: 5 })),
    rank: Type.Optional(Type.Integer({ minimum: 1, maximum: 15 })),
    email: Type.Optional(Type.String({ maxLength: 254, description: "The requester's own email, for verify_email." })),
    code: Type.Optional(Type.String({ maxLength: 12, description: "The 6-digit email verification code the person received." })),
    to: Type.Optional(Type.String({ maxLength: 254, description: "The practice's email, from its own website." })),
    name: Type.Optional(Type.String({ maxLength: 80, description: "The requester's name, to sign the email." })),
    cc: Type.Optional(Type.String({ maxLength: 254, description: "Another household member's verified email." })),
    note: Type.Optional(Type.String({ maxLength: 300 })),
  },
  { additionalProperties: false },
);

const DESCRIPTION =
  "Find and rank doctors, therapists, urgent care or labs for a household member, then contact practices. " +
  "Steps: start, intake, confirm_intake (only after the person confirms; this starts a background search " +
  "that messages them when ready), shortlist, choose, summary. search re-runs a failed search; widen " +
  "searches a larger area. Email: verify_email, confirm_email, draft_email; the person approves a draft " +
  "themselves with /ok.";

export function normalizePhone(value: string): string {
  const match = /^(?:whatsapp:)?(\+?[1-9][0-9]{7,14})(?:@s\.whatsapp\.net)?$/.exec(value);
  if (!match) {
    throw new Error("requester does not have a valid phone-number identity");
  }
  return `+${match[1].replace(/^\+/, "")}`;
}

export function authorize(requester: string, config: DoctorConfig): string {
  const actor = normalizePhone(requester);
  if (!config.allowedRequesters.map(normalizePhone).includes(actor)) {
    throw new Error("requester is not authorized for doctor search");
  }
  return actor;
}

function need<T>(value: T | undefined, field: string, action: string): T {
  if (value === undefined || value === null || (typeof value === "string" && !value.trim())) {
    throw new Error(`${field} is required for action=${action}`);
  }
  return value;
}

/** CLI arguments. Values are passed as --flag=value so one starting with "-" stays a value. */
export function commandFor(p: DoctorParams, actor: string): string[] {
  const who = `--requester=${actor}`;
  const id = () => `--id=${need(p.requestId, "requestId", p.action)}`;
  switch (p.action) {
    case "start":
      return ["new", who, `--lang=${p.lang ?? "en"}`];
    case "list":
      return ["list", who];
    case "intake":
      return ["intake", who, id(), `--fields=${JSON.stringify(need(p.fields, "fields", p.action))}`, "--format=text"];
    case "confirm_intake":
      return ["confirm-intake", who, id()];
    case "search":
      return ["search", who, id()];
    case "widen":
      return ["widen", who, id(), `--fields=${JSON.stringify(need(p.fields, "fields", p.action))}`];
    case "shortlist":
      return ["show", who, id(), "--format=shortlist"];
    case "choose":
      return ["choose", who, id(), `--ranks=${need(p.ranks, "ranks", p.action).join(",")}`];
    case "summary":
      return ["show", who, id(), "--format=summary"];
    case "verify_email":
      return ["verify-email", who, `--email=${need(p.email, "email", p.action)}`, `--lang=${p.lang ?? "en"}`];
    case "confirm_email":
      return ["confirm-email", who, `--code=${need(p.code, "code", p.action)}`];
    case "draft_email": {
      const args = ["draft", who, id(), `--rank=${need(p.rank, "rank", p.action)}`,
        `--to=${need(p.to, "to", p.action)}`, `--name=${need(p.name, "name", p.action)}`];
      if (p.cc) args.push(`--cc=${p.cc}`);
      if (p.note) args.push(`--note=${p.note}`);
      return args;
    }
    case "email_status":
      return ["outreach", who, id()];
    case "replies":
      return ["replies", who, id()];
    case "close":
      return ["close", who, id()];
    default:
      throw new Error(`unknown action ${(p as { action: string }).action}`);
  }
}

const PROSE = new Set<Action>(["intake", "shortlist", "summary"]);

/** Runs the engine. Its own refusals (exit 2, JSON on stdout) come back as data for the agent to explain. */
export async function runEngine(
  args: string[],
  config: DoctorConfig,
  exec: Exec,
  opts: { slow?: boolean; prose?: boolean } = {},
): Promise<Record<string, unknown>> {
  try {
    const { stdout } = await exec(
      resolve(config.pythonPath),
      [resolve(config.scriptPath), `--db=${resolve(config.dbPath)}`, ...args],
      { timeout: opts.slow ? 280_000 : 60_000, maxBuffer: 2 * 1024 * 1024 },
    );
    return opts.prose ? { ok: true, text: stdout.trimEnd() } : (JSON.parse(stdout) as Record<string, unknown>);
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string; message?: string; killed?: boolean };
    if (failure.stdout) {
      try {
        return JSON.parse(failure.stdout) as Record<string, unknown>;
      } catch {
        /* not the engine's JSON: fall through */
      }
    }
    if (failure.killed) {
      return { ok: false, error: "timeout", message: "The search took too long. Try again, or a smaller area." };
    }
    // Unexpected runtime failures are operational diagnostics, not chat content.
    // Keep known domain JSON refusals above inspectable; never expose stderr.
    return { ok: false, error: "unavailable", message: "Doctor search is temporarily unavailable. Please try again." };
  }
}

export async function runDoctor(
  p: DoctorParams,
  requester: string,
  config: DoctorConfig,
  exec: Exec = execFileAsync as Exec,
): Promise<Record<string, unknown>> {
  const actor = authorize(requester, config);
  return runEngine(commandFor(p, actor), config, exec, { prose: PROSE.has(p.action) });
}

export type CommandCtx = {
  senderId?: string;
  from?: string;
  channel: string;
  channelId?: string;
  accountId?: string;
  args?: string;
  isAuthorizedSender?: boolean;
};

const OK = {
  en: {
    sent: "✅ Email sent to {to}.",
    notSent: "Not sent to {to} ({reason}).",
    none: "There was no approved email waiting.",
    bad: "That code doesn't match one of your drafts. Send /ok and the 6-character code from the draft.",
    unavailable: "Sending isn't available right now. Try again later.",
    where: "/ok works only on the household WhatsApp number.",
  },
  pt: {
    sent: "✅ Email enviado para {to}.",
    notSent: "Não enviado para {to} ({reason}).",
    none: "Não havia email aprovado esperando.",
    bad: "Esse código não corresponde a um rascunho seu. Envie /ok e o código de 6 caracteres do rascunho.",
    unavailable: "O envio não está disponível agora. Tente mais tarde.",
    where: "/ok só funciona no WhatsApp da casa.",
  },
};
const both = (key: keyof (typeof OK)["en"]) => `${OK.en[key]}\n${OK.pt[key]}`;

/** /ok CODE: the only way a draft gets approved and sent. Handled by the host, never by the model. */
export async function okCommand(
  ctx: CommandCtx,
  config: DoctorConfig,
  exec: Exec = execFileAsync as Exec,
): Promise<{ text: string }> {
  const channel = ctx.channelId ?? ctx.channel;
  if (channel !== "whatsapp" || ctx.accountId !== config.whatsappAccountId || ctx.isAuthorizedSender !== true) {
    return { text: both("where") };
  }
  let actor: string;
  try {
    actor = authorize(ctx.senderId ?? "", config);
    if (normalizePhone(ctx.from ?? "") !== actor) return { text: both("where") };
  } catch {
    return { text: both("where") };
  }
  // Commands bypass model tool policies: re-check current grants before sending.
  if (config.accessDbPath) {
    try {
      const modulePath = resolve(dirname(config.scriptPath), "../../access/plugin/dist/client.js");
      const { AccessClient } = await import(pathToFileURL(modulePath).href);
      const access = new AccessClient(config.accessDbPath);
      const principal = access.resolvePrincipal("whatsapp", config.whatsappAccountId, actor);
      if (!principal || !access.can(principal.personId, "doctor", "request")) {
        return { text: both("unavailable") };
      }
    } catch {
      return { text: both("unavailable") };
    }
  }
  const code = (ctx.args ?? "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(code)) return { text: both("bad") };
  let result: Record<string, unknown>;
  try {
    result = await runEngine(["ok", `--requester=${actor}`, `--code=${code}`], config, exec);
  } catch {
    return { text: both("unavailable") };
  }
  if (result.ok === false) return { text: result.error === "bad_code" ? both("bad") : both("unavailable") };
  const t = OK[result.lang === "pt" ? "pt" : "en"];
  const rows = (result.results as Array<{ to: string; status: string; reason?: string }>) ?? [];
  if (!rows.length) return { text: t.none };
  return {
    text: rows
      .map((r) =>
        r.status === "sent"
          ? t.sent.replace("{to}", r.to)
          : t.notSent.replace("{to}", r.to).replace("{reason}", r.reason ?? r.status),
      )
      .join("\n"),
  };
}

export default definePluginEntry({
  id: "doctor-search-tool",
  name: "Doctor Search",
  description: "Find in-network providers for household members and email practices after the requester approves (/ok).",
  register(api) {
    const config = api.pluginConfig as DoctorConfig;

    api.registerTool(
      (toolContext) => {
        const accountId = toolContext.deliveryContext?.accountId;
        const channel = toolContext.deliveryContext?.channel ?? toolContext.messageChannel;
        const requester = toolContext.requesterSenderId;
        if (channel !== "whatsapp" || accountId !== config.whatsappAccountId || !requester) {
          return null;
        }
        return {
          name: "doctor_search",
          label: "Doctor Search",
          description: DESCRIPTION,
          parameters,
          execute: async (_toolCallId: string, input: unknown) => {
            const result = await runDoctor(input as DoctorParams, requester, config);
            const text = typeof result.text === "string" && result.ok !== false
              ? result.text
              : JSON.stringify(result, null, 2);
            return { content: [{ type: "text" as const, text }], details: result };
          },
        };
      },
      { name: "doctor_search", optional: true },
    );

    api.registerCommand({
      name: "ok",
      description: "Approve and send a doctor-search email draft: /ok CODE",
      acceptsArgs: true,
      requireAuth: true,
      handler: async (ctx) => okCommand(ctx as unknown as CommandCtx, config),
    });
  },
});
