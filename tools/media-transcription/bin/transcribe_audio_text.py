#!/usr/bin/env python3
"""Clean a whisper transcript and decide whether any usable speech remains.

Used by tools/media-transcription/bin/transcribe-audio-local:

    python3 -I transcribe_audio_text.py <transcript.txt> [--speech-ms N]
        [--min-speech-ms N] [--lang-p P] [--min-p P]

Prints the cleaned transcript on one line and exits 0 when usable speech
remains. Otherwise it prints nothing and exits 3.

Works on Unicode text read as UTF-8 bytes, so behaviour does not depend on the
caller's locale ("(MÚSICA)" is stripped under LC_ALL=C too).

What is removed:
  - (…), […] and *…* groups that are whisper sound tags: plain words only (no
    digits or punctuation), at most 8 of them, containing a word from
    SOUND_WORDS or a phrase from SOUND_PHRASES, in any letter case:
    (upbeat music), (MÚSICA), [BLANK_AUDIO], (PESSOA FALANDO), *risos*,
    [Música tocando], (música de fundo tocando suavemente agora).
    Anything else is kept, because a household note puts real words in
    parentheses: (o de coco), (two boxes), (sabão em pó), (DOVE), [BOM BRIL],
    (Ypê, 500 ml).
  - Subtitle credits and subscribe calls, only as whole sentences that are
    that phrase (SENTENCE_CREDIT): "Legendas por: Amara.org", "Inscreva-se no
    canal!", "Thank you for watching." A sentence that merely contains a word
    such as "legendário", "subscribe" or "obrigado por assistir o jogo" is
    kept.
  - Sign-offs that are the entire transcript (WHOLE_SIGNOFF): "Muito
    obrigado.", "Até a próxima!", "Thank you very much."
  - Words written only in non-Latin scripts (whisper emits ლლლლ on noise).
  - Music symbols.

When the rest is usable, see usable_reason().
"""
import re
import sys
import unicodedata

# Folded (lowercase, accents removed) filler that carries no request on its
# own. Affirmations and negations are NOT filler: "sim" or "não" is the whole
# answer when the agent asks "Ouvi 'a grande'. Era granola?".
FILLER = frozenset('''
a e o ai ah aham alo amem bye eh entao ha haha hello hi hm hmm hum legal mm ne
obrigado obrigada obrigados oh oi ola so tchau thank thanks the uh uhum um valeu
you
'''.split())

# Folded words that make up a spoken answer to a confirmation question.
ANSWERS = frozenset('''
sim nao isso mesmo ok okay ta bom beleza pode claro certo exato exatamente
perfeito combinado yes no yeah yep nope sure correct right
'''.split())

# An answer-only transcript ("Sim.", "Tá bom.") is shorter than any grocery
# request, so it gets its own, lower speech minimum, and a detection this
# confident is enough (a one-syllable clip rarely reaches the 0.8 used for
# single content words).
ANSWER_MIN_SPEECH_MS = 400
ANSWER_MIN_P = 0.5

# Folded words that mark a group as a whisper sound tag, pt and en.
SOUND_WORDS = frozenset('''
music musica musicas song cancao singing cantando instrumental
applause aplausos aplauso laugh laughs laughing laughter risos riso risada
risadas rindo cough coughs coughing tosse tossindo sigh sighs sighing suspiro
suspiros suspirando barulho barulhos noise noises ruido ruidos silence silencio
beep beeps beeping bip bipe bell bells sino chirping chirp barking latido
latidos inaudible inaudivel indistinct unintelligible ininteligivel blank_audio
breathing respiracao respirando crying chorando choro static estatica
whistling assobio assobiando mumbling murmurio murmurando pigarro
'''.split())
# Multi-word tags whose words are ordinary on their own ("falando nisso",
# "speaking of milk", "blank" and "throat" stay in real parentheses).
SOUND_PHRASES = (
    'pessoa falando', 'pessoas falando', 'speaking in foreign language',
    'speaking foreign language', 'foreign language', 'lingua estrangeira',
    'clears throat', 'clearing throat', 'blank audio', 'no speech',
)

# Credit and subscribe lines, matched against one whole sentence after norm().
SENTENCE_CREDIT = re.compile(
    r'legendas? (?:por|pela|pelo)(?: .*)?'
    r'|transcricao e legendas?(?: .*)?'
    r'|(?:.* )?amara org(?: .*)?'
    r'|inscreva se no (?:meu |nosso )?canal(?: .*)?'
    r'|nao se esqueca de se inscrever(?: .*)?'
    r'|(?:please )?subscribe to (?:my|our|the) channel(?: .*)?'
    r'|(?:please )?(?:like and )?subscribe'
    r'|thank(?:s| you)(?: so| very)? much for watching'
    r'|thank(?:s| you) for watching'
    r'|obrigad[oa]s? por assistir(?:em)?'
)
# Sign-offs whisper invents on silence, rejected only when they are the whole
# transcript after norm(); "compra leite, muito obrigado" is a real note.
WHOLE_SIGNOFF = re.compile(
    r'(?:muito )?obrigad[oa]s?(?: e)? ate a proxima(?: vez)?'
    r'|muito obrigad[oa]s?(?: a todos)?'
    r'|ate a proxima(?: vez)?'
    r'|thank you(?: so| very)? much'
)

WORD_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
PAREN_RE = re.compile(r'\(([^()]*)\)')
BRACKET_RE = re.compile(r'\[([^\[\]]*)\]')
STAR_RE = re.compile(r'\*([^*\n]+)\*')
SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?…])\s+')
MUSIC_RE = re.compile('[♪♫♬♭♮♯\U0001F3B5\U0001F3B6]+')
TAG_TEXT_RE = re.compile(r"[^\W\d]+(?:[\s'’-]+[^\W\d]+)*")  # letters, _ ' - and spaces only


def fold(text):
    text = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in text if not unicodedata.combining(c))


def norm(text):
    """Folded text with every run of non-alphanumerics turned into one space."""
    return re.sub(r'[^a-z0-9]+', ' ', fold(text)).strip()


def is_latin_letter(ch):
    return ch.isalpha() and 'LATIN' in unicodedata.name(ch, '')


def is_latin_word(word):
    letters = [c for c in word if c.isalpha()]
    return bool(letters) and all(is_latin_letter(c) for c in letters)


def is_sound_tag(inner):
    """True when a bracketed group is a whisper sound tag (see module doc)."""
    inner = inner.strip()
    if not inner:
        return True
    if not TAG_TEXT_RE.fullmatch(inner):
        return False  # digits, commas, units: real words
    folded = fold(inner).replace('-', ' ')
    words = folded.split()
    if len(words) > 8:
        return False
    if any(w in SOUND_WORDS for w in words):
        return True
    spaced = ' ' + folded.replace('_', ' ') + ' '
    return any(f' {p} ' in spaced for p in SOUND_PHRASES)


def strip_group(match):
    return ' ' if is_sound_tag(match.group(1)) else match.group(0)


def clean(text):
    text = unicodedata.normalize('NFC', text.replace('\r', ' ').replace('\n', ' '))
    text = MUSIC_RE.sub(' ', text)
    # Nested groups are rare; two passes handle "((sighs))".
    for _ in range(2):
        text = PAREN_RE.sub(strip_group, text)
    text = BRACKET_RE.sub(strip_group, text)
    text = STAR_RE.sub(strip_group, text)
    sentences = [s for s in SENTENCE_SPLIT_RE.split(text)
                 if not SENTENCE_CREDIT.fullmatch(norm(s))]
    text = ' '.join(sentences)
    # Drop words written entirely in non-Latin scripts.
    text = WORD_RE.sub(lambda m: m.group(0) if is_latin_word(m.group(0)) else ' ', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([,.;:!?…])', r'\1', text)
    text = re.sub(r'^[\s,.;:!?…-]+', '', text)
    text = re.sub(r'([,;:])(?:\s*[,;:])+', r'\1', text)
    text = re.sub(r'^[\s,.;:!?…-]+$', '', text)
    text = text.strip()
    if WHOLE_SIGNOFF.fullmatch(norm(text)):
        return ''
    return text


def latin_words(text):
    return [w for w in WORD_RE.findall(text) if is_latin_word(w)]


def content_words(text):
    """Latin words that are neither filler nor a single letter (answers count)."""
    return [w for w in latin_words(text) if len(w) > 1 and fold(w) not in FILLER]


def usable_reason(text, speech_ms, min_speech_ms, lang_p, min_p):
    """None when the cleaned text is usable, else a short reason.

    Short Portuguese clips come back as confident junk: "granola" as
    "a grande", "leite" as "E aí", "presunto" as "o preso". The rules, in order:
      - no Latin words left: rejected.
      - answer-only ("Sim.", "Não.", "Isso mesmo.", "Tá bom.", "Yes."): kept
        with at least ANSWER_MIN_SPEECH_MS of speech and lang_p >=
        ANSWER_MIN_P, so the agent's confirmation question can be answered
        by voice.
      - under min_speech_ms of speech (VAD time, else audio length): rejected.
      - only filler ("E aí", "Obrigado.", "you"): rejected only when the
        language detection was unsure (lang_p < min_p); with enough speech
        and a confident detection it is real speech and passes through.
      - a single content word: kept only when lang_p >= min_p. An explicit
        language hint counts as confident (lang_p = 1).
      - two or more content words: kept.
    Credits and whole-transcript sign-offs never get this far: clean() removes
    them whatever the speech time or confidence.
    """
    if not text or not latin_words(text):
        return 'nothing left after cleaning' if not text else 'only non-Latin text'
    words = content_words(text)
    if words and all(fold(w) in ANSWERS for w in words):
        if speech_ms < ANSWER_MIN_SPEECH_MS:
            return f'too little speech for an answer ({speech_ms / 1000:.2f} s)'
        if lang_p < ANSWER_MIN_P:
            return f'answer with unsure language detection (p={lang_p:.2f})'
        return None
    if speech_ms < min_speech_ms:
        return f'too little speech ({speech_ms / 1000:.2f} s < {min_speech_ms / 1000:.2f} s)'
    if not words:
        if lang_p < min_p:
            return f'only filler with unsure language detection (p={lang_p:.2f})'
        return None
    if len(words) == 1 and lang_p < min_p:
        return f'single word "{words[0]}" with unsure language detection (p={lang_p:.2f})'
    return None


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog='transcribe_audio_text.py')
    ap.add_argument('transcript')
    ap.add_argument('--speech-ms', type=int, default=None,
                    help='milliseconds of speech (VAD), default: no minimum')
    ap.add_argument('--min-speech-ms', type=int, default=1000)
    ap.add_argument('--lang-p', type=float, default=1.0,
                    help='language detection probability; 1 for an explicit hint')
    ap.add_argument('--min-p', type=float, default=0.8)
    try:
        a = ap.parse_args(argv[1:])
    except SystemExit:
        return 2
    try:
        with open(a.transcript, 'rb') as f:
            raw = f.read().decode('utf-8', errors='replace')
    except OSError as e:
        sys.stderr.write(f'transcribe_audio_text: {e}\n')
        return 3
    text = clean(raw)
    speech_ms = a.speech_ms if a.speech_ms is not None else a.min_speech_ms
    reason = usable_reason(text, speech_ms, a.min_speech_ms, a.lang_p, a.min_p)
    if reason:
        sys.stderr.write(f'transcribe-audio-local: rejected transcript: {reason}\n')
        return 3
    sys.stdout.buffer.write(text.encode('utf-8') + b'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
