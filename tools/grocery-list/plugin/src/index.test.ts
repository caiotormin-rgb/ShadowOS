import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";


import entry, {
  compactActivity,
  databasePath,
  groceryArguments,
  normalizePhone,
  runGrocery,
} from "./index.js";

const temporaryPaths: string[] = [];

afterEach(async () => {
  await Promise.all(temporaryPaths.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

describe("grocery-list-tool", () => {
  it("exposes the capabilities a new member needs", () => {
    // Without help and onboard reachable, someone added to the allowlist has
    // no way to discover what to say.
    const args = (action: string, extra: Record<string, unknown> = {}) =>
      groceryArguments({ action, ...extra } as never, "/tmp/x.sqlite3", "+15551234567");

    expect(args("help")).toContain("help");
    expect(args("onboard")).toContain("onboard");
    expect(args("due", { section: "produce" })).toEqual(
      expect.arrayContaining(["due", "--section", "produce", "--format", "text"]),
    );
    expect(args("layout", { walkOrder: "warehouse" })).toEqual(
      expect.arrayContaining(["layout", "--set", "warehouse"]),
    );
    expect(args("help", { lang: "pt" })).toEqual(
      expect.arrayContaining(["--lang", "pt"]),
    );
  });

  it("does not expose administrative actions", () => {
    // Sharing, contact management, and roster edits must not be reachable by a
    // household member over WhatsApp.
    const forbidden = ["share", "allow", "delivered", "members", "who", "group"];
    for (const action of forbidden) {
      const args = groceryArguments({ action } as never, "/tmp/x.sqlite3", "+1555");
      expect(args, `${action} must not be routable`).not.toContain(action);
    }
  });

  it("identifies the caller on every action", () => {
    // The engine resolves reply language and household membership from
    // --actor, and refuses an unidentified caller once members are enrolled.
    // Omitting it on read actions took `list` down before this test existed.
    const actions = [
      "add", "list", "buy", "unbuy", "remove", "close", "reopen", "history",
      "stores", "due", "layout", "help", "onboard", "activity",
    ] as const;
    for (const action of actions) {
      const args = groceryArguments(
        {
          action,
          items: [{ name: "milk" }],
          names: ["milk"],
        } as never,
        "/tmp/x.sqlite3",
        "+15551234567",
      );
      expect(args, `action=${action} must pass --actor`).toContain("--actor");
      expect(args[args.indexOf("--actor") + 1]).toBe("+15551234567");
    }
  });

  it("builds the activity query with the sender as the only actor", () => {
    // `by` filters whose changes to show; it must never become who is asking.
    const args = groceryArguments(
      {
        action: "activity",
        since: "7d",
        until: "today",
        by: "Alex",
        item: "leite",
        store: "Costco",
        changeType: "purchased",
        limit: 30,
        lang: "en",
        actor: "+19999999999",
      } as never,
      "/tmp/x.sqlite3",
      "+15551234567",
    );
    expect(args.slice(0, 3)).toEqual(["--db", "/tmp/x.sqlite3", "activity"]);
    expect(args).toEqual(
      expect.arrayContaining([
        "--since", "7d", "--until", "today", "--by", "Alex", "--item", "leite",
        "--store", "Costco", "--action", "purchased", "--limit", "30", "--lang", "en",
      ]),
    );
    expect(args.filter((arg) => arg === "--actor")).toHaveLength(1);
    expect(args[args.indexOf("--actor") + 1]).toBe("+15551234567");
    expect(args).not.toContain("+19999999999");
    expect(args).not.toContain("--format");
  });

  it("refuses activity filters that would read as flags", () => {
    for (const field of ["since", "until", "by", "item"]) {
      expect(
        () =>
          groceryArguments(
            { action: "activity", [field]: "--db" } as never,
            "/tmp/x.sqlite3",
            "+15551234567",
          ),
        `${field} starting with a dash`,
      ).toThrow();
    }
  });

  describe("item names for buy, unbuy and remove", () => {
    const positionals = (extra: Record<string, unknown>, action = "buy") => {
      const args = groceryArguments(
        { action, ...extra } as never, "/tmp/x.sqlite3", "+15551234567",
      );
      expect(args.slice(0, 3)).toEqual(["--db", "/tmp/x.sqlite3", action]);
      return args.slice(3, args.indexOf("--source-type"));
    };

    it("takes names", () => {
      expect(positionals({ names: ["leite", "pão"] })).toEqual(["leite", "pão"]);
    });

    it("takes items[].name, as add sends it", () => {
      for (const action of ["buy", "unbuy", "remove"]) {
        expect(positionals({ items: [{ name: "leite" }, { name: "ovos" }] }, action), action)
          .toEqual(["leite", "ovos"]);
      }
    });

    it("takes item, as activity sends it", () => {
      expect(positionals({ item: "leite" })).toEqual(["leite"]);
    });

    it("takes items alongside rawText and keeps the raw text", () => {
      const args = groceryArguments(
        { action: "buy", rawText: "comprei leite", items: [{ name: "leite" }] } as never,
        "/tmp/x.sqlite3",
        "+15551234567",
      );
      expect(args.slice(3, args.indexOf("--source-type"))).toEqual(["leite"]);
      expect(args[args.indexOf("--raw-text") + 1]).toBe("comprei leite");
    });

    it("prefers names over items over item", () => {
      expect(
        positionals({ names: ["leite"], items: [{ name: "ovos" }], item: "café" }),
      ).toEqual(["leite"]);
      expect(positionals({ items: [{ name: "ovos" }], item: "café" })).toEqual(["ovos"]);
    });

    it("never acts on a placeholder name", () => {
      for (const junk of ["placeholder", "Item", "x", "X", "name", "", "  "]) {
        for (const action of ["buy", "unbuy", "remove"]) {
          for (const shape of [{ names: [junk] }, { items: [{ name: junk }] }, { item: junk }]) {
            expect(
              () => groceryArguments(
                { action, scope: "family", ...shape } as never, "/tmp/x.sqlite3", "+15551234567",
              ),
              `${action} ${JSON.stringify(shape)}`,
            ).toThrow(`names are required for action=${action}`);
          }
        }
      }
    });

    it("drops placeholders but keeps the real names beside them", () => {
      expect(positionals({ items: [{ name: "item" }, { name: "leite" }] }, "remove"))
        .toEqual(["leite"]);
      // A names list of only filler does not hide a real item elsewhere.
      expect(positionals({ names: ["placeholder"], items: [{ name: "leite" }] }))
        .toEqual(["leite"]);
    });

    it("refuses a name that would read as a flag, from any field", () => {
      for (const bad of ["-rf", "--db=/tmp/x", "--actor", " -x"]) {
        for (const action of ["buy", "unbuy", "remove"]) {
          const shapes = [
            { names: [bad] },
            { names: ["leite", bad] },
            { items: [{ name: bad }] },
            { items: [{ name: "leite" }, { name: bad }] },
            { item: bad },
          ];
          for (const shape of shapes) {
            expect(
              () => groceryArguments(
                { action, ...shape } as never, "/tmp/x.sqlite3", "+15551234567",
              ),
              `${action} ${JSON.stringify(shape)}`,
            ).toThrow('names must not begin with "-"');
          }
        }
      }
      // A dash inside a name is an ordinary product name.
      expect(positionals({ names: ["pão-de-queijo"] })).toEqual(["pão-de-queijo"]);
    });

    it("still requires a name when none is sent", () => {
      expect(() => positionals({})).toThrow("names are required for action=buy");
      expect(() => positionals({ items: [] }, "unbuy")).toThrow("names are required for action=unbuy");
    });

    it("leaves placeholder filler on read actions harmless", () => {
      for (const action of ["list", "help", "activity", "history"]) {
        const args = groceryArguments(
          { action, items: [{ name: "placeholder" }] } as never, "/tmp/x.sqlite3", "+15551234567",
        );
        expect(args, action).not.toContain("placeholder");
      }
    });
  });

  it("says where a buy, unbuy or remove came from", () => {
    for (const action of ["buy", "unbuy", "remove"]) {
      const args = groceryArguments(
        {
          action,
          names: ["leite"],
          sourceType: "voice",
          sourceRef: "wa:voice-9",
          rawText: "comprei o leite",
        } as never,
        "/tmp/x.sqlite3",
        "+15551234567",
      );
      expect(args, action).toEqual(
        expect.arrayContaining([
          "--source-type", "voice", "--source-ref", "wa:voice-9",
          "--raw-text", "comprei o leite",
        ]),
      );
      const plain = groceryArguments(
        { action, names: ["leite"] } as never, "/tmp/x.sqlite3", "+15551234567",
      );
      expect(plain[plain.indexOf("--source-type") + 1], action).toBe("text");
    }
  });

  it("hands the agent the rendered activity, not every row behind it", () => {
    // At limit=100 the engine's JSON is ~95 KB plus the same content as text;
    // the agent relays `text`, so the rows only cost tokens.
    const engine = {
      timezone: "America/Sao_Paulo",
      household_timezone: "America/New_York",
      since: null,
      until: null,
      by: "Alex",
      by_matched: true,
      item: null,
      store: null,
      action: null,
      limit: 100,
      count: 2,
      truncated: true,
      entries: [{ item: "Leite" }, { item: "Pão" }],
      text: "Atividade da lista\n\n*hoje*\n- 10:00 · Alex adicionou Leite · Costco",
    };
    const compact = compactActivity(engine);
    expect(compact).not.toHaveProperty("entries");
    expect(compact).toEqual({
      text: engine.text,
      count: 2,
      truncated: true,
      limit: 100,
      by_matched: true,
      timezone: "America/Sao_Paulo",
    });
  });



  it("normalizes phone identities and separates private databases", () => {
    const config = { familyDbPath: "/data/family.sqlite3", privateDbDir: "/data/private" };
    expect(normalizePhone("+1 (202) 555-0101")).toBe("+12025550101");
    expect(databasePath("family", "+12025550101", config)).toBe("/data/family.sqlite3");
    expect(databasePath("private", "+12025550101", config)).not.toBe(
      databasePath("private", "+12025550166", config),
    );
  });

  it("builds fixed argv without a shell", () => {
    const args = groceryArguments(
      {
        action: "add",
        store: "Trader Joe's",
        items: [{ name: "Milk", quantity: 2, note: "2%" }],
      },
      "/tmp/family.sqlite3",
      "+12025550101",
    );
    expect(args).toContain("ingest");
    expect(args).toContain("--items-json");
    expect(args).toContain("+12025550101");
  });

  it("passes product metadata to the local backend without fetching", () => {
    const args = groceryArguments(
      {
        action: "add",
        store: "Amazon",
        sourceType: "url",
        sourceRef: "https://amazon.com/gp/product/B012345678",
        items: [{
          name: "Coffee beans",
          productUrl: "https://www.amazon.com/dp/B012345678",
        }],
      },
      "/tmp/family.sqlite3",
      "+12025550101",
    );
    const payload = JSON.parse(args[args.indexOf("--items-json") + 1]);
    expect(payload).toEqual([{
      name: "Coffee beans",
      productUrl: "https://www.amazon.com/dp/B012345678",
    }]);
    expect(args).toContain("url");
  });

  it("runs the real Python backend and defaults to family scope", async () => {
    const root = await mkdtemp(join(tmpdir(), "grocery-plugin-"));
    temporaryPaths.push(root);
    const result = await runGrocery(
      { action: "add", store: "Costco", items: [{ name: "Eggs", quantity: 2 }] },
      "+12025550101",
      {
        pythonPath: "/usr/bin/python3",
        scriptPath: join(process.cwd(), "..", "core", "grocery.py"),
        familyDbPath: join(root, "family.sqlite3"),
        privateDbDir: join(root, "private"),
        whatsappAccountId: "tools",
        allowedRequesters: ["+12025550101"],
      },
    );
    expect(result).toMatchObject({ scope: "family", store: "Costco", added: ["Eggs"] });

    const feed = await runGrocery(
      { action: "activity", since: "today", lang: "en" },
      "+12025550101",
      {
        pythonPath: "/usr/bin/python3",
        scriptPath: join(process.cwd(), "..", "core", "grocery.py"),
        familyDbPath: join(root, "family.sqlite3"),
        privateDbDir: join(root, "private"),
        whatsappAccountId: "tools",
        allowedRequesters: ["+12025550101"],
      },
    );
    expect(feed).toMatchObject({ scope: "family", count: 1 });
    expect(feed).not.toHaveProperty("entries");
    expect(String(feed.text)).toContain("added Eggs x2");
    // Nobody is named in this database, so the sender is masked, not printed.
    expect(JSON.stringify(feed)).not.toContain("12025550101");
  });

  it("rejects a requester outside the plugin allowlist", async () => {
    await expect(
      runGrocery(
        { action: "stores" },
        "+12025550199",
        {
          pythonPath: "/usr/bin/python3",
          scriptPath: "/tmp/grocery.py",
          familyDbPath: "/tmp/family.sqlite3",
          privateDbDir: "/tmp/private",
          whatsappAccountId: "tools",
          allowedRequesters: ["+12025550101"],
        },
      ),
    ).rejects.toThrow("not authorized");
  });
});
