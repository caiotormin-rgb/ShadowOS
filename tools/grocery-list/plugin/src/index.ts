import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";

import { Type } from "typebox";
import { definePluginEntry, type OpenClawPluginDefinition } from "openclaw/plugin-sdk/plugin-entry";
import { pathToFileURL } from "node:url";
import { Value } from "typebox/value";

const execFileAsync = promisify(execFile);

const itemSchema = Type.Object(
  {
    name: Type.String({ minLength: 1, maxLength: 160 }),
    quantity: Type.Optional(Type.Number({ exclusiveMinimum: 0, maximum: 10000 })),
    unit: Type.Optional(Type.String({ maxLength: 40 })),
    note: Type.Optional(Type.String({ maxLength: 240 })),
    productUrl: Type.Optional(
      Type.String({
        minLength: 1,
        maxLength: 2048,
        description:
          "Canonical product URL extracted by OpenClaw, or the original HTTP(S) URL when no canonical URL is available. The grocery backend never fetches it.",
      }),
    ),
  },
  { additionalProperties: false },
);

export const legacyParameters = Type.Object(
  {
    action: Type.Union([
      Type.Literal("add"),
      Type.Literal("list"),
      Type.Literal("buy"),
      Type.Literal("unbuy"),
      Type.Literal("remove"),
      Type.Literal("close"),
      Type.Literal("reopen"),
      Type.Literal("history"),
      Type.Literal("stores"),
      Type.Literal("due"),
      Type.Literal("layout"),
      Type.Literal("help"),
      Type.Literal("onboard"),
      Type.Literal("activity"),
    ]),
    scope: Type.Optional(
      Type.Union([Type.Literal("family"), Type.Literal("private")], {
        description: "Use family unless the requester explicitly asks for a private list.",
      }),
    ),
    store: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
    items: Type.Optional(Type.Array(itemSchema, { minItems: 1, maxItems: 100 })),
    names: Type.Optional(
      Type.Array(Type.String({ minLength: 1, maxLength: 160 }), {
        minItems: 1,
        maxItems: 100,
      }),
    ),
    unit: Type.Optional(Type.String({ maxLength: 40 })),
    neededOnly: Type.Optional(Type.Boolean()),
    sourceType: Type.Optional(
      Type.Union([
        Type.Literal("text"),
        Type.Literal("voice"),
        Type.Literal("image"),
        Type.Literal("video"),
        Type.Literal("url"),
      ]),
    ),
    sourceRef: Type.Optional(Type.String({ maxLength: 500 })),
    rawText: Type.Optional(Type.String({ maxLength: 10000 })),
    tripId: Type.Optional(Type.Integer({ minimum: 1 })),
    // Constrained to the sections the CLI actually accepts. As free text the
    // model could pick a plausible synonym — "veggies", a Portuguese aisle
    // name — and argparse would exit 2, surfacing as a generic failure.
    section: Type.Optional(
      Type.Union(
        [
          Type.Literal("produce"),
          Type.Literal("bakery"),
          Type.Literal("butcher"),
          Type.Literal("seafood"),
          Type.Literal("dairy"),
          Type.Literal("frozen"),
          Type.Literal("pantry"),
          Type.Literal("snacks"),
          Type.Literal("beverages"),
          Type.Literal("household"),
          Type.Literal("personal_care"),
          Type.Literal("baby"),
          Type.Literal("pet"),
          Type.Literal("other"),
        ],
        { description: "Narrow `due` to one store section." },
      ),
    ),
    walkOrder: Type.Optional(
      Type.Union([Type.Literal("supermarket"), Type.Literal("warehouse")], {
        description: "Aisle order for this store. Warehouse clubs walk differently.",
      }),
    ),
    lang: Type.Optional(
      Type.Union([Type.Literal("en"), Type.Literal("pt")], {
        description:
          "Overrides the requester's stored preference. Omit it unless they asked to switch — the engine already answers each person in their own language.",
      }),
    ),
    limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
    // activity filters. The requester is never one of these: who is asking is
    // always the authenticated sender, passed as --actor below.
    since: Type.Optional(
      Type.String({
        minLength: 1,
        maxLength: 40,
        description:
          "Start of the period for activity: today, yesterday, 12h, 7d, 2w, or YYYY-MM-DD. Resolved in the requester's own timezone.",
      }),
    ),
    until: Type.Optional(
      Type.String({
        minLength: 1,
        maxLength: 40,
        description: "End of the period for activity, same forms; a day includes the whole day.",
      }),
    ),
    by: Type.Optional(
      Type.String({
        minLength: 1,
        maxLength: 80,
        description: "Only changes by this household member, by name (e.g. Alex).",
      }),
    ),
    item: Type.Optional(
      Type.String({
        minLength: 1,
        maxLength: 160,
        description: "Only changes to this product; either language matches (leite finds Milk).",
      }),
    ),
    changeType: Type.Optional(
      Type.Union(
        [
          Type.Literal("added"),
          Type.Literal("merged"),
          Type.Literal("purchased"),
          Type.Literal("unpurchased"),
          Type.Literal("removed"),
          Type.Literal("closed"),
          Type.Literal("reopened"),
        ],
        { description: "Only this kind of change, for activity. purchased answers who bought something." },
      ),
    ),
  },
  { additionalProperties: false },
);

export const configSchema = Type.Object(
  {
    pythonPath: Type.String({ minLength: 1 }),
    scriptPath: Type.String({ minLength: 1 }),
    familyDbPath: Type.String({ minLength: 1 }),
    privateDbDir: Type.String({ minLength: 1 }),
    whatsappAccountId: Type.String({ minLength: 1 }),
    allowedRequesters: Type.Array(Type.String({ minLength: 1 }), { minItems: 1 }),
    accessDbPath: Type.Optional(Type.String({ minLength: 1 })),
  },
  { additionalProperties: false },
);

export type GroceryConfig = {
  pythonPath: string;
  scriptPath: string;
  familyDbPath: string;
  privateDbDir: string;
  whatsappAccountId: string;
  allowedRequesters: string[];
  accessDbPath?: string;
};

type GroceryParams = {
  action:
    | "add" | "list" | "buy" | "unbuy" | "remove" | "close" | "reopen"
    | "history" | "stores" | "due" | "layout" | "help" | "onboard" | "activity";
  since?: string;
  until?: string;
  by?: string;
  item?: string;
  changeType?:
    | "added" | "merged" | "purchased" | "unpurchased" | "removed" | "closed" | "reopened";
  scope?: "family" | "private";
  store?: string;
  items?: Array<{
    name: string;
    quantity?: number;
    unit?: string;
    note?: string;
    productUrl?: string;
  }>;
  names?: string[];
  unit?: string;
  neededOnly?: boolean;
  sourceType?: "text" | "voice" | "image" | "video" | "url";
  sourceRef?: string;
  rawText?: string;
  tripId?: number;
  limit?: number;
  // Mirrors the Type.Union above. The schema is what validates, but an
  // alias saying `string` invites a future edit back to free text.
  section?:
    | "produce"
    | "bakery"
    | "butcher"
    | "seafood"
    | "dairy"
    | "frozen"
    | "pantry"
    | "snacks"
    | "beverages"
    | "household"
    | "personal_care"
    | "baby"
    | "pet"
    | "other";
  walkOrder?: "supermarket" | "warehouse";
  lang?: "en" | "pt";
};

// These answer in prose rather than JSON, so their stdout is returned as-is.
// Actions whose CLI counterpart prints prose rather than JSON. Their stdout
// is returned verbatim; parsing it throws. `due` belongs here because it is
// invoked with --format text, which is the whole point of asking it what is
// missing — the answer is a sentence, not a row set.
const TEXT_ACTIONS = new Set(["help", "onboard", "due"]);

export function normalizePhone(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 8 || digits.length > 15) {
    throw new Error("requester does not have a valid phone-number identity");
  }
  return `+${digits}`;
}

export function databasePath(
  scope: "family" | "private",
  requester: string,
  config: Pick<GroceryConfig, "familyDbPath" | "privateDbDir">,
): string {
  if (scope === "family") {
    return resolve(config.familyDbPath);
  }
  const key = createHash("sha256").update(normalizePhone(requester)).digest("hex").slice(0, 24);
  return join(resolve(config.privateDbDir), `${key}.sqlite3`);
}

function requireItems(params: GroceryParams): NonNullable<GroceryParams["items"]> {
  if (!params.items?.length) {
    throw new Error("items are required for action=add");
  }
  return params.items;
}

// Filler models put in a field they believe is required. Acting on one would
// buy or remove whatever the engine matches to "item", so it is never a name.
const PLACEHOLDER_NAMES = new Set(["", "placeholder", "item", "x", "name"]);

function realNames(values: Array<string | undefined>): string[] {
  return values
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter((value) => !PLACEHOLDER_NAMES.has(value.toLowerCase()));
}

// buy/unbuy/remove take `names`, but models often send the item the way `add`
// does (`items: [{name}]`) or the way `activity` does (`item`). Accept those,
// in that order of precedence; a shape holding only placeholders falls through
// to the next, and nothing real left is the same error as nothing sent.
// Names are positional and followed by flags, so one beginning with "-" would
// be read by argparse as an option; every name that reaches the CLI is refused
// on that, whichever field it came from.
function requireNames(params: GroceryParams): string[] {
  const candidates = [
    realNames(params.names ?? []),
    realNames((params.items ?? []).map((entry) => entry?.name)),
    realNames([params.item]),
  ];
  const names = candidates.find((list) => list.length > 0);
  if (!names) {
    throw new Error(`names are required for action=${params.action}`);
  }
  return names.map((name) => flagSafe("names", name));
}

// A free-text value that begins with "-" would be read by argparse as an
// option rather than as the value. Refuse it instead of guessing.
function flagSafe(field: string, value: string): string {
  if (value.startsWith("-")) {
    throw new Error(`${field} must not begin with "-"`);
  }
  return value;
}

export function groceryArguments(
  params: GroceryParams,
  dbPath: string,
  actor: string,
): string[] {
  const args = ["--db", dbPath];
  const addLang = () => {
    if (params.lang) args.push("--lang", params.lang);
  };
  const addStore = () => {
    if (params.store) args.push("--store", params.store);
  };

  switch (params.action) {
    case "add":
      args.push("ingest");
      addStore();
      args.push(
        "--source-type",
        params.sourceType ?? "text",
        "--source-ref",
        params.sourceRef ?? "",
        "--raw-text",
        params.rawText ?? "",
        "--items-json",
        JSON.stringify(requireItems(params)),
        "--actor",
        actor,
      );
      break;
    case "list":
      args.push("list");
      addStore();
      if (params.neededOnly) args.push("--needed-only");
      // The engine resolves the reply language and the caller's household from
      // --actor, and refuses an unidentified caller once members are enrolled.
      args.push("--actor", actor);
      break;
    case "buy":
    case "unbuy":
    case "remove":
      args.push(params.action);
      addStore();
      args.push(...requireNames(params));
      if (params.unit !== undefined) args.push("--unit", params.unit);
      // Where the change came from, recorded in history as add already does.
      args.push(
        "--source-type",
        params.sourceType ?? "text",
        "--source-ref",
        params.sourceRef ?? "",
        "--raw-text",
        params.rawText ?? "",
        "--actor",
        actor,
      );
      break;
    case "close":
      args.push("close");
      addStore();
      args.push("--actor", actor);
      break;
    case "reopen":
      args.push("reopen");
      addStore();
      if (params.tripId !== undefined) args.push("--trip-id", String(params.tripId));
      args.push("--actor", actor);
      break;
    case "history":
      args.push("history");
      addStore();
      if (params.limit !== undefined) args.push("--limit", String(params.limit));
      args.push("--actor", actor);
      break;
    case "stores":
      args.push("stores");
      args.push("--actor", actor);
      break;
    case "activity":
      // Read-only, and scoped by the engine to the sender's household. `by`
      // filters whose changes to show; the caller is always --actor.
      args.push("activity");
      addStore();
      if (params.since) args.push("--since", flagSafe("since", params.since));
      if (params.until) args.push("--until", flagSafe("until", params.until));
      if (params.by) args.push("--by", flagSafe("by", params.by));
      if (params.item) args.push("--item", flagSafe("item", params.item));
      if (params.changeType) args.push("--action", params.changeType);
      if (params.limit !== undefined) args.push("--limit", String(params.limit));
      args.push("--actor", actor);
      addLang();
      break;
    case "due":
      args.push("due");
      addStore();
      if (params.section) args.push("--section", params.section);
      args.push("--format", "text", "--actor", actor);
      addLang();
      break;
    case "layout":
      args.push("layout");
      addStore();
      if (params.walkOrder) args.push("--set", params.walkOrder);
      args.push("--actor", actor);
      break;
    case "help":
    case "onboard":
      args.push(params.action);
      args.push("--actor", actor);
      addLang();
      break;
  }
  return args;
}

// The engine's activity JSON carries every row plus the same content rendered
// as text: ~95 KB at limit=100. The agent relays `text`, so the rows only cost
// tokens. Keep the rendered answer and what the agent needs to act on it.
export function compactActivity(result: Record<string, unknown>): Record<string, unknown> {
  const { text, count, truncated, limit, by_matched, timezone } = result;
  return { text, count, truncated, limit, by_matched, timezone };
}

export async function runGrocery(
  params: GroceryParams,
  requester: string,
  config: GroceryConfig,
): Promise<Record<string, unknown>> {
  const actor = normalizePhone(requester);
  const allowed = new Set(config.allowedRequesters.map(normalizePhone));
  if (!allowed.has(actor)) {
    throw new Error("requester is not authorized for grocery tools");
  }

  const scope = params.scope ?? "family";
  const dbPath = databasePath(scope, actor, config);
  await mkdir(dirname(dbPath), { recursive: true, mode: 0o700 });

  try {
    const { stdout } = await execFileAsync(
      resolve(config.pythonPath),
      [resolve(config.scriptPath), ...groceryArguments(params, dbPath, actor)],
      { timeout: 15_000, maxBuffer: 2 * 1024 * 1024 },
    );
    if (TEXT_ACTIONS.has(params.action)) {
      return { scope, text: stdout.trimEnd() };
    }
    const result = JSON.parse(stdout) as Record<string, unknown>;
    return { scope, ...(params.action === "activity" ? compactActivity(result) : result) };
  } catch (error) {
    const failure = error as { stderr?: string; message?: string };
    const detail = failure.stderr?.trim() || failure.message || "grocery operation failed";
    throw new Error(detail);
  }
}

// All registered tools use the compact API. Legacy argv helpers remain for CLI tests.
const common = {
  scope: Type.Optional(Type.Union([Type.Literal("family"), Type.Literal("private")], { description: "Default family; private only when explicitly requested." })),
  store: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })),
  lang: Type.Optional(Type.Union([Type.Literal("en"), Type.Literal("pt")], { description: "Reply language; omit to use stored preference." })),
};
const names = Type.Array(Type.Object({ name: Type.String({ minLength: 1, maxLength: 160, description: "The user's words; the engine resolves partial names." }) }, { additionalProperties: false }), { minItems: 1, maxItems: 100 });
const source = { sourceType: legacyParameters.properties.sourceType, rawText: legacyParameters.properties.rawText };
const object = (properties: Record<string, any>) => Type.Object(properties, { additionalProperties: false });
export const narrowTools = [
  {
    name: "grocery_show", description: "Show grocery list, stores, missing regular purchases (due), previous trips, aisle layout, help or onboarding. Relay reply verbatim.",
    parameters: object({ ...common, view: Type.Union([Type.Literal("list"), Type.Literal("stores"), Type.Literal("due"), Type.Literal("history"), Type.Literal("layout"), Type.Literal("help"), Type.Literal("onboard")]), neededOnly: Type.Optional(Type.Boolean()), limit: legacyParameters.properties.limit, section: legacyParameters.properties.section }),
    request: (p: Record<string, unknown>) => { const { view, ...rest } = p; return { ...rest, action: view }; },
  },
  {
    name: "grocery_add", description: "Add groceries. Keep names as supplied. Omit store for default. URLs are metadata only; no page fetching.",
    parameters: object({ ...common, ...source, items: Type.Array(itemSchema, { minItems: 1, maxItems: 100 }) }),
    request: (p: Record<string, unknown>) => ({ ...p, action: "add" }),
  },
  {
    name: "grocery_mark", description: "Mark groceries purchased or needed. Pass partial names as said; relay clarification without guessing. Missing purchased items are added already bought.",
    parameters: object({ ...common, ...source, items: names, state: Type.Union([Type.Literal("purchased"), Type.Literal("needed")]), unit: legacyParameters.properties.unit }),
    request: (p: Record<string, unknown>) => { const { state, ...rest } = p; return { ...rest, action: state === "purchased" ? "buy" : "unbuy" }; },
  },
  {
    name: "grocery_remove", description: "Preview removal only. Relay /remover CODE; removal requires a direct user command. Never confirm for the user.",
    parameters: object({ ...common, items: names, unit: legacyParameters.properties.unit }),
    request: (p: Record<string, unknown>) => ({ ...p, action: "remove" }),
  },
  {
    name: "grocery_trip", description: "Close a shopping trip or reopen a closed trip.",
    parameters: object({ ...common, operation: Type.Union([Type.Literal("close"), Type.Literal("reopen")]), tripId: legacyParameters.properties.tripId }),
    request: (p: Record<string, unknown>) => { const { operation, ...rest } = p; return { ...rest, action: operation }; },
  },
  {
    name: "grocery_activity", description: "Show who changed groceries and when. by filters a member name; it never changes requester identity.",
    parameters: object({ ...common, since: legacyParameters.properties.since, until: legacyParameters.properties.until, by: legacyParameters.properties.by, item: legacyParameters.properties.item, changeType: legacyParameters.properties.changeType, limit: legacyParameters.properties.limit }),
    request: (p: Record<string, unknown>) => ({ ...p, action: "activity" }),
  },
  {
    name: "grocery_preferences", description: "Read or save your own language, usual store, display name and timezone; set store aisle order with walkOrder. Supply only preferences the requester gave; no preference fields reads current values.",
    parameters: object({ ...common, walkOrder: legacyParameters.properties.walkOrder, name: Type.Optional(Type.String({ minLength: 1, maxLength: 80 })), timezone: Type.Optional(Type.String({ minLength: 1, maxLength: 80 })) }),
    request: (p: Record<string, unknown>) => ({ ...p, action: "preferences" }),
  },
];
export type ApiResult = { ok: boolean; reply: string; status: "done" | "clarification" | "confirmation" | "error"; assumptions: string[]; candidates?: unknown[]; confirmation_code?: string };
export type Exec = (file: string, args: string[], options: { timeout: number; maxBuffer: number }) => Promise<{ stdout: string }>;
const failure = (lang: unknown, denied = false): ApiResult => ({ ok: false, status: "error", assumptions: [], reply: lang === "pt" ? (denied ? "Esta lista não está disponível nesta conversa." : "Não consegui concluir o pedido. Tente novamente.") : (denied ? "This list is not available in this conversation." : "I could not complete the request. Please try again.") });

// Never strip arbitrary identity characters: a group JID must not become a user.
export function trustedPhone(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = /^(?:whatsapp:)?(\+?[1-9][0-9]{7,14})(?:@s\.whatsapp\.net)?$/.exec(value);
  return match ? `+${match[1].replace(/^\+/, "")}` : null;
}
export function authorizedRequester(channel: unknown, account: unknown, sender: unknown, config: GroceryConfig): string | null {
  if (channel !== "whatsapp" || account !== config.whatsappAccountId) return null;
  const actor = trustedPhone(sender);
  return actor && config.allowedRequesters.some((entry) => trustedPhone(entry) === actor) ? actor : null;
}
export function publicResult(value: unknown): ApiResult {
  if (!value || typeof value !== "object") throw new Error("invalid response");
  const p = value as Record<string, unknown>;
  if (typeof p.ok !== "boolean" || typeof p.reply !== "string" || !["done", "clarification", "confirmation", "error"].includes(String(p.status)) || !Array.isArray(p.assumptions) || !p.assumptions.every((v) => typeof v === "string")) throw new Error("invalid response");
  const result: ApiResult = { ok: p.ok, reply: p.reply, status: p.status as ApiResult["status"], assumptions: p.assumptions };
  if (Array.isArray(p.candidates)) result.candidates = p.candidates.map((candidate) => {
    if (typeof candidate === "string") return candidate;
    if (!candidate || typeof candidate !== "object") throw new Error("invalid candidate");
    const { name, unit, candidates } = candidate;
    if (typeof name !== "string") throw new Error("invalid candidate");
    return { name, ...(typeof unit === "string" ? { unit } : {}), ...(Array.isArray(candidates) && candidates.every((v: unknown) => typeof v === "string") ? { candidates } : {}) };
  });
  if (typeof p.confirmation_code === "string") result.confirmation_code = p.confirmation_code;
  return result;
}
export function requestKey(session: string | undefined, toolCallId: string): string | undefined {
  // SDK has no inbound message ID. Protects one host call retry, not repeated texts.
  return session && toolCallId ? createHash("sha256").update(JSON.stringify([session, toolCallId])).digest("hex") : undefined;
}
export async function runAgentApi(request: Record<string, unknown>, requester: string, config: GroceryConfig, options: { exec?: Exec; trustedConfirmation?: boolean; requestId?: string } = {}): Promise<ApiResult> {
  const actor = authorizedRequester("whatsapp", config.whatsappAccountId, requester, config);
  if (!actor || (request.action === "confirm_remove" && !options.trustedConfirmation)) return failure(request.lang, true);
  const scope = request.scope === "private" ? "private" : "family";
  const db = databasePath(scope, actor, config);
  const { actor: _actor, db: _db, request_id: _requestId, trusted_confirmation: _trusted, scope: _scope, ...payload } = request;
  if (options.requestId) payload.request_id = options.requestId;
  const args = [join(dirname(resolve(config.scriptPath)), "agent_api.py"), "--db", db, "--actor", actor, "--request-json", JSON.stringify(payload)];
  if (scope === "private") args.push("--private");
  if (options.trustedConfirmation) args.push("--trusted-confirmation");
  try {
    await mkdir(dirname(db), { recursive: true, mode: 0o700 });
    const { stdout } = await (options.exec ?? execFileAsync as Exec)(resolve(config.pythonPath), args, { timeout: 15_000, maxBuffer: 256 * 1024 });
    const result = publicResult(JSON.parse(stdout));
    if (scope === "private" && result.status === "confirmation" && result.confirmation_code) result.reply = result.reply.replace(`/remover ${result.confirmation_code}`, `/remover ${result.confirmation_code} private`);
    return result;
  } catch { return failure(request.lang); }
}
export type RemoveCommandContext = { channel: string; channelId?: string; accountId?: string; senderId?: string; from?: string; isAuthorizedSender?: boolean; args?: string };
export async function removeCommand(ctx: RemoveCommandContext, config: GroceryConfig, exec?: Exec): Promise<{ text: string }> {
  const actor = authorizedRequester(ctx.channelId ?? ctx.channel, ctx.accountId, ctx.senderId, config);
  if (!actor || trustedPhone(ctx.from) !== actor || ctx.isAuthorizedSender !== true) return { text: "Removal is unavailable here. / A remoção não está disponível aqui." };
  if (config.accessDbPath) {
    try {
      const modulePath = resolve(dirname(config.scriptPath), "..", "..", "access", "plugin", "dist", "client.js");
      const { AccessClient } = await import(pathToFileURL(modulePath).href);
      const access = new AccessClient(config.accessDbPath);
      const principal = access.resolvePrincipal("whatsapp", config.whatsappAccountId, actor);
      if (!principal || !access.can(principal.personId, "grocery", "use")) return { text: "Removal is unavailable here. / A remoção não está disponível aqui." };
    } catch { return { text: "Removal is unavailable here. / A remoção não está disponível aqui." }; }
  }
  const match = /^([A-Za-z0-9]{6,32})(?:\s+(private))?$/.exec((ctx.args ?? "").trim());
  if (!match) return { text: "Send /remover CODE from the preview. / Envie /remover CÓDIGO da prévia." };
  const result = await runAgentApi({ action: "confirm_remove", confirmation_code: match[1], scope: match[2] ? "private" : "family" }, actor, config, { trustedConfirmation: true, exec });
  return { text: result.reply };
}
const plugin: OpenClawPluginDefinition = definePluginEntry({
  id: "grocery-list-tool", name: "Grocery List Tool", description: "Purpose-specific household grocery tools with native removal confirmation.",
  register(api) {
    const config = api.pluginConfig as GroceryConfig;
    const definitions = [...narrowTools, { name: "grocery_list", description: "Deprecated Grocery adapter. Do not allowlist alongside narrow tools.", parameters: legacyParameters, request: (p: Record<string, unknown>) => p }];
    for (const definition of definitions) {
      api.registerTool((context) => {
        const actor = authorizedRequester(context.deliveryContext?.channel ?? context.messageChannel, context.deliveryContext?.accountId, context.requesterSenderId, config);
        if (!actor) return null;
        return { name: definition.name, label: definition.name, description: definition.description, parameters: definition.parameters,
          execute: async (toolCallId: string, input: unknown) => {
            const result = Value.Check(definition.parameters, input) ? await runAgentApi(definition.request(input as Record<string, unknown>), actor, config, { requestId: requestKey(context.sessionId ?? context.sessionKey, toolCallId) }) : failure((input as Record<string, unknown> | null)?.lang);
            return { content: [{ type: "text" as const, text: JSON.stringify(result) }], details: result };
          },
        };
      }, { name: definition.name, optional: true });
    }
    api.registerCommand({ name: "remover", description: "Confirm grocery removal: /remover CODE", acceptsArgs: true, requireAuth: true, handler: async (ctx) => removeCommand(ctx, config) });
  },
});

export default plugin;

/** Production schema mapping reused by isolated evaluations. */
export function toolRequest(name: string, input: unknown): Record<string, unknown> {
  const tool = narrowTools.find((entry) => entry.name === name);
  if (!tool || !Value.Check(tool.parameters, input)) throw new Error("Invalid grocery request");
  return tool.request(input as Record<string, unknown>);
}
