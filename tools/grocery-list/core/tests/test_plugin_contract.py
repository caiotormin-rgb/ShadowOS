"""Contract tests between the OpenClaw plugin and the grocery CLI.

The plugin at ``tools/grocery-list/plugin/src/index.ts`` builds an argv list as text,
shells out to ``grocery.py`` with it, and then ``JSON.parse``s stdout. Nothing
on either side verifies that the argv it builds is accepted by the parser it
targets, or that the commands it treats as prose are the ones that actually
print prose -- and both have already drifted in production-shaped ways.

So this file reads the plugin source *as text*: no node, no TypeScript import,
no build step. It recovers the actions the plugin can emit and the flags it
attaches to each, then holds them against ``grocery.build_parser()`` and, for
the output-shape check, against the real CLI running on a temporary database.

When this file fails, the failure names the action and the flag. That is the
whole point: the next person to add a CLI flag or a plugin action should find
out here, not from a family whose list stopped answering.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import grocery
import sections

COMPONENT = Path(__file__).resolve().parent.parent
TOOL = COMPONENT.parent
PLUGIN_SOURCE = TOOL / "plugin" / "src" / "index.ts"
SCRIPT = COMPONENT / "grocery.py"

# Values the plugin has no reason to send but the parser demands; keyed by flag.
SAMPLES = {
    "--items-json": json.dumps([{"name": "milk", "quantity": 2, "unit": "L"}]),
    "--actor": "+15550001111",
    "--store": "Contract Market",
    "--unit": "L",
    "--source-ref": "wa:contract-test",
    "--raw-text": "milk",
    "--since": "7d",
    "--until": "today",
    "--by": "Alex",
    "--item": "milk",
}

# Operator-only commands, per TRUST-BOUNDARY.md. A household member reaching one
# of these over WhatsApp is the security failure that document describes, so the
# boundary is checked twice below: once on the action names the schema offers,
# and once on the argv those actions actually build. Checking only the names
# misses a case rewired to push an administrative command while the enum still
# reads as thirteen innocent verbs.
ADMINISTRATIVE = frozenset({
    "group", "who", "allow", "members", "share", "delivered",
})

# Every subcommand the plugin can currently reach. Pinned, because
# TRUST-BOUNDARY.md names "a new plugin action is added" as the way an
# administrative command becomes reachable: widening this set should be an edit
# someone makes on purpose and another person reads, not a side effect.
REACHABLE = frozenset({
    "ingest", "list", "buy", "unbuy", "remove", "close", "reopen",
    "history", "stores", "due", "layout", "help", "onboard",
    # Added deliberately, per TRUST-BOUNDARY.md: read-only, keyed on the
    # caller's household, and it prints people by name, never by number.
    "activity",
})

# Member actions that only read. Pinned so a later edit that makes one of them
# write is caught here, not by a household noticing their list changed.
READ_ONLY = frozenset({"activity", "history", "stores"})


# --------------------------------------------------------------------------
# Reading the plugin source as text
# --------------------------------------------------------------------------

def plugin_source() -> str:
    return PLUGIN_SOURCE.read_text(encoding="utf-8")


def declared_actions(src: str) -> list[str]:
    """The action literals the tool schema accepts, in declaration order."""
    block = re.search(r"action:\s*Type\.Union\(\s*\[(.*?)\]\s*\)", src, re.S)
    assert block, "could not find the action union in the plugin's parameter schema"
    return re.findall(r'Type\.Literal\("([^"]+)"\)', block.group(1))


def text_actions(src: str) -> set[str]:
    """Actions whose stdout the plugin returns verbatim instead of JSON.parsing."""
    block = re.search(r"const TEXT_ACTIONS\s*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    assert block, "could not find TEXT_ACTIONS in the plugin source"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def declared_union(src: str, field: str) -> list[str]:
    """The literals an optional Type.Union parameter accepts, in order."""
    block = re.search(
        rf"{field}:\s*Type\.Optional\(\s*Type\.Union\(\s*\[(.*?)\]", src, re.S)
    return re.findall(r'Type\.Literal\("([^"]+)"\)', block.group(1)) if block else []


def switch_body(src: str) -> str:
    """The body of groceryArguments' switch, where every argv is assembled."""
    start = src.index("switch (params.action)")
    return src[start:src.index("return args;", start)]


def action_blocks(src: str) -> dict[str, str]:
    """Map each action to the case body that builds its argv.

    Consecutive labels with no body of their own fall through to the next
    block, exactly as they do in TypeScript -- that is how buy/unbuy/remove
    and help/onboard share their construction.
    """
    body = switch_body(src)
    labels = [(m.start(), m.end(), m.group(1))
              for m in re.finditer(r'case "([a-z]+)":', body)]
    # A case ends where the *next* label begins, not where that label ends --
    # slice it the other way and an empty case swallows the label after it and
    # never looks like the fall-through it is.
    bounds = [start for start, _, _ in labels[1:]] + [len(body)]

    blocks: dict[str, str] = {}
    pending: list[str] = []
    for index, (_, end, name) in enumerate(labels):
        chunk = body[end:bounds[index]]
        if not chunk.strip():
            pending.append(name)
            continue
        for shared in [*pending, name]:
            blocks[shared] = chunk
        pending = []
    assert not pending, f"trailing fall-through cases with no body: {pending}"
    return blocks


def subcommand_for(action: str, block: str) -> str:
    """The CLI subcommand this action shells out to."""
    literal = re.search(r'args\.push\("([a-z-]+)"\)', block)
    if literal:
        return literal.group(1)
    if "args.push(params.action)" in block:
        return action
    raise AssertionError(f"action {action!r} pushes no recognizable subcommand")


def flags_for(block: str) -> list[tuple[str, str | None, bool]]:
    """Every flag this case can emit, as (flag, pinned value, conditional).

    A pinned value is one the plugin hard-codes -- ``--format text`` -- rather
    than forwarding from its caller. Conditional flags sit behind an ``if`` or
    come from the addStore/addLang helpers, so they are absent when the model
    supplies no such parameter.
    """
    found: list[tuple[str, str | None, bool]] = []
    for line in block.splitlines():
        conditional = "if (" in line
        for match in re.finditer(r'"(--[a-z-]+)"(?:\s*,\s*"([^"]*)")?', line):
            found.append((match.group(1), match.group(2), conditional))
    if "addStore()" in block:
        found.append(("--store", None, True))
    if "addLang()" in block:
        found.append(("--lang", None, True))
    return found


def takes_positionals(block: str) -> bool:
    return "requireNames(params)" in block


# --------------------------------------------------------------------------
# Reading the parser
# --------------------------------------------------------------------------

def parses(parser: argparse.ArgumentParser, argv: list[str]) -> bool:
    """Whether the parser accepts this argv, without its usage dump."""
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            parser.parse_args(argv)
            return True
        except SystemExit:
            return False


def subcommands() -> dict[str, argparse.ArgumentParser]:
    """The CLI's subparsers. argparse exposes no public accessor for these."""
    for action in grocery.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("build_parser() grew no subcommands")


def options_of(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {opt: action for action in parser._actions for opt in action.option_strings}


def sample_for(flag: str, action: argparse.Action) -> list[str]:
    """A value the parser will accept for this flag, or nothing if it is a switch."""
    if action.nargs == 0:
        return []
    if flag in SAMPLES:
        return [SAMPLES[flag]]
    if action.choices:
        return [str(list(action.choices)[0])]
    if action.type in (int, float):
        return ["1"]
    return ["sample"]


def argv_for(action: str, block: str, *, include_optional: bool) -> list[str]:
    """Rebuild the argv the plugin would hand to execFile for this action."""
    command = subcommand_for(action, block)
    parser = subcommands()[command]
    options = options_of(parser)

    argv = ["--db", "PLACEHOLDER", command]
    if takes_positionals(block):
        argv.append("milk")
    for flag, pinned, conditional in flags_for(block):
        if conditional and not include_optional:
            continue
        if flag not in options:      # reported by its own test; skip to stay specific
            continue
        argv.append(flag)
        argv.extend([pinned] if pinned is not None else sample_for(flag, options[flag]))
    return argv


class PluginContractTests(unittest.TestCase):
    """Every assertion here reads the plugin source fresh, so it cannot go stale."""

    @classmethod
    def setUpClass(cls):
        cls.src = plugin_source()
        cls.actions = declared_actions(cls.src)
        cls.blocks = action_blocks(cls.src)
        cls.text_actions = text_actions(cls.src)

    def test_every_declared_action_builds_arguments(self):
        """A schema literal with no case in the switch emits a bare argv."""
        for action in self.actions:
            with self.subTest(action=action):
                self.assertIn(
                    action, sorted(self.blocks),
                    f"the plugin accepts action={action!r} but groceryArguments has "
                    f"no case for it, so it would shell out with no subcommand",
                )

    def test_every_action_maps_to_a_real_subcommand(self):
        available = subcommands()
        for action in self.actions:
            with self.subTest(action=action):
                command = subcommand_for(action, self.blocks[action])
                self.assertIn(
                    command, available,
                    f"action={action!r} shells out to `{command}`, which "
                    f"build_parser() does not define",
                )

    def test_every_flag_exists_on_its_subcommand(self):
        """The failure that already happened once: a flag the engine dropped."""
        available = subcommands()
        for action in self.actions:
            block = self.blocks[action]
            command = subcommand_for(action, block)
            options = options_of(available[command])
            for flag, _, _ in flags_for(block):
                with self.subTest(action=action, flag=flag):
                    self.assertIn(
                        flag, sorted(options),
                        f"action={action!r} passes {flag} to `{command}`, which "
                        f"does not accept it; every call would exit 2",
                    )

    def test_every_action_identifies_its_caller(self):
        """--actor carries the household and the reply language. Omitting it
        on a read command is what nearly took `list` down for everyone."""
        for action in self.actions:
            with self.subTest(action=action):
                flags = {flag for flag, _, _ in flags_for(self.blocks[action])}
                self.assertIn(
                    "--actor", flags,
                    f"action={action!r} never passes --actor, so the engine "
                    f"cannot tell who is asking or which household they are in",
                )

    def test_actor_is_unconditional(self):
        """Identity must not sit behind an `if`; it is required, not optional."""
        for action in self.actions:
            with self.subTest(action=action):
                unconditional = {
                    flag for flag, _, conditional in flags_for(self.blocks[action])
                    if not conditional
                }
                self.assertIn(
                    "--actor", unconditional,
                    f"action={action!r} passes --actor only conditionally; it "
                    f"must go on every call",
                )

    # ---------------------------------------------------------------
    # The trust boundary. The vitest suite guards the enum at the source;
    # these guard the reach, in the suite that runs by default.
    # ---------------------------------------------------------------

    def reached_subcommands(self) -> dict[str, str]:
        """Every action mapped to the subcommand its argv actually invokes."""
        return {action: subcommand_for(action, self.blocks[action])
                for action in self.actions if action in self.blocks}

    def test_no_action_is_named_for_an_administrative_command(self):
        """The enum is the boundary: members must not be offered these at all."""
        offered = sorted(set(self.actions) & ADMINISTRATIVE)
        if offered:
            self.fail(
                f"the plugin's action enum now offers {offered}, which "
                f"TRUST-BOUNDARY.md lists as operator-only; a household member "
                f"over WhatsApp could invoke it"
            )

    def test_no_action_reaches_an_administrative_command(self):
        """And the argv is the boundary too: an innocent-looking action name
        proves nothing if its case pushes an operator-only subcommand."""
        leaks = {action: command
                 for action, command in self.reached_subcommands().items()
                 if command in ADMINISTRATIVE}
        if leaks:
            self.fail("; ".join(
                f"action={action!r} builds argv for `{command}`, an "
                f"operator-only command TRUST-BOUNDARY.md says members cannot "
                f"reach"
                for action, command in sorted(leaks.items())
            ))

    def test_reachable_subcommands_are_the_pinned_set(self):
        """Widening what the plugin can invoke should be deliberate."""
        reached = set(self.reached_subcommands().values())
        if reached != set(REACHABLE):
            self.fail(
                f"what the plugin can invoke has changed -- newly reachable: "
                f"{sorted(reached - REACHABLE)}, no longer reachable: "
                f"{sorted(REACHABLE - reached)}. If that is intended, update "
                f"REACHABLE in this file, and check the new command is one a "
                f"household member should be able to run."
            )

    def test_minimal_argv_parses(self):
        """What the plugin sends when the model supplies no optional parameters."""
        parser = grocery.build_parser()
        for action in self.actions:
            with self.subTest(action=action):
                argv = argv_for(action, self.blocks[action], include_optional=False)
                self.assertTrue(
                    parses(parser, argv),
                    f"action={action!r} builds argv the parser rejects: {argv}",
                )

    def test_argv_with_every_optional_flag_parses(self):
        """And what it sends when the model supplies all of them."""
        parser = grocery.build_parser()
        for action in self.actions:
            with self.subTest(action=action):
                argv = argv_for(action, self.blocks[action], include_optional=True)
                self.assertTrue(
                    parses(parser, argv),
                    f"action={action!r} builds argv the parser rejects: {argv}",
                )

    def test_section_literals_match_the_cli(self):
        """`--section` is a closed set on both sides; it must be the same set."""
        offered = declared_union(self.src, "section")
        self.assertTrue(
            offered,
            "the plugin no longer enumerates sections, so the model can emit an "
            "aisle name of its own invention and argparse will exit 2 on it",
        )
        self.assertEqual(
            set(offered), set(sections.SECTION_KEYS),
            f"the plugin's section literals and sections.SECTION_KEYS have "
            f"drifted; only in the plugin: "
            f"{sorted(set(offered) - set(sections.SECTION_KEYS))}, only in the "
            f"CLI: {sorted(set(sections.SECTION_KEYS) - set(offered))}",
        )

    def test_every_offered_section_parses(self):
        parser = grocery.build_parser()
        for key in declared_union(self.src, "section"):
            with self.subTest(section=key):
                self.assertTrue(
                    parses(parser,
                           ["due", "--section", key, "--actor", SAMPLES["--actor"]]),
                    f"the plugin may send --section {key}, which `due` rejects",
                )

    def test_walk_orders_match_the_cli_layouts(self):
        offered = declared_union(self.src, "walkOrder")
        self.assertTrue(offered, "could not read the walkOrder union from the plugin")
        self.assertEqual(
            set(offered), set(sections.LAYOUTS),
            "the plugin offers walk orders the CLI does not define, or vice versa",
        )


class PluginOutputShapeTests(unittest.TestCase):
    """The half of the contract argparse cannot see.

    The plugin returns stdout verbatim for TEXT_ACTIONS and ``JSON.parse``s
    everything else. If a command prints prose and is not declared there, the
    plugin throws a SyntaxError at runtime and the family sees only a generic
    "grocery operation failed" -- strictly worse than failing here.
    """

    # Some actions only mean anything against a list in a particular state.
    # These run first, so what is under test is the argv and nothing else.
    PRECONDITIONS = {
        "close": [["buy", "milk"]],
        "reopen": [["buy", "milk"], ["close"]],
    }

    @classmethod
    def setUpClass(cls):
        cls.src = plugin_source()
        cls.actions = declared_actions(cls.src)
        cls.blocks = action_blocks(cls.src)
        cls.text_actions = text_actions(cls.src)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, db: str, argv: list[str]) -> subprocess.CompletedProcess:
        """Run argv verbatim. Nothing is added -- an argv missing --actor must
        fail here exactly as it would in production."""
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--db", db, *argv],
            capture_output=True, text=True, timeout=60,
        )

    def seeded_db(self, action: str) -> str:
        """A fresh list holding one item, plus whatever this action presumes."""
        db = str(Path(self.temp.name) / f"{action}.sqlite3")
        self.run_cli(db, ["init"])
        self.run_cli(db, [
            "ingest", "--store", SAMPLES["--store"],
            "--source-type", "text", "--source-ref", "", "--raw-text", "milk",
            "--items-json", SAMPLES["--items-json"], "--actor", SAMPLES["--actor"],
        ])
        for step in self.PRECONDITIONS.get(action, []):
            result = self.run_cli(
                db, [*step, "--store", SAMPLES["--store"], "--actor", SAMPLES["--actor"]])
            self.assertEqual(
                result.returncode, 0,
                f"could not set up {action!r}: `{' '.join(step)}` exited "
                f"{result.returncode}: {result.stderr.strip()}",
            )
        return db

    def invoke(self, action: str) -> subprocess.CompletedProcess:
        """Run one action exactly as the plugin sends it, on a real database."""
        argv = argv_for(action, self.blocks[action], include_optional=False)[2:]
        return self.run_cli(self.seeded_db(action), argv)

    def test_plugins_minimal_argv_runs(self):
        """Every action, as the plugin sends it, against a real database."""
        for action in self.actions:
            with self.subTest(action=action):
                argv = argv_for(action, self.blocks[action], include_optional=False)[2:]
                result = self.invoke(action)
                self.assertEqual(
                    result.returncode, 0,
                    f"action={action!r} exited {result.returncode}: "
                    f"{result.stderr.strip()}\nargv: {argv}",
                )

    def test_read_only_actions_change_nothing(self):
        """activity is a new member action; reading history must not write it."""
        tables = ("events", "items", "trips", "trip_items", "people", "stores",
                  "group_members")
        for action in sorted(READ_ONLY):
            with self.subTest(action=action):
                self.assertIn(action, self.actions,
                              f"{action!r} is pinned read-only but not offered")
                db = self.seeded_db(action)

                def snapshot():
                    conn = sqlite3.connect(db)
                    try:
                        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                                for t in tables}
                    finally:
                        conn.close()

                before = snapshot()
                argv = argv_for(action, self.blocks[action], include_optional=False)[2:]
                result = self.run_cli(db, argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(snapshot(), before,
                                 f"action={action!r} changed the database")

    def test_activity_output_names_no_phone(self):
        """The one new member-reachable read, as the plugin sends it."""
        db = self.seeded_db("activity")
        argv = argv_for("activity", self.blocks["activity"], include_optional=False)[2:]
        result = self.run_cli(db, argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["entries"], "the seeded add should appear")
        self.assertNotIn(SAMPLES["--actor"].lstrip("+"), result.stdout)

    def test_text_actions_are_exactly_the_commands_that_print_prose(self):
        """The check that catches prose being handed to JSON.parse."""
        for action in self.actions:
            with self.subTest(action=action):
                argv = argv_for(action, self.blocks[action], include_optional=False)[2:]
                result = self.invoke(action)
                if result.returncode != 0:
                    self.skipTest("covered by test_plugins_minimal_argv_runs")

                try:
                    json.loads(result.stdout)
                    prints_json = True
                except json.JSONDecodeError:
                    prints_json = False

                first = result.stdout.strip().splitlines()
                opening = first[0] if first else "<empty>"

                if action in self.text_actions and prints_json:
                    self.fail(
                        f"action={action!r} is in TEXT_ACTIONS, so the plugin "
                        f"returns its stdout as one opaque string, but "
                        f"`{' '.join(argv)}` prints JSON. Callers lose every "
                        f"parsed field. Drop {action!r} from TEXT_ACTIONS in "
                        f"tools/grocery-list/plugin/src/index.ts."
                    )
                if action not in self.text_actions and not prints_json:
                    self.fail(
                        f"action={action!r} is not in TEXT_ACTIONS, so the "
                        f"plugin JSON.parses it, but `{' '.join(argv)}` prints "
                        f"prose:\n  {opening}\nEvery call throws. Add "
                        f"{action!r} to TEXT_ACTIONS in "
                        f"tools/grocery-list/plugin/src/index.ts, or stop sending "
                        f"--format text for it."
                    )


if __name__ == "__main__":
    unittest.main()
