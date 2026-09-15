# Canonical household Grocery instructions

`SKILL.md` is the maintained source for the restricted household Grocery agent.
It describes the seven narrow tools and the compact reply contract introduced
2026-09-14. It must be deployed together with the corresponding plugin, backend,
and tool allowlist. The old `grocery_list` adapter is compatibility only.

Do not maintain a second copy of these behavioral instructions in an agent's
AGENTS.md. Keep the agent identity, permissions and routing rules there; generate
or copy this skill from source as part of deployment. The rollout manifest in
`docs/shadow-0914/` is the authority for destination and allowlist. Source edits
alone do not update a running agent.

The owner's broader grocery skill is separate and is not managed here. A
restricted household skill must never inherit the owner's filesystem or shell
instructions. Neither this plugin nor the backend fetches product URLs.
