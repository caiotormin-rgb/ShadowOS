# Doctor research model boundary

`core/llm.py` previously selected shared-tools without an explicit model when
DOCTOR_LLM_MODEL was absent. Changing household chat could change research.
Research now always passes --model, defaulting to openai/gpt-5.6-sol, including
when the environment value is blank. DOCTOR_LLM_AGENT still selects credentials
and policy and defaults to shared-tools for compatibility.

For a separate worker use DOCTOR_LLM_AGENT=doctor-research and explicitly set
DOCTOR_LLM_MODEL=openai/gpt-5.6-sol in its service. The model must be callable
and permitted. Evaluate research separately from Grocery. Existing intake,
requester isolation, evidence extraction and /ok email approval are unchanged.
No implicit cross-model fallback is introduced.

Tests in test_llm_model.py intercept subprocess execution to check default,
blank, and explicit selections. The full Doctor suite covers curation and
outreach. These tests perform no external searches or sends.

Rollback: restore the previous llm.py only if the chat model still has the
intended research quality; otherwise retain explicit worker environment pins.
See DEPLOYMENT.md for actual deployment status.
