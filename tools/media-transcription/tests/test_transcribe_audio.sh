#!/usr/bin/env bash
# Tests for tools/media-transcription/bin/transcribe-audio-local.
#
#   tools/media-transcription/tests/test_transcribe_audio.sh
#
# Real-speech tests use local files named by env vars and are skipped when
# unset or absent (voice notes are private and never live in git):
#   TRANSCRIBE_TEST_PT=/path/to/portuguese.ogg  TRANSCRIBE_TEST_PT_EXPECT='regex'
#   TRANSCRIBE_TEST_EN=/path/to/english.ogg     TRANSCRIBE_TEST_EN_EXPECT='regex'
# The *_EXPECT regexes are optional (case-insensitive, extended).
# Placeholder stripping, the language fallback and failure paths use a fake
# whisper-cli, so they need neither models nor samples.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
tool_dir="$(cd "$here/../bin" && pwd)"
script="${TRANSCRIBE_SCRIPT:-$tool_dir/transcribe-audio-local}"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

passed=0
failed=0
skipped=0
ok() { printf 'ok   %s\n' "$1"; passed=$((passed + 1)); }
bad() { printf 'FAIL %s: %s\n' "$1" "$2"; failed=$((failed + 1)); }
skip() { printf 'skip %s: %s\n' "$1" "$2"; skipped=$((skipped + 1)); }

# run [env assignments...] -- args...   sets out, err, rc, secs
run() {
  local envs=()
  while [[ $# -gt 0 && "$1" != -- ]]; do envs+=("$1"); shift; done
  shift
  local start=$SECONDS
  out="$(env "${envs[@]}" "$script" "$@" 2>"$tmp/stderr")"
  rc=$?
  err="$(cat "$tmp/stderr")"
  secs=$((SECONDS - start))
}

has_placeholder() { [[ "$1" =~ \[|\((speaking|foreign|music) ]]; }

expect_failure() { # name
  if ((rc == 0)); then bad "$1" "exit 0, stdout: $out"; return; fi
  if [[ -n "$out" ]]; then bad "$1" "stdout not empty: $out"; return; fi
  if [[ -z "$err" ]]; then bad "$1" "no stderr message"; return; fi
  ok "$1 (exit $rc: $err)"
}

expect_transcript() { # name expect-regex
  if ((rc != 0)); then bad "$1" "exit $rc: $err"; return; fi
  if [[ -z "$out" ]]; then bad "$1" "empty transcript"; return; fi
  if has_placeholder "$out"; then bad "$1" "placeholder left in: $out"; return; fi
  if [[ -n "$2" ]] && ! grep -Eiq -- "$2" <<<"$out"; then bad "$1" "no match for /$2/: $out"; return; fi
  if ((secs >= 60)); then bad "$1" "took ${secs}s"; return; fi
  ok "$1 (${secs}s): $out"
}

# --- fixtures ---------------------------------------------------------------
silence="$tmp/silence.ogg"
gst-launch-1.0 -q audiotestsrc wave=silence num-buffers=200 ! audioconvert ! audioresample ! \
  audio/x-raw,rate=48000,channels=1 ! opusenc ! oggmux ! filesink location="$silence" >/dev/null 2>&1
[[ -s "$silence" ]] || { echo "could not generate silence fixture with gst" >&2; exit 1; }

head -c 16384 /dev/urandom >"$tmp/corrupt.ogg"
{ printf 'OggS'; head -c 8192 /dev/urandom; } >"$tmp/corrupt-header.ogg"


fake="$tmp/fake-whisper-cli"
cat >"$fake" <<'EOF'
#!/usr/bin/env bash
lang="" of="" model="" threads="" file=""
while [[ $# -gt 0 ]]; do
  case "$1" in -l) lang="$2"; shift ;; -of) of="$2"; shift ;; -m) model="$2"; shift ;; -t) threads="$2"; shift ;; -f) file="$2"; shift ;; esac
  shift
done
printf '%s\n' "$lang" >>"$FAKE_CALLS"
[[ -n "${FAKE_MODELS-}" ]] && printf '%s\n' "$model" >>"$FAKE_MODELS"
[[ -n "${FAKE_THREADS-}" ]] && printf '%s\n' "$threads" >>"$FAKE_THREADS"
if [[ -n "${FAKE_EVENTS-}" ]]; then
  printf '%s +1\n' "$(date +%s%N)" >>"$FAKE_EVENTS"
  sleep "${FAKE_HOLD:-1}"
  printf '%s -1\n' "$(date +%s%N)" >>"$FAKE_EVENTS"
fi
# Like real whisper, a run that hangs never writes its .txt.
if [[ -n "${FAKE_SLEEP_ON_CALL-}" ]] && (($(wc -l <"$FAKE_CALLS") >= FAKE_SLEEP_ON_CALL)); then
  exec sleep 60
fi
# Per-chunk overrides for long notes, keyed by the input file name without
# .wav (chunk000, chunk001, ...): FAKE_TEXT_<lang>_<chunk>,
# FAKE_DETECT_<chunk> (empty = no detection line), FAKE_SPEECH_S_<chunk>.
chunk="$(basename "$file" .wav)"
var="FAKE_TEXT_$lang"
cvar="FAKE_TEXT_${lang}_$chunk"
[[ -n "${!cvar+set}" ]] && var="$cvar"
if [[ -n "${FAKE_ECHO_FILE-}" ]]; then
  printf '%s %s.\n' "${!var-}" "$chunk" >"$of.txt"
else
  printf '%s\n' "${!var-}" >"$of.txt"
fi
detect="${FAKE_DETECT-}"
dvar="FAKE_DETECT_$chunk"
[[ -n "${!dvar+set}" ]] && detect="${!dvar}"
if [[ "$lang" == auto && -n "$detect" ]]; then
  echo "whisper_full_with_state: auto-detected language: $detect" >&2
fi
speech="${FAKE_SPEECH_S-}"
svar="FAKE_SPEECH_S_$chunk"
[[ -n "${!svar+set}" ]] && speech="${!svar}"
if [[ -n "$speech" ]]; then
  echo "whisper_vad: total duration of speech segments: $speech seconds" >&2
fi
if [[ -n "${FAKE_SLEEP-}" ]]; then
  exec sleep "$FAKE_SLEEP"
fi
exit "${FAKE_RC:-0}"
EOF
chmod +x "$fake"
: >"$tmp/fake-model.bin"
: >"$tmp/fake-vad.bin"
fake_env=(TRANSCRIBE_WHISPER_CLI="$fake" TRANSCRIBE_MODEL="$tmp/fake-model.bin" TRANSCRIBE_VAD=0
  TRANSCRIBE_VAD_MODEL="$tmp/fake-vad.bin" FAKE_CALLS="$tmp/calls")
# Keep every test's flock slots out of the real state directory.
export TRANSCRIBE_LOCK_DIR="$tmp/locks"

wait_for_proc() { # pattern: wait up to 10 s for a process to appear
  local i
  for i in $(seq 100); do
    pgrep -f "$1" >/dev/null && return 0
    sleep 0.1
  done
  return 1
}

# --- concurrency (item 1) -------------------------------------------------------
: >"$tmp/threads"
run "${fake_env[@]}" FAKE_THREADS="$tmp/threads" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
want_threads=$(($(nproc) / 2))
((want_threads >= 1)) || want_threads=1
((want_threads <= 6)) || want_threads=6
if ((rc == 0)) && [[ "$(head -n 1 "$tmp/threads")" == "$want_threads" ]]; then
  ok "default threads are min(6, nproc/2) = $want_threads"
else
  bad "default threads" "rc=$rc threads=[$(head -n 1 "$tmp/threads")] want $want_threads: $err"
fi

# Low: well-formed but out-of-range values are clamped, not trusted.
max_threads=$(nproc)
((max_threads <= 16)) || max_threads=16
: >"$tmp/threads"
run "${fake_env[@]}" TRANSCRIBE_THREADS=64 FAKE_THREADS="$tmp/threads" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
if ((rc == 0)) && [[ "$(head -n 1 "$tmp/threads")" == "$max_threads" && "$err" == *"TRANSCRIBE_THREADS=64 clamped to $max_threads"* ]]; then
  ok "TRANSCRIBE_THREADS=64 is clamped to min(16, nproc) = $max_threads"
else
  bad "threads clamp" "rc=$rc threads=[$(head -n 1 "$tmp/threads")] err=$err"
fi
run "${fake_env[@]}" TRANSCRIBE_BUDGET_S=120 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
if ((rc == 0)) && [[ "$err" == *"TRANSCRIBE_BUDGET_S=120 clamped to 53"* ]]; then
  ok "TRANSCRIBE_BUDGET_S=120 is clamped to 53 (under OpenClaw's 55 s timeout)"
else
  bad "budget clamp" "rc=$rc err=$err"
fi
run "${fake_env[@]}" TRANSCRIBE_BUDGET_S=53 TRANSCRIBE_THREADS=1 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
((rc == 0)) && [[ "$err" != *clamped* ]] && ok "in-range budget 53 and threads 1 are not clamped" ||
  bad "no clamp in range" "rc=$rc err=$err"

events="$tmp/events"
: >"$events"
conc_pids=()
for i in 1 2 3; do
  env "${fake_env[@]}" TRANSCRIBE_MAX_CONCURRENT=2 FAKE_EVENTS="$events" FAKE_HOLD=1.5 \
    FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz $i" \
    "$script" "$silence" >"$tmp/conc$i.out" 2>"$tmp/conc$i.err" &
  conc_pids+=($!)
done
conc_ok=1
for pid in "${conc_pids[@]}"; do wait "$pid" || conc_ok=0; done
max_overlap=$(sort -n "$events" | awk '{ c += $2; if (c > m) m = c } END { print m + 0 }')
if ((conc_ok)) && ((max_overlap == 2)) && (($(wc -l <"$events") == 6)); then
  ok "3 callers with a limit of 2: all succeed, at most 2 run whisper at once"
else
  bad "concurrency limit" "all_ok=$conc_ok max_overlap=$max_overlap events=$(wc -l <"$events") err=$(cat "$tmp"/conc*.err)"
fi

env "${fake_env[@]}" TRANSCRIBE_MAX_CONCURRENT=1 FAKE_SLEEP=43.271 FAKE_DETECT="pt (p = 0.990000)" \
  "$script" "$silence" >/dev/null 2>&1 &
holder=$!
if wait_for_proc '^sleep 43.271$'; then
  run "${fake_env[@]}" TRANSCRIBE_MAX_CONCURRENT=1 TRANSCRIBE_BUDGET_S=7 \
    FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz" -- "$silence"
  if ((rc == 5)) && [[ -z "$out" && "$err" == *busy* ]] && ((secs <= 8)); then
    ok "no free slot within the budget: exit 5 after ${secs}s (busy)"
  else
    bad "busy slot" "rc=$rc secs=$secs out=[$out] err=$err"
  fi
else
  bad "busy slot setup" "holder never started"
fi
kill -TERM "$holder" 2>/dev/null
wait "$holder" 2>/dev/null

if [[ -n "${TRANSCRIBE_TEST_PT-}" && -f "$TRANSCRIBE_TEST_PT" ]]; then
  real_budget="${TRANSCRIBE_TEST_CONCURRENT_BUDGET:-30}"
  real_pids=()
  real_start=$SECONDS
  for i in 1 2; do
    env TRANSCRIBE_BUDGET_S="$real_budget" "$script" "$TRANSCRIBE_TEST_PT" >"$tmp/real$i.out" 2>"$tmp/real$i.err" &
    real_pids+=($!)
  done
  real_rcs=()
  for pid in "${real_pids[@]}"; do
    wait "$pid"
    real_rcs+=($?)
  done
  real_secs=$((SECONDS - real_start))
  real_expect="${TRANSCRIBE_TEST_PT_EXPECT:-.}"
  if [[ "${real_rcs[*]}" == "0 0" ]] && grep -Eiq -- "$real_expect" "$tmp/real1.out" && grep -Eiq -- "$real_expect" "$tmp/real2.out"; then
    ok "2 concurrent real PT notes with a ${real_budget}s budget both succeed (${real_secs}s wall)"
  else
    bad "2 concurrent real PT notes" "rcs=${real_rcs[*]} secs=$real_secs err1=$(cat "$tmp/real1.err") err2=$(cat "$tmp/real2.err")"
  fi
else
  skip "2 concurrent real PT notes" "set TRANSCRIBE_TEST_PT to a local voice note"
fi

# --- input validation --------------------------------------------------------
run -- ; expect_failure "no arguments"
run -- "$tmp/does-not-exist.ogg"; expect_failure "missing file"
: >"$tmp/empty.ogg"
run -- "$tmp/empty.ogg"; expect_failure "zero-byte file"
run -- "$tmp/corrupt.ogg"; expect_failure "corrupt file (random bytes)"
run -- "$tmp/corrupt-header.ogg"; expect_failure "corrupt file (Ogg magic, garbage body)"
run -- "$silence" "xx-invalid"; expect_failure "invalid language hint"

# --- configuration validation (item 7) ----------------------------------------
expect_rc() { # name code
  if ((rc != $2)); then bad "$1" "exit $rc, want $2: $err"; return; fi
  if [[ -n "$out" ]]; then bad "$1" "stdout not empty: $out"; return; fi
  ok "$1 (exit $rc)"
}
run -- "$silence" de; expect_rc "language hint outside whitelist" 2
run -- "$silence" pt; [[ "$err" != *"invalid language hint"* ]] && ok "whitelisted hint accepted" || bad "whitelisted hint accepted" "$err"
pwned="$tmp/pwned"
for var in TRANSCRIBE_BUDGET_S TRANSCRIBE_THREADS TRANSCRIBE_MIN_LANG_P TRANSCRIBE_VAD \
  TRANSCRIBE_LANG TRANSCRIBE_FALLBACK_LANG TRANSCRIBE_ALLOWED_LANGS TRANSCRIBE_MAX_CONCURRENT \
  TRANSCRIBE_LONG_S TRANSCRIBE_CHUNK_S; do
  rm -f -- "$pwned"
  run "${fake_env[@]}" "$var=threads[\$(touch $pwned)]" -- "$silence"
  if [[ -e "$pwned" ]]; then
    bad "$var injection" "command executed"
  else
    expect_rc "$var rejects injection" 2
  fi
done
run "${fake_env[@]}" TRANSCRIBE_BUDGET_S=08 -- "$silence"; expect_rc "leading-zero number rejected" 2
run "${fake_env[@]}" TRANSCRIBE_MIN_LANG_P=1.5 -- "$silence"; expect_rc "probability above 1 rejected" 2
run "${fake_env[@]}" TRANSCRIBE_VAD=yes -- "$silence"; expect_rc "non-boolean VAD rejected" 2
run "${fake_env[@]}" TRANSCRIBE_ALLOWED_LANGS="pt de" -- "$silence"; expect_rc "allowed language outside whitelist" 2
ro="$tmp/readonly-tmp"
mkdir -p "$ro" && chmod 500 "$ro"
run "${fake_env[@]}" TMPDIR="$ro" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
expect_rc "unwritable TMPDIR" 6
chmod 700 "$ro"

# --- real model on silence (skipped if the default model is not installed) ---
default_model="/home/openclaw/.local/share/openclaw/whisper/models/ggml-small.bin"
if [[ -f "${TRANSCRIBE_MODEL:-$default_model}" ]]; then
  run -- "$silence"; expect_failure "silent file"
  [[ "$err" == *"no usable speech"* || "$err" == *"no audio"* ]] || bad "silent file reason" "$err"
  # Without VAD, whisper turned 20 s of silence or faint noise into "E aí".
  noise="$tmp/noise.ogg"
  gst-launch-1.0 -q audiotestsrc wave=pink-noise volume=0.02 num-buffers=400 ! audioconvert ! audioresample ! \
    audio/x-raw,rate=48000,channels=1 ! opusenc ! oggmux ! filesink location="$noise" >/dev/null 2>&1
  run -- "$noise"; expect_failure "faint noise only"
else
  skip "silent file" "model not installed"
  skip "faint noise only" "model not installed"
fi

# --- fake whisper: placeholders, fallback, failures --------------------------
: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="en (p = 0.990000)" FAKE_TEXT_auto="(speaking in foreign language)" -- "$silence"
expect_failure "placeholder-only transcript"

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="[BLANK_AUDIO] (speaking in foreign language)" -- "$silence"
expect_failure "blank-audio placeholders only"

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="Legendas pela comunidade Amara.org" -- "$silence"
expect_failure "subtitle-credit hallucination only"

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="pt (p = 0.990000)" \
  FAKE_TEXT_auto="[BLANK_AUDIO] Granola, azeite. (speaking in foreign language)" -- "$silence"
if ((rc == 0)) && [[ "$out" == "Granola, azeite." ]] && [[ "$(cat "$tmp/calls")" == auto ]]; then
  ok "placeholders stripped, speech kept, single pass"
else
  bad "placeholders stripped, speech kept, single pass" "rc=$rc out=[$out] calls=$(tr '\n' ' ' <"$tmp/calls")"
fi

# --- placeholder, credit and filler stripping (item 3) --------------------------
# probe_text <input> <expected>: whisper "says" input; an empty expected means
# the script must print nothing and exit 3. Run under LC_ALL=C to prove the
# matching does not depend on a UTF-8 locale.
probe_text() {
  : >"$tmp/calls"
  run "${fake_env[@]}" LC_ALL=C LANG=C FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="$1" -- "$silence"
  if [[ -z "$2" ]]; then
    if ((rc == 3)) && [[ -z "$out" ]]; then ok "stripped to nothing, exit 3: $1"; else bad "strip: $1" "rc=$rc out=[$out]"; fi
  else
    if ((rc == 0)) && [[ "$out" == "$2" ]]; then ok "kept: $1 -> $2"; else bad "keep: $1" "rc=$rc out=[$out] want [$2] err=$err"; fi
  fi
}
for s in "(upbeat music)" "(soft music)" "(sighs)" "(coughs)" "(clears throat)" "(birds chirping)" \
  "(BEEPING)" "(Bell) (Bell)" "(PESSOA FALANDO)" "(tosse)" "(suspiro)" "(risada)" "(barulho)" \
  "(MÚSICA)" "(MÚSICA DE FUNDO E APLAUSOS DA PLATEIA)" "[Música]" "*risos*" "♪ ♪" \
  "Legendas por: Amara.org" "Transcrição e Legendas por Carla Negrão" \
  "Thank you for watching. Please subscribe." "Inscreva-se no canal!" "ლლლლლ"; do
  probe_text "$s" ""
done
# Filler ("Obrigado.", "E aí", "you") is judged with speech time and detection
# confidence: see the N1 answer/filler cases after the short-clip block.
probe_text "Compra o leite condensado [marca Nestlé] e o pão." "Compra o leite condensado [marca Nestlé] e o pão."
probe_text "Leite *importante* sem lactose." "Leite *importante* sem lactose."
probe_text "(upbeat music) Arroz e feijão. (sighs)" "Arroz e feijão."
probe_text "Leite, ovos. Inscreva-se no canal!" "Leite, ovos."
probe_text "*risos* Pão de queijo." "Pão de queijo."
probe_text "Arroz ლლლლ e feijão." "Arroz e feijão."
probe_text "Leite e ovos. Obrigado." "Leite e ovos. Obrigado."
probe_text "(MÚSICA) Detergente (Ypê, o de coco, 500 ml)." "Detergente (Ypê, o de coco, 500 ml)."

# --- credits anchored to whole phrases (N2) ------------------------------------
# A word that happens to appear in a credit line must not drop the sentence.
for s in "compra aquele queijo legendário e leite" "Get the legendary cheese and milk" \
  "legenda: lista da semana" "Cancela a subscribe da Netflix e compra pão." \
  "compra leite, obrigado por assistir o jogo comigo" "Compra leite, muito obrigado." \
  "Até a próxima compra leva arroz."; do
  probe_text "$s" "$s"
done
probe_text "Leite e ovos. Thank you for watching." "Leite e ovos."
# Hallucinated credits and sign-offs, rejected whatever the speech time.
for s in "Muito obrigado." "Até a próxima!" "Thank you very much." "Thank you so much for watching." \
  "Não se esqueça de se inscrever no canal." "Obrigado por assistir." "Obrigada e até a próxima." \
  "Legendas pela comunidade Amara.org" "[Música tocando]" "(música de fundo tocando suavemente agora)"; do
  probe_text "$s" ""
done

# --- parentheticals: only known sound tags are stripped (Low) ---------------------
for s in "Sabão (aquele azul) e leite." "Leite de coco (o de coco) e arroz." \
  "Buy tissues (two boxes) and milk." "Detergente e (sabão em pó) também." \
  "Palha de aço [BOM BRIL] e esponja." "Sabonete (DOVE) e shampoo." \
  "Compra pão (falando nisso) e manteiga." "Pega (Ypê, 500 ml) e (2 caixas) de leite."; do
  probe_text "$s" "$s"
done
probe_text "(MÚSICA) Arroz e feijão." "Arroz e feijão."
probe_text "Arroz e feijão (upbeat music)." "Arroz e feijão."
probe_text "(Pessoa falando) Leite e ovos." "Leite e ovos."
probe_text "[BLANK_AUDIO] Leite e ovos. [Música tocando]" "Leite e ovos."

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="es (p = 0.990000)" FAKE_TEXT_auto="leche y pan" FAKE_TEXT_pt="leite e pão" -- "$silence"
if ((rc == 0)) && [[ "$out" == "leite e pão" ]] && [[ "$(tr '\n' ' ' <"$tmp/calls")" == "auto pt " ]]; then
  ok "unexpected language falls back to -l pt"
else
  bad "unexpected language falls back to -l pt" "rc=$rc out=[$out] calls=$(tr '\n' ' ' <"$tmp/calls")"
fi

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_DETECT="en (p = 0.410000)" FAKE_TEXT_auto="lay che and eggs" FAKE_TEXT_pt="leite e ovos" -- "$silence"
if ((rc == 0)) && [[ "$out" == "leite e ovos" ]] && [[ "$(tr '\n' ' ' <"$tmp/calls")" == "auto pt " ]]; then
  ok "low-confidence detection falls back to -l pt"
else
  bad "low-confidence detection falls back to -l pt" "rc=$rc out=[$out] calls=$(tr '\n' ' ' <"$tmp/calls")"
fi

# --- short clips and English retry (item 4) --------------------------------------
# short_case <name> <expect: text or empty for exit 3> <want calls> env...
# Every case runs with VAD on (fake VAD model) so FAKE_SPEECH_S is what counts.
short_case() {
  local name="$1" want="$2" want_calls="$3"
  shift 3
  : >"$tmp/calls"
  run "${fake_env[@]}" TRANSCRIBE_VAD=1 "$@" -- "$silence"
  local calls
  calls="$(tr '\n' ' ' <"$tmp/calls" | sed 's/ $//')"
  if [[ -z "$want" ]]; then
    if ((rc == 3)) && [[ -z "$out" ]] && [[ "$calls" == "$want_calls" ]]; then ok "$name (exit 3, calls: $calls)"
    else bad "$name" "rc=$rc out=[$out] calls=[$calls] want [$want_calls] err=$err"; fi
  else
    if ((rc == 0)) && [[ "$out" == "$want" ]] && [[ "$calls" == "$want_calls" ]]; then ok "$name (calls: $calls)"
    else bad "$name" "rc=$rc out=[$out] want [$want] calls=[$calls] want [$want_calls] err=$err"; fi
  fi
}
short_case "0.6 s of speech is never trusted, no retry" "" "auto" \
  FAKE_SPEECH_S=0.60 FAKE_DETECT="en (p = 0.316000)" FAKE_TEXT_auto="E aí leite" FAKE_TEXT_pt="leite"
short_case "single junk word after unsure detection is rejected (granola -> a grande)" "" "auto pt" \
  FAKE_SPEECH_S=1.60 FAKE_DETECT="pt (p = 0.291000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="a grande"
short_case "single junk word after unsure detection is rejected (presunto -> o preso)" "" "auto pt" \
  FAKE_SPEECH_S=1.55 FAKE_DETECT="pt (p = 0.587000)" FAKE_TEXT_auto="Olá." FAKE_TEXT_pt="o preso"
short_case "single clear word with confident detection is kept" "Leite." "auto" \
  FAKE_SPEECH_S=1.20 FAKE_DETECT="pt (p = 0.950000)" FAKE_TEXT_auto="Leite."
short_case "two content words after a retry are kept" "arroz e feijão" "auto pt" \
  FAKE_SPEECH_S=2.40 FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a rosa fechou" FAKE_TEXT_pt="arroz e feijão"
short_case "English at p=0.86 is kept, not retried in pt" "Sesame." "auto" \
  FAKE_SPEECH_S=1.30 FAKE_DETECT="en (p = 0.860000)" FAKE_TEXT_auto="Sesame." FAKE_TEXT_pt="Sésamo."
short_case "English at p=0.60 is kept, not retried in pt" "Sesame seeds, please." "auto" \
  FAKE_SPEECH_S=2.10 FAKE_DETECT="en (p = 0.600000)" FAKE_TEXT_auto="Sesame seeds, please." FAKE_TEXT_pt="Sementes de sésamo."
short_case "English below p=0.50 is retried in pt" "leite e ovos" "auto pt" \
  FAKE_SPEECH_S=2.10 FAKE_DETECT="en (p = 0.450000)" FAKE_TEXT_auto="late and oval" FAKE_TEXT_pt="leite e ovos"
# English at or above TRANSCRIBE_MIN_LANG_P (0.8) is confident anyway; the keep
# rule decides only between TRANSCRIBE_EN_KEEP_P and TRANSCRIBE_MIN_LANG_P.
short_case "English at p=0.70 is kept by default" "Sesame seeds." "auto" \
  FAKE_SPEECH_S=2.10 FAKE_DETECT="en (p = 0.700000)" FAKE_TEXT_auto="Sesame seeds." FAKE_TEXT_pt="sésamo e gergelim"
short_case "TRANSCRIBE_EN_KEEP_P=0.9 retries English at p=0.70" "sésamo e gergelim" "auto pt" \
  TRANSCRIBE_EN_KEEP_P=0.9 FAKE_SPEECH_S=2.10 FAKE_DETECT="en (p = 0.700000)" FAKE_TEXT_auto="Sesame seeds." FAKE_TEXT_pt="sésamo e gergelim"
short_case "no VAD speech total in the log: audio length is used" "Leite." "auto" \
  FAKE_DETECT="pt (p = 0.950000)" FAKE_TEXT_auto="Leite."
: >"$tmp/calls"
run "${fake_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=1.10 FAKE_TEXT_pt="Presunto." -- "$silence" pt
((rc == 0)) && [[ "$out" == "Presunto." ]] && ok "language hint: a single clear word is kept" ||
  bad "hint single word" "rc=$rc out=[$out] err=$err"
: >"$tmp/calls"
run "${fake_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=0.80 FAKE_TEXT_pt="Presunto e queijo." -- "$silence" pt
((rc == 3)) && [[ -z "$out" ]] && ok "language hint does not lift the 1 s speech minimum" ||
  bad "hint speech minimum" "rc=$rc out=[$out]"

# --- spoken answers vs filler (N1) -------------------------------------------------
# The agent asks "Ouvi 'a grande'. Era granola?"; a spoken "sim" must come back.
for a in "Sim." "Não." "Isso." "Ok." "Okay." "Tá bom." "Beleza." "Pode." "Claro." "Certo." "Exato."; do
  short_case "answer kept (pt, 5 s, p=0.99): $a" "$a" "auto" \
    FAKE_SPEECH_S=5.00 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="$a"
done
for a in "Yes." "No." "Yeah." "Yep." "Nope." "Sure." "Correct." "Right."; do
  short_case "answer kept (en, 5 s, p=0.99): $a" "$a" "auto" \
    FAKE_SPEECH_S=5.00 FAKE_DETECT="en (p = 0.990000)" FAKE_TEXT_auto="$a"
done
short_case "answer kept at p=0.5 after the pt retry: Isso mesmo." "Isso mesmo." "auto pt" \
  FAKE_SPEECH_S=5.00 FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="Isso mesmo." FAKE_TEXT_pt="Isso mesmo."
short_case "a spoken 'sim' shorter than 1 s is still an answer (0.5 s, p=0.9)" "Sim." "auto" \
  FAKE_SPEECH_S=0.50 FAKE_DETECT="pt (p = 0.900000)" FAKE_TEXT_auto="Sim."
short_case "an answer under 0.4 s of speech is rejected" "" "auto" \
  FAKE_SPEECH_S=0.30 FAKE_DETECT="pt (p = 0.900000)" FAKE_TEXT_auto="Sim."
short_case "an answer with unsure detection (p=0.3) is rejected" "" "auto pt" \
  FAKE_SPEECH_S=2.00 FAKE_DETECT="pt (p = 0.300000)" FAKE_TEXT_auto="Ok." FAKE_TEXT_pt="Ok."
short_case "filler with very short speech is rejected: E aí" "" "auto" \
  FAKE_SPEECH_S=0.50 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="E aí"
short_case "filler with very short speech is rejected: Obrigado." "" "auto" \
  FAKE_SPEECH_S=0.60 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="Obrigado."
short_case "filler with very short speech is rejected: Tchau, tchau." "" "auto" \
  FAKE_SPEECH_S=0.40 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="Tchau, tchau."
short_case "filler with unsure detection is rejected: you" "" "auto pt" \
  FAKE_SPEECH_S=2.00 FAKE_DETECT="en (p = 0.300000)" FAKE_TEXT_auto="you" FAKE_TEXT_pt="you"
short_case "filler with enough speech and a confident detection passes: Obrigado." "Obrigado." "auto" \
  FAKE_SPEECH_S=3.00 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="Obrigado."
for v in TRANSCRIBE_EN_KEEP_P TRANSCRIBE_MIN_SPEECH_MS; do
  rm -f -- "$pwned"
  run "${fake_env[@]}" "$v=x[\$(touch $pwned)]" -- "$silence"
  [[ ! -e "$pwned" ]] && ((rc == 2)) && ok "$v rejects injection (exit 2)" || bad "$v injection" "rc=$rc"
done

# --- long notes (item 2) ----------------------------------------------------------
# The splitter itself: every chunk at most 30 s, contiguous, covering the note.
# audiotestsrc adopts the downstream rate, so size buffers as rate/10 samples
# (0.1 s each): 1300 buffers is exactly 130 s at any rate.
noise130="$tmp/noise-130s.wav"
gst-launch-1.0 -q audiotestsrc wave=pink-noise volume=0.3 samplesperbuffer=1600 num-buffers=1300 ! \
  audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink location="$noise130" >/dev/null 2>&1
noise_ms=$((($(stat -c %s "$noise130") - 44) / 32))
chunk_list="$(python3 -I "$tool_dir/transcribe_audio_chunks.py" "$noise130" "$tmp/split" 30000)"
chunk_check="$(awk -v total="$noise_ms" '
  { if ($2 != prev) bad = bad " gap@" NR; if ($3 - $2 > 30000) bad = bad " long@" NR; prev = $3; n++ }
  END { if (prev < total - 1) bad = bad " short-cover"; print n, (bad == "" ? "ok" : bad) }' <<<"$chunk_list")"
if ((noise_ms == 130000)) && [[ "$chunk_check" =~ ^[5-6]\ ok$ ]]; then
  ok "splitter: 130 s -> ${chunk_check% ok} contiguous chunks, none over 30 s"
else
  bad "splitter" "fixture ${noise_ms} ms, $chunk_check :: $chunk_list"
fi
rm -rf -- "$tmp/split" "$noise130"

long130="$tmp/long-130s.ogg"
gst-launch-1.0 -q audiotestsrc wave=silence samplesperbuffer=4800 num-buffers=1300 ! \
  audio/x-raw,rate=48000,channels=1 ! audioconvert ! opusenc ! oggmux ! filesink location="$long130" >/dev/null 2>&1
: >"$tmp/fake-base.bin"
long_env=("${fake_env[@]}" TRANSCRIBE_LONG_MODEL="$tmp/fake-base.bin" FAKE_MODELS="$tmp/models" FAKE_ECHO_FILE=1)
long_calls() { tr '\n' ' ' <"$tmp/calls" | sed 's/ $//'; }

# The 130 s silence fixture splits into chunk000..chunk004.
: >"$tmp/calls"; : >"$tmp/models"
run "${long_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" FAKE_TEXT_pt="leite e ovos" -- "$long130"
long_models="$(sed 's#.*/##' "$tmp/models" | sort -u | tr '\n' ' ' | sed 's/ $//')"
if ((rc == 0)) && [[ "$(long_calls)" == "auto auto auto auto auto" ]] && [[ "$long_models" == fake-base.bin ]] &&
  [[ "$out" == "arroz e feijão chunk000. arroz e feijão chunk001. arroz e feijão chunk002. arroz e feijão chunk003. arroz e feijão chunk004." ]]; then
  ok "130 s note: every chunk detects its own language, confident chunks need one pass, all text kept"
else
  bad "130 s note" "rc=$rc calls=[$(long_calls)] models=[$long_models] out=[$out] err=$err"
fi

: >"$tmp/calls"
run "${long_env[@]}" FAKE_DETECT="en (p = 0.700000)" FAKE_TEXT_auto="milk" FAKE_TEXT_en="eggs" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto auto auto auto auto" ]] && ok "long note: English at p=0.70 in each chunk is kept, no pt retry" ||
  bad "long note English" "rc=$rc calls=[$(long_calls)]"

: >"$tmp/calls"
run "${long_env[@]}" FAKE_TEXT_pt="pão" -- "$long130" pt
[[ $rc == 0 && "$(long_calls)" =~ ^pt( pt){4,}$ ]] && ok "long note: a language hint is used for every chunk" ||
  bad "long note hint" "rc=$rc calls=[$(long_calls)]"

# Low: base missed items on 61-120 s notes, so the primary model (small)
# handles chunked notes up to TRANSCRIBE_BASE_OVER_S (120) and base only above.
long90="$tmp/long-90s.ogg"
gst-launch-1.0 -q audiotestsrc wave=silence samplesperbuffer=4800 num-buffers=900 ! \
  audio/x-raw,rate=48000,channels=1 ! audioconvert ! opusenc ! oggmux ! filesink location="$long90" >/dev/null 2>&1
: >"$tmp/calls"; : >"$tmp/models"
run "${long_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$long90"
long_models="$(sed 's#.*/##' "$tmp/models" | sort -u | tr '\n' ' ' | sed 's/ $//')"
[[ $rc == 0 && "$long_models" == fake-model.bin && "$(long_calls)" =~ ^auto( auto){2,}$ && "$err" == *"long note (90 s): fake-model.bin"* ]] &&
  ok "90 s note is chunked on the primary model (small), not base" ||
  bad "90 s note model" "rc=$rc models=[$long_models] calls=[$(long_calls)] err=$err"
: >"$tmp/calls"; : >"$tmp/models"
run "${long_env[@]}" TRANSCRIBE_BASE_OVER_S=60 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$long90"
long_models="$(sed 's#.*/##' "$tmp/models" | sort -u | tr '\n' ' ' | sed 's/ $//')"
[[ $rc == 0 && "$long_models" == fake-base.bin ]] && ok "TRANSCRIBE_BASE_OVER_S=60 sends a 90 s note to base" ||
  bad "base threshold override" "rc=$rc models=[$long_models] err=$err"
rm -f -- "$pwned"
run "${fake_env[@]}" "TRANSCRIBE_BASE_OVER_S=x[\$(touch $pwned)]" -- "$silence"
[[ ! -e "$pwned" ]] && ((rc == 2)) && ok "TRANSCRIBE_BASE_OVER_S rejects injection (exit 2)" || bad "TRANSCRIBE_BASE_OVER_S injection" "rc=$rc"

# N3: the language is decided per chunk, never locked by the first one.
: >"$tmp/calls"
run "${long_env[@]}" FAKE_DETECT="pt (p = 0.950000)" FAKE_DETECT_chunk000="en (p = 0.950000)" FAKE_DETECT_chunk001="en (p = 0.950000)" \
  FAKE_TEXT_auto="spoken" FAKE_TEXT_pt="FORCED-PT" FAKE_TEXT_en="FORCED-EN" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto auto auto auto auto" && "$out" != *FORCED* ]] &&
  ok "long note EN then PT: each chunk keeps its own confident language, nothing forced" ||
  bad "long note EN->PT" "rc=$rc calls=[$(long_calls)] out=[$out]"

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=20.0 FAKE_SPEECH_S_chunk000=0.00 FAKE_DETECT_chunk000= \
  FAKE_DETECT="en (p = 0.950000)" FAKE_TEXT_auto_chunk000= FAKE_TEXT_auto="milk and eggs" FAKE_TEXT_pt="FORCED-PT" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto auto auto auto auto" && "$out" == *"milk and eggs chunk001."* && "$out" != *FORCED* ]] &&
  ok "long note silence then EN: the silent chunk decides nothing, English is not forced to pt" ||
  bad "long note silence->EN" "rc=$rc calls=[$(long_calls)] out=[$out] err=$err"

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=20.0 FAKE_DETECT="en (p = 0.950000)" FAKE_DETECT_chunk001="pt (p = 0.400000)" \
  FAKE_TEXT_auto="arroz" FAKE_TEXT_en="milk" FAKE_TEXT_pt="FORCED-PT" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto auto en auto auto auto" && "$out" == *"milk chunk001."* && "$out" != *FORCED* ]] &&
  ok "long note: an unsure chunk is retried in the last confident language (en)" ||
  bad "long note unsure chunk" "rc=$rc calls=[$(long_calls)] out=[$out] err=$err"

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=20.0 FAKE_DETECT="pt (p = 0.950000)" FAKE_DETECT_chunk000="es (p = 0.400000)" \
  FAKE_TEXT_auto="leche" FAKE_TEXT_pt="leite" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto pt auto auto auto auto" && "$out" == "leite chunk000. leche chunk001."* ]] &&
  ok "long note: an unsure first chunk falls back to pt; later chunks still detect" ||
  bad "long note unsure first chunk" "rc=$rc calls=[$(long_calls)] out=[$out] err=$err"

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_VAD=1 FAKE_SPEECH_S=20.0 FAKE_DETECT="pt (p = 0.950000)" FAKE_DETECT_chunk001="pt (p = 0.400000)" \
  FAKE_SPEECH_S_chunk001=0.50 FAKE_TEXT_auto="arroz" -- "$long130"
[[ $rc == 0 && "$(long_calls)" == "auto auto auto auto auto" ]] &&
  ok "long note: an unsure chunk with under 1 s of speech is not retried" ||
  bad "long note little speech chunk" "rc=$rc calls=[$(long_calls)] out=[$out]"

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_BUDGET_S=12 FAKE_SLEEP_ON_CALL=3 \
  FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" FAKE_TEXT_pt="leite e ovos" -- "$long130"
if ((rc == 0)) && [[ "$out" == "arroz e feijão chunk000. arroz e feijão chunk001."* ]] &&
  [[ "$out" =~ \[transcript\ truncated\ after\ [0-9]+\ s\ of\ 13[0-9]\ s\]$ ]] && ((secs <= 15)); then
  ok "budget runs out on chunk 3: partial transcript plus truncation marker, exit 0 in ${secs}s"
else
  bad "long note truncation" "rc=$rc secs=$secs out=[$out] err=$err"
fi

# N3 with real speech: ~90 s notes stitched from the PT and EN samples in $tmp
# (deleted with it). The expected words come from the supplied samples
# themselves, never from this file: each sample is transcribed alone, and up
# to 6 distinctive words (5+ letters, not stopwords, absent from the other
# sample's transcript) are kept in $tmp. A stitched note passes when at least
# half of each language's words come back. A translated passage would lose its
# words, so this also checks that each part stays in its own language.
derive_words() { # own-transcript other-transcript -> words, one per line
  python3 -I - "$1" "$2" <<'PY'
import re, sys, unicodedata
def fold(t):
    t = unicodedata.normalize('NFKD', t.lower())
    return ''.join(c for c in t if not unicodedata.combining(c))
def words(path):
    return re.findall(r'[^\W\d_]+', fold(open(path, encoding='utf-8', errors='replace').read()))
STOP = set('''about after again being could every first great little other people really right should
still their there these thing things think those through today where which while would years
agora ainda antes aquele aquela assim coisa coisas depois desse dessa entao essas esses estava
estou estamos fazer muito nessa nesse outra outro porque quando sobre tambem temos tenho todos
vamos voces please thank thanks'''.split())
own, other = words(sys.argv[1]), set(words(sys.argv[2]))
cand = sorted({w for w in own if len(w) >= 5 and w not in STOP and w not in other},
              key=lambda w: (-len(w), w))
print('\n'.join(cand[:6]))
PY
}
count_words() { # words-file text -> "found total"
  python3 -I - "$1" "$2" <<'PY'
import re, sys, unicodedata
def fold(t):
    t = unicodedata.normalize('NFKD', t.lower())
    return ''.join(c for c in t if not unicodedata.combining(c))
want = [w for w in open(sys.argv[1], encoding='utf-8').read().split() if w]
have = set(re.findall(r'[^\W\d_]+', fold(sys.argv[2])))
print(sum(w in have for w in want), len(want))
PY
}
half_found() { # "found total": at least half, rounded up
  local found total
  read -r found total <<<"$1"
  ((total > 0 && found * 2 >= total))
}
if [[ -n "${TRANSCRIBE_TEST_PT-}" && -f "$TRANSCRIBE_TEST_PT" && -n "${TRANSCRIBE_TEST_EN-}" && -f "$TRANSCRIBE_TEST_EN" ]]; then
  stitch_ready=1
  run -- "$TRANSCRIBE_TEST_PT"
  ((rc == 0)) && printf '%s\n' "$out" >"$tmp/ref-PT.txt" || stitch_ready=0
  run -- "$TRANSCRIBE_TEST_EN"
  ((rc == 0)) && printf '%s\n' "$out" >"$tmp/ref-EN.txt" || stitch_ready=0
  if ((stitch_ready)); then
    derive_words "$tmp/ref-PT.txt" "$tmp/ref-EN.txt" >"$tmp/words-PT.txt"
    derive_words "$tmp/ref-EN.txt" "$tmp/ref-PT.txt" >"$tmp/words-EN.txt"
    (($(wc -w <"$tmp/words-PT.txt") >= 3 && $(wc -w <"$tmp/words-EN.txt") >= 3)) || stitch_ready=0
  fi
  rm -f -- "$tmp/ref-PT.txt" "$tmp/ref-EN.txt"
fi
if [[ -z "${stitch_ready-}" ]]; then
  skip "real stitched EN/PT long notes" "set TRANSCRIBE_TEST_PT and TRANSCRIBE_TEST_EN"
elif ((stitch_ready == 0)); then
  skip "real stitched EN/PT long notes" "samples did not transcribe alone or yield 3+ distinctive words each"
else
  for lang_src in PT EN; do
    src_var="TRANSCRIBE_TEST_$lang_src"
    gst-launch-1.0 -q filesrc location="${!src_var}" ! decodebin ! audioconvert ! audioresample ! \
      audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink location="$tmp/src-$lang_src.wav" >/dev/null 2>&1
  done
  python3 -I - "$tmp" <<'PY'
import sys, wave
d = sys.argv[1]
def pcm(name):
    with wave.open(f'{d}/src-{name}.wav', 'rb') as w:
        return w.readframes(w.getnframes())
def rep(data, seconds):
    need = seconds * 32000
    return (data * (need // len(data) + 1))[:need]
pt, en = pcm('PT'), pcm('EN')
for name, data in {'en-pt': rep(en, 45) + rep(pt, 45), 'pt-en': rep(pt, 45) + rep(en, 45),
                   'sil-en': bytes(40 * 32000) + rep(en, 50)}.items():
    with wave.open(f'{d}/{name}.wav', 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(data)
PY
  for mix in en-pt pt-en sil-en; do
    gst-launch-1.0 -q filesrc location="$tmp/$mix.wav" ! wavparse ! audioconvert ! audioresample ! \
      audio/x-raw,rate=48000,channels=1 ! opusenc ! oggmux ! filesink location="$tmp/$mix.ogg" >/dev/null 2>&1
    rm -f -- "$tmp/$mix.wav"
  done
  rm -f -- "$tmp/src-PT.wav" "$tmp/src-EN.wav"

  # The chunk that straddles a switch holds both languages and is decoded in its
  # dominant one, so a few words next to the switch may be lost; the rest of
  # each language must come back (at least half of its derived words).
  for mix in en-pt pt-en sil-en; do
    run -- "$tmp/$mix.ogg"
    pt_found="$(count_words "$tmp/words-PT.txt" "$out")"
    en_found="$(count_words "$tmp/words-EN.txt" "$out")"
    case "$mix" in
      en-pt | pt-en)
        if ((rc == 0)) && half_found "$pt_found" && half_found "$en_found" && ((secs < 55)); then
          ok "real ${mix^^} 90 s note: Portuguese words ${pt_found/ //}, English words ${en_found/ //}, ${secs}s"
        else
          bad "real ${mix^^} 90 s note" "rc=$rc pt=${pt_found/ //} en=${en_found/ //} secs=$secs err=$err"
        fi
        ;;
      sil-en)
        if ((rc == 0)) && half_found "$en_found" && ((secs < 55)); then
          ok "real 40 s silence -> EN note: English words ${en_found/ //}, not forced to pt, ${secs}s"
        else
          bad "real silence->EN note" "rc=$rc en=${en_found/ //} secs=$secs err=$err"
        fi
        ;;
    esac
  done
  rm -f -- "$tmp/en-pt.ogg" "$tmp/pt-en.ogg" "$tmp/sil-en.ogg" "$tmp/words-PT.txt" "$tmp/words-EN.txt"
fi

: >"$tmp/calls"
run "${long_env[@]}" TRANSCRIBE_BUDGET_S=8 FAKE_SLEEP_ON_CALL=1 FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz" -- "$long130"
((rc == 5)) && [[ -z "$out" ]] && ((secs <= 11)) && ok "no chunk finishes within the budget: exit 5 in ${secs}s" ||
  bad "long note no chunk" "rc=$rc secs=$secs out=[$out] err=$err"

if [[ -n "${TRANSCRIBE_TEST_PT-}" && -f "$TRANSCRIBE_TEST_PT" ]]; then
  # ~5 minutes of real speech: the PT sample repeated, built and deleted in $tmp.
  gst-launch-1.0 -q filesrc location="$TRANSCRIBE_TEST_PT" ! decodebin ! audioconvert ! audioresample ! \
    audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink location="$tmp/pt.wav" >/dev/null 2>&1
  python3 -I - "$tmp/pt.wav" "$tmp/long-pt.wav" 300 <<'PY'
import sys, wave
src, dst, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3])
with wave.open(src, 'rb') as w:
    pcm = w.readframes(w.getnframes())
need = seconds * 16000 * 2
data = (pcm * (need // len(pcm) + 1))[:need]
with wave.open(dst, 'wb') as out:
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(data)
PY
  gst-launch-1.0 -q filesrc location="$tmp/long-pt.wav" ! wavparse ! audioconvert ! audioresample ! \
    audio/x-raw,rate=48000,channels=1 ! opusenc ! oggmux ! filesink location="$tmp/long-pt.ogg" >/dev/null 2>&1
  rm -f -- "$tmp/pt.wav" "$tmp/long-pt.wav"
  run -- "$tmp/long-pt.ogg"
  real_state="complete"
  [[ "$out" == *"[transcript truncated after"* ]] && real_state="truncated: ${out##*\[}"
  if ((rc == 0)) && [[ -n "$out" ]] && ((secs < 55)); then
    ok "real 5 min PT note: exit 0 in ${secs}s, ${#out} chars, $real_state"
  else
    bad "real 5 min PT note" "rc=$rc secs=$secs chars=${#out} err=$err"
  fi
  rm -f -- "$tmp/long-pt.ogg"
else
  skip "real 5 min PT note" "set TRANSCRIBE_TEST_PT to a local voice note"
fi

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_TEXT_en="milk and eggs" -- "$silence" en
if ((rc == 0)) && [[ "$out" == "milk and eggs" ]] && [[ "$(tr '\n' ' ' <"$tmp/calls")" == "en " ]]; then
  ok "explicit language hint is used without detection"
else
  bad "explicit language hint is used without detection" "rc=$rc out=[$out] calls=$(tr '\n' ' ' <"$tmp/calls")"
fi

# --- model choice (lead decision: small primary, turbo only as retry) ----------
# A fake install tree so the script's own defaults are exercised.
root="$tmp/fake-root"
mkdir -p "$root/models" "$root/whisper-bin-ubuntu-x64"
: >"$root/models/ggml-small.bin"
: >"$root/models/ggml-large-v3-turbo-q8_0.bin"
: >"$root/models/ggml-silero-v5.1.2.bin"
root_env=(WHISPER_ROOT="$root" TRANSCRIBE_WHISPER_CLI="$fake" TRANSCRIBE_VAD=0 FAKE_CALLS="$tmp/calls" FAKE_MODELS="$tmp/models")
# $silence is 4.27 s (under the 6 s turbo threshold); note7s is exactly 7 s.
note7s="$tmp/note-7s.ogg"
gst-launch-1.0 -q audiotestsrc wave=silence samplesperbuffer=4800 num-buffers=70 ! \
  audio/x-raw,rate=48000,channels=1 ! audioconvert ! opusenc ! oggmux ! filesink location="$note7s" >/dev/null 2>&1
models_used() { tr '\n' ' ' <"$tmp/models" | sed -E 's#[^ ]*/##g; s/ $//'; }

: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
[[ $rc == 0 && "$(models_used)" == "ggml-small.bin" ]] && ok "default model is small, confident detection needs one pass" ||
  bad "default model is small" "rc=$rc models=[$(models_used)] err=$err"

: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
[[ $rc == 0 && "$(models_used)" == "ggml-small.bin ggml-large-v3-turbo-q8_0.bin" && "$out" == "granola e azeite" ]] &&
  ok "unsure detection on a 7 s note with a free slot retries with turbo" ||
  bad "retry with turbo" "rc=$rc models=[$(models_used)] out=[$out] err=$err"

: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$silence"
[[ "$(models_used)" == "ggml-small.bin ggml-small.bin" ]] && ok "note under 6 s (4.3 s) retries with small, not turbo" ||
  bad "short note retry model" "rc=$rc models=[$(models_used)]"

: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" TRANSCRIBE_RETRY_MIN_BUDGET_S=9999 FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
[[ "$(models_used)" == "ggml-small.bin ggml-small.bin" ]] && ok "retry uses small when the budget left is too small for turbo" ||
  bad "budget-limited retry model" "rc=$rc models=[$(models_used)]"

: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" TRANSCRIBE_RETRY_MODEL= FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
[[ "$(models_used)" == "ggml-small.bin ggml-small.bin" ]] && ok "empty TRANSCRIBE_RETRY_MODEL disables turbo" ||
  bad "retry model disabled" "rc=$rc models=[$(models_used)]"

# N4: turbo must not hold the CPU while another member's note waits.
env "${fake_env[@]}" TRANSCRIBE_MAX_CONCURRENT=2 FAKE_SLEEP=44.519 FAKE_DETECT="pt (p = 0.990000)" \
  "$script" "$silence" >/dev/null 2>&1 &
slot_holder=$!
if wait_for_proc '^sleep 44.519$'; then
  : >"$tmp/calls"; : >"$tmp/models"
  run "${root_env[@]}" TRANSCRIBE_MAX_CONCURRENT=2 FAKE_DETECT="pt (p = 0.500000)" \
    FAKE_TEXT_auto="arroz e feijão" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
  if ((rc == 0)) && [[ "$(models_used)" == "ggml-small.bin" && "$out" == "arroz e feijão" && "$err" == *"no free slot"* ]]; then
    ok "other slot held: turbo retry skipped, small's first pass kept"
  else
    bad "retry skipped when busy" "rc=$rc models=[$(models_used)] out=[$out] err=$err"
  fi
  : >"$tmp/calls"; : >"$tmp/models"
  run "${root_env[@]}" TRANSCRIBE_MAX_CONCURRENT=2 FAKE_DETECT="pt (p = 0.500000)" \
    FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
  if ((rc == 3)) && [[ -z "$out" && "$(models_used)" == "ggml-small.bin" ]]; then
    ok "other slot held and small's pass was rejected: exit 3 without turbo"
  else
    bad "retry skipped when busy, rejected first pass" "rc=$rc models=[$(models_used)] out=[$out] err=$err"
  fi
else
  bad "slot holder setup" "holder never started"
fi
kill -TERM "$slot_holder" 2>/dev/null
wait "$slot_holder" 2>/dev/null

rm -f "$root/models/ggml-large-v3-turbo-q8_0.bin"
: >"$tmp/calls"; : >"$tmp/models"
run "${root_env[@]}" FAKE_DETECT="pt (p = 0.500000)" FAKE_TEXT_auto="a grande" FAKE_TEXT_pt="granola e azeite" -- "$note7s"
[[ $rc == 0 && "$(models_used)" == "ggml-small.bin ggml-small.bin" ]] && ok "turbo not installed: retry quietly uses small" ||
  bad "missing turbo" "rc=$rc models=[$(models_used)] err=$err"

: >"$tmp/calls"
run "${fake_env[@]}" FAKE_RC=1 FAKE_TEXT_auto="whatever" -- "$silence"
expect_failure "whisper exits non-zero"

run TRANSCRIBE_MODEL="$tmp/no-such-model.bin" -- "$silence"
expect_failure "model missing"

# Item 5: a missing VAD model must not silently turn VAD off, because without
# VAD whisper turns silence and faint noise into words.
: >"$tmp/calls"
run "${fake_env[@]}" TRANSCRIBE_VAD=1 TRANSCRIBE_VAD_MODEL="$tmp/no-such-vad.bin" \
  FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="E aí" -- "$silence"
expect_rc "VAD requested but VAD model missing" 5
if [[ -s "$tmp/calls" ]]; then bad "VAD model missing: whisper not run" "whisper was called"; else ok "VAD model missing: whisper not run"; fi
[[ "$err" == *"VAD model"* ]] && ok "VAD model missing: message names the VAD model" || bad "VAD model missing message" "$err"
: >"$tmp/calls"
run "${fake_env[@]}" TRANSCRIBE_VAD=0 TRANSCRIBE_VAD_MODEL="$tmp/no-such-vad.bin" \
  FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
((rc == 0)) && ok "VAD explicitly off does not need the VAD model" || bad "VAD off without VAD model" "exit $rc: $err"

# --- signals and leftovers (item 6) --------------------------------------------
# start_slow <tmpdir> <unique sleep seconds>: run the script in the background
# with a fake whisper that execs `sleep`, and wait until that sleep is running.
start_slow() {
  mkdir -p "$1"
  : >"$tmp/calls"
  env "${fake_env[@]}" TMPDIR="$1" FAKE_SLEEP="$2" FAKE_DETECT="pt (p = 0.990000)" \
    "$script" "$silence" >/dev/null 2>"$tmp/slow.err" &
  slow_pid=$!
  local i
  for i in $(seq 100); do
    pgrep -f "^sleep $2\$" >/dev/null && return 0
    sleep 0.1
  done
  return 1
}
gone_within() { # pattern seconds
  local i
  for i in $(seq $(($2 * 10))); do
    pgrep -f "$1" >/dev/null || return 0
    sleep 0.1
  done
  return 1
}

killtmp="$tmp/kill-tmp"
if start_slow "$killtmp" 41.731; then
  kill -KILL "$slow_pid"
  wait "$slow_pid" 2>/dev/null
  if gone_within '^sleep 41.731$' 3; then
    ok "SIGKILL on the script also kills whisper"
  else
    bad "SIGKILL on the script also kills whisper" "whisper still running"
    pkill -KILL -f '^sleep 41.731$'
  fi
  left=("$killtmp"/transcribe-audio.*)
  if [[ -d "${left[0]}" ]]; then
    ok "SIGKILL leaves the work dir behind (expected; cleaned on next start)"
    # Age the leftover past the 10 minute threshold, add a young one, and a
    # symlink that must not be followed.
    touch -d '-20 minutes' "${left[0]}"
    mkdir -p "$killtmp/transcribe-audio.young"
    mkdir -p "$tmp/canary-target" && : >"$tmp/canary-target/keep"
    ln -s "$tmp/canary-target" "$killtmp/transcribe-audio.link"
    touch -h -d '-20 minutes' "$killtmp/transcribe-audio.link"
    run "${fake_env[@]}" TMPDIR="$killtmp" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz e feijão" -- "$silence"
    [[ ! -e "${left[0]}" ]] && ok "stale work dir removed on next start" || bad "stale work dir removed" "still there"
    [[ -d "$killtmp/transcribe-audio.young" ]] && ok "young work dir kept" || bad "young work dir kept" "removed"
    [[ -f "$tmp/canary-target/keep" && -L "$killtmp/transcribe-audio.link" ]] &&
      ok "symlinked lookalike neither followed nor removed" || bad "symlink lookalike" "followed or removed"
  else
    bad "SIGKILL leftover" "no work dir found to test stale cleanup"
  fi
else
  bad "SIGKILL test setup" "fake whisper never started: $(cat "$tmp/slow.err")"
fi

termtmp="$tmp/term-tmp"
if start_slow "$termtmp" 42.917; then
  kill -TERM "$slow_pid"
  wait "$slow_pid"
  trc=$?
  ((trc == 143)) && ok "SIGTERM exits 143" || bad "SIGTERM exit code" "$trc"
  if gone_within '^sleep 42.917$' 3; then ok "SIGTERM kills whisper"; else bad "SIGTERM kills whisper" "still running"; pkill -KILL -f '^sleep 42.917$'; fi
  leftovers=$(find "$termtmp" -mindepth 1 -maxdepth 1 -name 'transcribe-audio.*' | wc -l)
  ((leftovers == 0)) && ok "SIGTERM removes the work dir (decoded audio)" || bad "SIGTERM cleanup" "$leftovers dirs left"
else
  bad "SIGTERM test setup" "fake whisper never started: $(cat "$tmp/slow.err")"
fi

# --- real speech ---------------------------------------------------------------
sample=""
for pair in "PT:${TRANSCRIBE_TEST_PT-}:${TRANSCRIBE_TEST_PT_EXPECT-}" "EN:${TRANSCRIBE_TEST_EN-}:${TRANSCRIBE_TEST_EN_EXPECT-}"; do
  IFS=: read -r name path expect <<<"$pair"
  if [[ -n "$path" && -f "$path" ]]; then
    run -- "$path"
    expect_transcript "$name sample" "$expect"
    [[ -n "$sample" ]] || sample="$path"
  else
    skip "$name sample" "set TRANSCRIBE_TEST_$name to a local voice note"
  fi
done

if [[ -n "$sample" ]]; then
  encoder=""
  for e in avenc_aac voaacenc fdkaacenc faac; do
    if gst-inspect-1.0 "$e" >/dev/null 2>&1; then encoder="$e"; break; fi
  done
  if [[ -n "$encoder" ]]; then
    m4a="$tmp/sample.m4a"
    gst-launch-1.0 -q filesrc location="$sample" ! decodebin ! audioconvert ! audioresample ! \
      "$encoder" ! mp4mux ! filesink location="$m4a" >/dev/null 2>&1
    if [[ -s "$m4a" ]]; then
      run -- "$m4a"; expect_transcript "m4a input ($encoder)" ""
    else
      bad "m4a input" "could not transcode sample with $encoder"
    fi
  else
    skip "m4a input" "no gst AAC encoder installed"
  fi

  spaced_dir="$tmp/dir with spaces"
  mkdir -p "$spaced_dir"
  spaced="$spaced_dir/it's a \"note\" (1) ! copy.ogg"
  cp -- "$sample" "$spaced"
  run -- "$spaced"; expect_transcript "path with spaces and quotes" ""
else
  skip "m4a input" "needs TRANSCRIBE_TEST_PT or TRANSCRIBE_TEST_EN"
  spaced_dir="$tmp/dir with spaces"
  mkdir -p "$spaced_dir"
  spaced="$spaced_dir/it's a \"note\" (1) ! copy.ogg"
  cp -- "$silence" "$spaced"
  : >"$tmp/calls"
  run "${fake_env[@]}" FAKE_DETECT="pt (p = 0.990000)" FAKE_TEXT_auto="arroz" -- "$spaced"
  if ((rc == 0)) && [[ "$out" == arroz ]]; then ok "path with spaces and quotes (decode + fake whisper)"
  else bad "path with spaces and quotes" "rc=$rc out=[$out] err=$err"; fi
fi

printf '\n%d passed, %d failed, %d skipped\n' "$passed" "$failed" "$skipped"
((failed == 0))
