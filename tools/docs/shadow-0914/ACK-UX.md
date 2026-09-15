# Native mode acknowledgements

The WhatsApp screenshot showed `/lista` returning a long Portuguese/English
paragraph and an unsolicited pitch for Doctor mode. The user then asked for a
more conversational style with emoji. Native acknowledgements now use one
domain emoji and a brief invitation in the explicit command alias language:

| Alias | Successful reply |
|---|---|
| `/lista` | 🛒 Lista ativa. O que vamos comprar? |
| `/groceries` | 🛒 Groceries active. What do we need? |
| `/medico` | 🩺 Busca de médicos ativa. Como posso ajudar? |
| `/doctor` | 🩺 Doctor search active. How can I help? |

Command errors also use the alias language. The internal command method accepts
an optional locale argument (default English for compatibility with callers);
registration supplies Portuguese for lista/medico and English otherwise.
Acknowledgements have no effect on the person's persisted Grocery preferences.

Validation: router syntax checks and 13 tests pass, including exact alias output,
64-character success limit and no translation/mode pitch. The two actual-host
callback integration tests also pass. This patch addresses reply UX only;
it does not claim to resolve the separately investigated routing loop. No live
deploy or user message was performed by this lane.
