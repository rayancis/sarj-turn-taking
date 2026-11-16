# B. Arabic Audio Benchmark Specification

This is the recording plan. It defines what a benchmark recording is, how timing and labels are written,
and the public development set, scripted in full (24 scenarios). The hidden evaluation set (64 scripted
scenarios + 16 naturalistic, plus 8 in reserve) and the Day 3 checkpoint set (16) use the same format. They are held by the organisers, outside this repository.

- The machine-readable form of every field below is in [C-dataset-schema.md](C-dataset-schema.md).
- The scoring is in [D-evaluation-scoring.md](D-evaluation-scoring.md).
- How the recordings get made is in [E-production-plan.md](E-production-plan.md).

---

## 1. What one recording is

One **scenario** = one short stereo clip, 8 to 25 seconds long, on two channels:

| Channel | Contains | Source |
|---|---|---|
| 0, `agent` | What the agent says. This is the reference signal: a system may use it, as a real agent knows its own audio. | Synthetic Saudi Arabic voice (two fixed voices, below) |
| 1, `user` | What the caller's microphone picks up: the caller's speech, background sound, and in the echo cases, a faint copy of the agent | Human speaker (consented) or approved synthetic voice, mixed with noise |

Every scenario follows the same shape:

```
A0  agent opener          (2–4 s)    e.g. greeting + identity check
U0  user reply            (1–2 s)    a clean sample of THIS caller's voice (systems may enrol on it)
A1  agent utterance       (6–15 s)   the utterance under test; the event happens during or after it
U1… user audio under test (0.2–6 s)  backchannel, interruption, hesitation, silence, …
tail                      (≥ 2 s)    the agent keeps talking, or silence, so late decisions are visible
```

The agent's text for each utterance is streamed to the system the moment that utterance starts (see the
contract in [A-challenge-brief.md](A-challenge-brief.md#6-inputoutput-contract)). Systems may use it: real
agents know what they are saying.

### Agent voices

| Voice | Description |
|---|---|
| `agent-f1` | Female, Saudi "white" dialect (the neutral register Saudi service agents use), calm, about 4.5 syllables/s |
| `agent-m1` | Male, same register and pace |

The agent voices are synthetic on purpose. That keeps the agent channel identical in quality across every
scenario, so only the caller side varies.

### Brands and people

All companies, names and amounts are fictional: متجر نوّار (store), عيادات رِفاه (clinic), سَنا للاتصالات
(telecom), the bank (unnamed), مركز صيانة الوادي (car service), خدمة توصيل مِرسال (delivery). Do not swap in
real company names. Never use real phone numbers, national IDs or IBANs, even as examples.

---

## 2. Categories

| Code | Category | What happens | What a skilled human agent does |
|---|---|---|---|
| `GI` | Genuine interruption | The caller deliberately takes the floor: objects, corrects, asks, says stop | **STOP** |
| `BC` | Backchannel | «إيه»، «أها»، «مم»، «إيه والله صادق»: listening, not taking the floor | **KEEP** |
| `HT` | Hesitation & thinking | «اممم»، long pauses, number dictation, "let me check" | KEEP (during agent speech); **no premature TAKE_TURN** (during the caller's turn) |
| `BG` | Background | TV, traffic, other people talking, children, a kitchen | **KEEP** |
| `NV` | Non-speech from the caller's side | cough, laugh, sigh, throat-clear, speakerphone echo of the agent | **KEEP** |
| `AM` | Ambiguous tokens | «طيب»، «خلاص»، «أوكي»، «إنزين», where timing and tone decide | Labelled per event; often **EITHER** |
| `OV` | Overlap | Both start at once; the caller completes the agent's phrase; the caller talks over the agent | Labelled per event |
| `DV` | Dialect variation | The same functions in Hijazi, Southern, Eastern, Kuwaiti, Emirati, Bahraini/Qatari speech | Labelled per event |

Two categories beyond the brief's seven, because they are common in real calls:

- **`NV`** (caller-side non-speech): the sound comes from the caller, but it is not a bid for the floor.
  This includes the agent's own voice coming back through a speakerphone.
- **`BG`** (background): the sound comes from someone or something other than the caller.

Hard and adversarial cases are not a separate category. They are spread across all eight, and marked
`difficulty: 3` or `4`.

---

## 3. Decisions and labels

A system makes two kinds of decision:

| Decision | When it applies | Meaning |
|---|---|---|
| `STOP` | while the agent is speaking | "Yield the floor now: the caller wants to speak." Anything else means **KEEP** speaking. |
| `TAKE_TURN` | while the agent is silent and the caller is speaking or pausing | "The caller has finished; the agent may answer now." Anything else means **WAIT**. |

These are the three behaviours of the draft brief: KEEP SPEAKING, STOP AND LISTEN, WAIT. KEEP and WAIT
are the default, so a system only ever has to *send* `STOP` or `TAKE_TURN`. A system may also send
`RESUME` to take back a stop (scoring in D §2).

### 3a. Where labels come from

**What a skilled human agent would do, as judged by native speakers. Nothing else.**

- Every `expected`, `evident`, `hold` and `EITHER` comes from native-speaker annotators (E §7), never from
  running any system, Sarj's current system included.
- No scenario is chosen, kept or dropped because a particular system passes or fails it.
- Script writers choose cases because they occur in real Saudi and Gulf calls, not because they defeat
  some technique. The `Why` line of each scenario describes the human behaviour only.
- The set is frozen and hashed before any system (baselines, Sarj, teams) runs on it.

Each scenario carries one or more labelled **events**:

| `expected` | Used for | Pass condition (details in D) |
|---|---|---|
| `STOP` | the caller genuinely takes the floor | a `STOP` between `onset` and `deadline`; latency is measured from `evident` |
| `KEEP` | backchannel, noise, filler, echo | the agent keeps speaking from `onset` to `offset + 500 ms` (a stop taken back with `RESUME` within 500 ms earns half) |
| `EITHER` | truly ambiguous | both behaviours are acceptable; `preferred` gets full credit, the other partial |
| `TAKE_TURN` | the end of a caller's turn after a hesitant answer | no `TAKE_TURN` during a `hold: required` pause; then a `TAKE_TURN` soon after `turn_end` |

### The four time points of an event

```
agent:  ───────────────────────────────── A1 keeps talking ───────────────────────────►
user:              [ onset ··· evident ········· offset ]
                     ▲          ▲                   ▲              ▲
                   speech     a native listener     speech      deadline = evident + 1500 ms
                   starts     would now say         ends        (STOP events only)
                              "they want the floor"
```

- **`onset` / `offset`**: the caller's vocalisation starts and ends, from forced alignment of the
  clean caller stem.
- **`evident`** (STOP events only): the earliest moment a native listener, hearing the audio only up to
  that moment, judges that the caller wants the floor. It is measured with a gating test (E §7),
  not guessed. Example: in «أيوه… بس أبغى أغيّر الموعد», `evident` is at the end of «بس», not at
  «أيوه».
- **`deadline`**: `evident + 1500 ms` unless the scenario says otherwise. A `STOP` after it counts as a
  miss.

**Why latency starts at `evident`, not at `onset`.** A system that waits through «أيوه» until «بس»
arrives is behaving correctly and is not penalised for that wait. A system that stops at «أيوه» also passes
this event, but it pays on every `KEEP` event where «أيوه» really was a backchannel. That is how the scoring
removes the reward for stopping at every sound.

### Production constraints (so every event is scoreable)

1. For `STOP` events, the agent keeps talking **at least 2,000 ms after `evident`**. Otherwise the agent
   finishes on its own and the decision is untestable.
2. For `KEEP` events, the agent keeps talking **at least 1,000 ms after `offset`**.
3. Events in one scenario are **at least 1,500 ms apart**, so a decision belongs to one event only.
4. `TAKE_TURN` scenarios end with **at least 3,000 ms of silence** after `turn_end`, with background
   noise kept running so the silence is not digital zero.
5. Every scenario is rendered at 16 kHz, 16-bit, stereo. Narrowband (`phone-nb`) scenarios are band-limited
   and µ-law round-tripped at 8 kHz, then resampled to 16 kHz. Every stream uses the same format.

---

## 4. Script notation

| Notation | Meaning |
|---|---|
| `«…»` | exact words to say |
| `~` after a word | lengthened sound (اممم~، إيه~) |
| `↗` / `↘` | rising / falling intonation at the end of the word |
| `[pause 900]` | silence of 900 ms inside the caller's turn (not padded with breath) |
| `(soft)` `(loud)` `(fast)` `(smiling)` | delivery |
| `end(«word»)+250` | caller onset 250 ms after the agent finishes «word» in the current agent utterance |
| `start(«word»)+80` | caller onset 80 ms after the agent starts «word» |
| `hold: required` | a pause inside the caller's turn during which `TAKE_TURN` is wrong |
| `hold: optional` | a pause where taking the turn is acceptable (the answer is complete enough) |

Timings are **anchored to agent words**, not to absolute seconds. The production pipeline renders the
agent, force-aligns its words, places the caller clip at the anchor, and only then writes the final
millisecond labels into the JSON. That is how the timings stay true after re-rendering.

**Speakers are given the meaning and the delivery, not only the words.** A speaker may adjust a word to
their own dialect (a Najdi speaker saying «أبي» where the script says «أبغى»), as long as the function
stays the same. The final words they say go in the transcript, and the reviewer checks them (E §4).

---

## 5. Codes

### Dialect

| Code | Region | Typical markers used in scripts |
|---|---|---|
| `sa-najdi` | Riyadh, Qassim, central | إيه، إيوه، وش، أبي، زين، الحين، ترى، والله، صادق |
| `sa-hijazi` | Jeddah, Makkah, Madinah | أيوه، إيش، أبغى، دحين، كدا، برضو، مرّة، يا شيخ |
| `sa-southern` | Asir, Jazan, Najran, Al-Baha | Southern pronunciation and pace; markers checked by the dialect reviewer |
| `sa-eastern` | Al-Ahsa, Qatif, Dammam | إيه، زين، شنو، جذي (speaker-dependent) |
| `sa-northern` | Hail, Al-Jouf, Tabuk (Bedouin-influenced) | إيه، وش، زين; distinct intonation |
| `gulf-kuwaiti` | Kuwait | إي، شنو، شلون، جذي، وايد، إنزين، هالشي |
| `gulf-emirati` | UAE | هيه (yes)، شو، إنزين، وايد |
| `gulf-bahraini` / `gulf-qatari` | Bahrain / Qatar | إي، زين، شنو / شو; set by speaker |

The marker lists are a starting point for script writers. Any line in a dialect other than Najdi or Hijazi
needs a native speaker of that dialect to sign it off before recording.

### Acoustic condition

| Code | Description | Level |
|---|---|---|
| `quiet` | quiet room, handset microphone | SNR > 30 dB |
| `ac-hum` | split-AC hum, low steady noise (very common in Saudi homes and offices) | SNR 20–25 dB |
| `phone-nb` | telephone band: 300–3400 Hz, 8 kHz µ-law round trip | combine with any of the others |
| `car` | in-car road noise, hands-free microphone | SNR 10–15 dB |
| `street` | traffic, horns, motorbikes | SNR 5–12 dB |
| `cafe` | crowd babble, cups (Arabic and other languages) | SNR 5–10 dB |
| `tv` | Arabic TV in the room: news (MSA), a Gulf series, football commentary | 0–12 dB below caller speech |
| `household` | children, family members talking, kitchen (dishes, running water) | varies per scenario |
| `speakerphone` | the caller on speaker: room reverb + the agent's voice leaking into the caller channel | echo −15 to −25 dB, delay 150–250 ms |

### Difficulty

| Level | Meaning |
|---|---|
| 1 Easy | the intent is obvious from the words alone, in clean audio |
| 2 Medium | a listener needs one more dimension: timing, tone, what the agent just said, or ignoring noise |
| 3 Hard | several dimensions together, or the intent runs against the surface form (a long agreement, a one-word stop) |
| 4 Adversarial | minimal pairs, the agent's own echo, nearby speech in the same dialect, or surface cues that mislead |

---

## 6. Scenario block format

Every scenario in this file and in the hidden set uses this block:

```
#### <ID> · <expected> · <difficulty> · <dialect> · <acoustic>
Use case · agent voice · speaker (gender, age band)
A0 «…»   U0 «…»
A1 «…»
U1 «…»  (delivery)
Timing     anchor rule for each caller segment
Intent     is the caller trying to take the floor? yes / no / ambiguous
Expected   decision, with evident / hold / turn_end anchors
Sound      background and channel conditions
Why        what this case tests, and which shortcut it breaks
```

---

## 7. Public development set (24 scenarios)

Released to teams at kickoff: audio, these scripts, and the JSON labels. The scripts are public on
purpose: teams learn the format and the categories from them. The hidden set uses **different**
scripts, speakers and noise recordings.

Composition: 3 per category · 13 `KEEP` scenarios (one with three events, one with two), 7 `STOP`,
2 `EITHER`, 2 `TAKE_TURN` · 12 of 24 not Najdi (Hijazi 4, Kuwaiti 2, Emirati 1, Qatari 1, Bahraini 1,
Eastern 1, Southern 1, Northern 1).

### GI: Genuine interruption

#### GI-D01 · STOP · 1 Easy · sa-najdi · quiet
Delivery scheduling (متجر نوّار) · agent-f1 · male, 25–34
- A0 «السلام عليكم، معك ريم من متجر نوّار. أكلّم الأستاذ فهد؟»
- U0 «وعليكم السلام، إيه معك.»
- A1 «حياك الله يا أستاذ فهد. أتصل عليك بخصوص طلبك، الطلب بيوصلك بكرة إن شاء الله بين الساعة أربع وست العصر، والمندوب بيتصل عليك قبل ما يوصل بنص ساعة تقريباً، وإذا حاب تغيّر وقت التوصيل تقدر ترد على رسالة التأكيد اللي بتوصلك.»
- U1 «لا لا لحظة، بكرة أنا مو بالرياض.» (normal loudness, slightly fast)
- **Timing** U1 onset = end(«العصر»)+250
- **Intent** yes
- **Expected** STOP · evident = end(«لا لا») · deadline = evident+1500
- **Sound** quiet handset, SNR > 30 dB
- **Why** A plain correction: repeated «لا», then new information. Any listener would yield at once.

#### GI-D02 · STOP · 2 Medium · sa-hijazi · phone-nb + street
Clinic appointment reminder (عيادات رِفاه) · agent-f1 · female, 35–44
- A0 «مساء الخير، معك سلمى من عيادات رِفاه. معي الأستاذة هند؟»
- U0 «أيوه أنا هند، مساء النور.»
- A1 «أهلين أستاذة هند. حبيت أذكّرك بموعدك يوم الأحد الساعة عشرة الصبح مع الدكتورة نورة، ونرجو الحضور قبل الموعد بربع ساعة عشان إجراءات التسجيل، ولو عندك تأمين طبي لا تنسين تجيبين البطاقة معك.»
- U1 «أيوه~… بس أبغى أغيّر الموعد لو سمحتي، الأحد ما يناسبني.» («أيوه» lengthened, about 350 ms, then a 150 ms gap)
- **Timing** U1 onset = end(«الصبح»)+200
- **Intent** yes, but it opens like a backchannel
- **Expected** STOP · evident = end(«بس») · deadline = evident+1500
- **Sound** narrowband phone, street traffic at SNR 12 dB
- **Why** «أيوه» alone would be an acknowledgement; «بس» turns it into a request to change something. A good agent
  keeps going through «أيوه» and yields at «بس».

#### GI-D03 · STOP · 4 Adversarial · sa-najdi · car
Card activation (the bank) · agent-m1 · male, 45–54
- A0 «هلا، معك عبدالله من خدمة العملاء في البنك. أكلّم الأستاذ ماجد؟»
- U0 «إيه نعم، تفضل.»
- A1 «يا هلا. عشان نفعّل بطاقتك الجديدة بنرسل لك الحين رمز تحقق على جوالك، والرمز صالح لمدة خمس دقايق بس، ولا تشاركه مع أي أحد حتى لو قال لك إنه موظف في البنك، وبعد ما يوصلك الرمز بأطلب منك تقراه لي رقم رقم.»
- U1 «لحظة.» (firm, one word, normal loudness), then 2 s of silence with faint phone-handling rustle
- **Timing** U1 onset = end(«جوالك»)+300
- **Intent** yes: the caller asks the agent to pause while they open their messages
- **Expected** STOP · evident = end(«لحظة») · deadline = evident+1500
- **Sound** in-car road noise at SNR 15 dB, hands-free microphone
- **Why** One word, about 450 ms, said firmly while the agent is mid-instruction. In Saudi usage, «لحظة» addressed to
  the speaker means "hold on". A good agent pauses at once.

### BC: Backchannel

#### BC-D01 · KEEP · 1 Easy · sa-najdi · quiet
Plan renewal (سَنا للاتصالات) · agent-f1 · male, 18–24
- A0 «السلام عليكم، معك نوف من سَنا للاتصالات. معي الأستاذ سعد؟»
- U0 «إيه، وعليكم السلام.»
- A1 «حياك الله. حبيت أبلغك إن باقتك الحالية بتنتهي بعد ثلاث أيام، وعندنا لك باقة جديدة فيها مية قيقا إنترنت ومكالمات بدون حد داخل الشبكة، وسعرها تقريباً نفس سعر باقتك الحالية.»
- U1 «إيه.» (soft, falling ↘, about 250 ms)
- **Timing** U1 onset = end(«أيام»)+150 (a phrase boundary)
- **Intent** no
- **Expected** KEEP
- **Sound** quiet
- **Why** The textbook acknowledgement, at a phrase boundary, where acknowledgements naturally fall.

#### BC-D02 · KEEP ×3 · 2 Medium · sa-eastern · phone-nb
Instalment explanation (متجر نوّار) · agent-m1 · female, 25–34
- A0 «مساء الخير، معك خالد من متجر نوّار. معي الأستاذة ريم؟»
- U0 «إيه هلا.»
- A1 «هلا فيك. بس حبيت أوضح لك طريقة التقسيط: المبلغ الإجمالي ألف ومية وعشرين ريال، ينقسم على أربع دفعات، كل دفعة مئتين وثمانين ريال، أول دفعة تنخصم اليوم، والباقي كل شهر بنفس التاريخ، وقبل كل دفعة بيوصلك تذكير برسالة.»
- U1a «أها.» (soft) · U1b «إيه إيه.» (quick) · U1c «مم.» (closed mouth, about 300 ms)
- **Timing** U1a = end(«ريال» first)+100 · U1b = end(«دفعات»)+200 · U1c = end(«التاريخ»)+100
- **Intent** no (three times)
- **Expected** three KEEP events
- **Sound** narrowband phone, no added noise
- **Why** Three acknowledgements during one explanation. Each says "I'm following". Together they still do not
  mean the caller wants to talk.

#### BC-D03 · KEEP · 4 Adversarial · sa-najdi · ac-hum
Auto-pay setup (the bank) · agent-f1 · male, 35–44
- A0 «هلا، معك سارة من البنك. أكلّم الأستاذ ناصر؟»
- U0 «إيه نعم.»
- A1 «حياك الله. نبي نذكرك إنك تقدر تفعّل السداد التلقائي لفاتورة البطاقة، لأن كثير من العملاء يدفعون رسوم تأخير بدون ما ينتبهون، فالتفعيل التلقائي يريحك من هالشي، وتقدر توقفه أي وقت من التطبيق.»
- U1 «إيه والله صادق، صح.» (agreeing, medium pace, about 1.1 s, 4 words)
- **Timing** U1 onset = end(«ينتبهون»)+200
- **Intent** no: the caller agrees and does not want the floor
- **Expected** KEEP
- **Sound** AC hum at SNR 22 dB
- **Why** Agreement that is long (four words, over a second) but closes with a falling tone. The caller is
  endorsing what the agent says, not taking over.

### HT: Hesitation & thinking

#### HT-D01 · TAKE_TURN · 2 Medium · sa-najdi · quiet
Callback time (عيادات رِفاه) · agent-f1 · male, 55–64
- A0 «السلام عليكم، معك سلمى من عيادات رِفاه. معي الأستاذ عبدالرحمن؟»
- U0 «إيه نعم، وعليكم السلام.»
- A1 «حياك الله. وش أنسب وقت نتصل عليك فيه عشان نأكد موعد التحاليل؟» (then the agent is silent)
- U1 «اممم~ [pause 900] بعد المغرب [pause 700] يعني تقريباً الساعة سبع.»
- **Timing** U1 onset = end(A1)+600
- **Intent** caller turn; hesitant
- **Expected** pause after «اممم» = hold: required · pause after «المغرب» = hold: optional (that answer is
  already complete) · turn_end = end(«سبع»): TAKE_TURN expected after it
- **Sound** quiet
- **Why** The caller has started answering but is still searching: a filled pause, then silence. A good agent waits
  through it. After «بعد المغرب» the answer is usable, so taking the turn there is acceptable.

#### HT-D02 · TAKE_TURN · 3 Hard · sa-hijazi · phone-nb
Order number (خدمة توصيل مِرسال) · agent-m1 · female, 25–34
- A0 «أهلين، معك فيصل من مِرسال. معي الأستاذة لمى؟»
- U0 «أيوه معاك.»
- A1 «ممكن تعطيني رقم الطلب لو سمحتي؟ هو سبع أرقام، تلقينه في رسالة التأكيد.» (then silent)
- U1 «أيوه لحظة أشوفه… [pause 2200, faint screen tapping] ثلاثة، سبعة، تسعة [pause 1100] واحد، اثنين [pause 900] أربعة، صفر.»
- **Timing** U1 onset = end(A1)+500
- **Intent** caller turn; dictation
- **Expected** holds required after «أشوفه», «تسعة» and «اثنين» · turn_end = end(«صفر»)
- **Sound** narrowband phone
- **Why** Dictation in groups. «لحظة أشوفه» asks the agent to wait, and the agent itself said the number has seven
  digits. Answering before the last digit loses the number.

#### HT-D03 · KEEP · 2 Medium · gulf-bahraini · quiet
Delivery options (متجر نوّار) · agent-f1 · female, 18–24
- A0 «هلا، معك ريم من متجر نوّار. معي الأستاذة جود؟»
- U0 «إيه هلا.»
- A1 «حياك الله. عندنا ثلاث خيارات للتوصيل: العادي خلال ثلاث إلى خمس أيام ومجاني، والسريع بكرة برسوم خمسة وعشرين ريال، أو تستلمينه من الفرع بنفس اليوم.»
- U1 «اممم~» (low, thinking, about 700 ms)
- **Timing** U1 onset = end(«ومجاني»)+300
- **Intent** no: the caller is thinking while listening to the options
- **Expected** KEEP
- **Sound** quiet
- **Why** Thinking aloud while listening to the options. The caller is not ready to speak yet.

### BG: Background

#### BG-D01 · KEEP · 1 Easy · gulf-qatari · tv
Plan offer (سَنا للاتصالات) · agent-m1 · male, 35–44
- A0 «السلام عليكم، معك خالد من سَنا للاتصالات. معي الأستاذ تركي؟»
- U0 «إيه، تفضل.»
- A1 «حياك الله. عندك عرض على باقة الألياف المنزلية، سرعة خمس مية ميقا، وأول شهرين بنص السعر، والتركيب مجاني، وتقدر تحدد موعد الفني اللي يناسبك.»
- U1 none: the caller is silent. TV news (MSA anchor) plays across the whole clip
- **Timing** TV from t=0 to the end; the TV level does not change during A1
- **Intent** no
- **Expected** KEEP (one event spanning A1)
- **Sound** TV news 12 dB below the caller's U0 level, room reverb
- **Why** Distant, formal-Arabic TV speech. It is not the caller, and not addressed to the agent.

#### BG-D02 · KEEP ×2 · 2 Medium · sa-southern · street + phone-nb
Car service booking (مركز صيانة الوادي) · agent-f1 · male, 25–34
- A0 «هلا، معك نوف من مركز صيانة الوادي. معي الأستاذ بدر؟»
- U0 «إيه معك، أنا بالشارع بس تفضلي.»
- A1 «حياك الله. سيارتك عليها صيانة العشرين ألف كيلو، وعندنا مواعيد متاحة يوم الأربعاء والخميس، والصيانة تاخذ تقريباً ساعتين، وفيه خصم عشرة بالمية لو حجزت هالأسبوع.»
- U1 none: a horn (400 ms) at end(«الكيلو»)+0, a motorbike passing (2 s) during «والصيانة…ساعتين»
- **Timing** as above
- **Intent** no
- **Expected** KEEP (two events: horn, motorbike)
- **Sound** street at SNR 8 dB, narrowband
- **Why** Loud, sudden street sounds. Nothing was said to the agent.

#### BG-D03 · KEEP · 4 Adversarial · sa-najdi · household
Clinic follow-up (عيادات رِفاه) · agent-f1 · female, 35–44 · second speaker: child, 8–11, near the phone
- A0 «مساء الخير، معك سلمى من عيادات رِفاه. معي أم عبدالله؟»
- U0 «إيه نعم، مساء النور.»
- A1 «أهلين. حبيت أبلغك إن نتايج التحاليل طلعت، والدكتور حاب يشوفك الأسبوع الجاي عشان يشرح لك النتايج، وعندنا موعد الثلاثاء الساعة أربع أو الأربعاء الساعة عشرة الصبح.»
- U1 child: «يُمّه! وين الشاحن؟ يُمّه!» (loud, about 1.5 m from the phone); the caller stays silent
- **Timing** child onset = end(«طلعت»)+300
- **Intent** no: it is not the caller
- **Expected** KEEP
- **Sound** household room tone; child about 6 dB below the caller's U0 level
- **Why** A child near the phone, in the same dialect, calling someone else in the room. The caller says nothing; a
  good agent carries on.

### NV: Non-speech from the caller's side

#### NV-D01 · KEEP · 1 Easy · sa-northern · quiet
Delivery address check (مِرسال) · agent-m1 · male, 45–54
- A0 «هلا، معك فيصل من مِرسال. أكلّم الأستاذ سلطان؟»
- U0 «إيه نعم.»
- A1 «يا هلا. بس أبي أتأكد من العنوان: حي النرجس، شارع الأمير سعود، فيلا رقم اثنعش، وجنبها مسجد، صح كذا؟ عشان المندوب ما يتوه.»
- U1 two coughs (about 400 ms each, 300 ms apart)
- **Timing** onset = end(«سعود»)+200
- **Intent** no
- **Expected** KEEP
- **Sound** quiet
- **Why** Coughing. The caller is not trying to speak.

#### NV-D02 · KEEP · 2 Medium · sa-hijazi · quiet
Survey intro (متجر نوّار) · agent-f1 · female, 25–34
- A0 «أهلين، معك ريم من متجر نوّار. معي الأستاذة رهف؟»
- U0 «أيوه أنا.»
- A1 «أهلين رهف. عندنا استبيان قصير مرة، والله ما ياخذ أكثر من دقيقة، وأوعدك ما راح أسألك عن رقم جوالك ولا عن عمرك، بس عن تجربتك مع آخر طلب.»
- U1 a short laugh «ههههه» (smiling, about 800 ms), no words
- **Timing** onset = end(«دقيقة»)+150
- **Intent** no
- **Expected** KEEP
- **Sound** quiet
- **Why** Laughter at the agent's line: a sign of rapport, not a bid for the floor.

#### NV-D03 · KEEP · 4 Adversarial · sa-najdi · speakerphone
Plan change confirmation (سَنا للاتصالات) · agent-m1 · male, 25–34 (on speaker, phone on a table)
- A0 «هلا، معك خالد من سَنا للاتصالات. معي الأستاذ يزيد؟»
- U0 «إيه معك، تفضل.» (with room reverb)
- A1 «يا هلا. تم تغيير باقتك للباقة الجديدة، والتغيير بيصير فعّال من بداية الشهر الجاي، وبتوصلك رسالة فيها التفاصيل، وما فيه أي رسوم إضافية على التغيير.»
- U1 none from the caller. The caller channel carries the agent's own voice as echo, plus breathing and a
  chair creak
- **Timing** echo for the full length of A1
- **Intent** no
- **Expected** KEEP (one event spanning A1)
- **Sound** speakerphone: echo −18 dB, 220 ms delay, reverb time about 0.4 s
- **Why** The caller is on speakerphone and silent. What reaches the caller's microphone is the agent's own voice
  coming back. Nobody new is talking.

### AM: Ambiguous tokens

#### AM-D01 · EITHER (prefer KEEP) · 2 Medium · sa-najdi · quiet
Payment reminder (متجر نوّار) · agent-f1 · male, 35–44
- A0 «السلام عليكم، معك ريم من متجر نوّار. معي الأستاذ عمر؟»
- U0 «إيه، وعليكم السلام.»
- A1 «حياك الله. حبيت أذكرك إن عندك دفعة بقيمة ثلاث مية وخمسين ريال مستحقة يوم الخميس، وتقدر تسددها من التطبيق أو من الرابط اللي بيوصلك برسالة.»
- U1 «طيب.» (short, falling ↘, about 300 ms; no continuation)
- **Timing** onset = end(«الخميس»)+200
- **Intent** ambiguous; most likely acknowledgement
- **Expected** EITHER, preferred KEEP
- **Sound** quiet
- **Why** «طيب» can mean "okay, go on" or "okay, now me". A falling tone and nothing after it suggest an
  acknowledgement. Stopping is not wrong, only less natural.

#### AM-D02 · STOP · 3 Hard · sa-najdi · phone-nb
Same use case as AM-D01, a different speaker · agent-f1 · male, 25–34
- A0 «السلام عليكم، معك ريم من متجر نوّار. معي الأستاذ مشعل؟»
- U0 «إيه نعم.»
- A1 «حياك الله. حبيت أذكرك إن عندك دفعة مستحقة يوم الخميس، وتقدر تسددها من التطبيق أو من الرابط اللي بيوصلك برسالة، وإذا سددت قبل الموعد ما فيه أي رسوم.»
- U1 «طيب↗… [pause 400] بس كم المبلغ بالضبط؟»
- **Timing** onset = end(«الخميس»)+200
- **Intent** yes: the rising «طيب» was a floor bid, confirmed by «بس»
- **Expected** STOP · evident = end(«بس») · deadline = evident+1500
- **Sound** narrowband
- **Why** Pair with AM-D01: the same word, with a rising tone, a pause and then a question. The caller is taking the
  floor, and by «بس» it is unmistakable.

#### AM-D03 · EITHER (prefer STOP) · 3 Hard · sa-najdi · quiet
Terms explanation (the bank) · agent-m1 · male, 45–54
- A0 «هلا، معك عبدالله من البنك. أكلّم الأستاذ فيصل؟»
- U0 «إيه نعم.»
- A1 «يا هلا. زي ما ذكرت لك قبل شوي، الرسوم السنوية مجانية أول سنة، وبعدها مية وخمسين ريال، يعني أول سنة ما راح تدفع شي، وبعد السنة الأولى تصير الرسوم مية وخمسين…»
- U1 «خلاص خلاص، فهمت.» (fast, slightly impatient)
- **Timing** onset = end(«شي»)+150 (the agent is clearly repeating itself)
- **Intent** ambiguous, leaning yes: "enough, I got it"
- **Expected** EITHER, preferred STOP · evident = end(«خلاص خلاص»)
- **Sound** quiet
- **Why** «خلاص» can mean "agreed" or "enough, I've got it". Here the agent is repeating itself and the caller
  speaks fast, so most listeners hear "enough".

### OV: Overlap

#### OV-D01 · STOP · 2 Medium · sa-najdi · quiet
Appointment booking (مركز صيانة الوادي) · agent-f1 · male, 25–34
- A0 «هلا، معك نوف من مركز صيانة الوادي. معي الأستاذ راكان؟»
- U0 «إيه هلا.»
- A1a «تحب أحجز لك موعد يوم الأحد الساعة عشرة الصبح؟»
- (the agent waits 900 ms, hears nothing, and goes on)
- A1b «وإذا ما يناسبك عندنا كمان يوم الثلاثاء بعد الظهر، ويوم الخميس من الساعة ثمان الصبح.»
- U1 «إيه الأحد زين، احجزه.»
- **Timing** U1 onset = start(A1b)+80. Both start almost together
- **Intent** yes: it is the answer to the agent's question
- **Expected** STOP · evident = end(«الأحد») · deadline = evident+1500
- **Sound** quiet
- **Why** Both start at once after a gap. «إيه» first sounds like an acknowledgement, but the agent had just asked
  a question, and this is the answer.

#### OV-D02 · KEEP · 3 Hard · sa-hijazi · quiet
Document request (the bank) · agent-f1 · female, 35–44
- A0 «أهلين، معك سارة من البنك. معي الأستاذة أمل؟»
- U0 «أيوه، تفضلي.»
- A1 «حياك الله. عشان نكمل طلب التمويل نحتاج منك صورة من الهوية الوطنية سارية المفعول، وتعريف بالراتب ما يكون أقدم من شهر، وكشف حساب لآخر ثلاث شهور.»
- U1 «الوطنية، أيوه، معايا.» (overlapping the agent, soft, completing the phrase)
- **Timing** U1 onset = start(«الوطنية»)+100, so the caller says the word with the agent
- **Intent** no: the caller is completing the phrase to show they are following
- **Expected** KEEP
- **Sound** quiet
- **Why** The caller says the agent's word along with it: supportive overlap. A human agent keeps going.

#### OV-D03 · STOP · 1 Easy · sa-najdi · ac-hum
Complaint follow-up (سَنا للاتصالات) · agent-m1 · male, 45–54
- A0 «هلا، معك خالد من سَنا للاتصالات. معي الأستاذ فهد؟»
- U0 «إيه نعم.»
- A1 «يا هلا. بخصوص البلاغ اللي رفعته عن ضعف الشبكة، حبيت أبلغك إن الفريق الفني يشتغل عليه، والمدة المتوقعة للحل من ثلاث إلى خمس أيام عمل، وبنرسل لك رسالة أول ما ينحل.»
- U1 «يا أخي هذا ثالث مرة أسمع نفس الكلام! صار لي أسبوعين والشبكة عندي زفت.» (loud, sustained, about 3.5 s)
- **Timing** U1 onset = end(«عليه»)+100
- **Intent** yes
- **Expected** STOP · evident = onset+400 (loud, sustained speech) · deadline = evident+1500
- **Sound** AC hum at SNR 22 dB
- **Why** The caller talks over the agent, loudly, and keeps going. A good agent yields within a syllable or two,
  without waiting for the caller to finish.

### DV: Dialect variation

#### DV-D01 · KEEP · 2 Medium · gulf-kuwaiti · phone-nb
Delivery confirmation (مِرسال) · agent-m1 · male, 25–34
- A0 «هلا، معك فيصل من مِرسال. معي الأخ عبدالله؟»
- U0 «إي نعم، هلا.»
- A1 «يا هلا. طلبك طلع من المستودع اليوم الصبح، والمفروض يوصلك خلال يومين، والمندوب بيتواصل معك على نفس الرقم، ولو ما رديت عليه بيحاول مرة ثانية بعد ساعة.»
- U1 «إي إي… زين.» (soft, the Kuwaiti «إي» rather than «إيه»)
- **Timing** onset = end(«الصبح»)+150
- **Intent** no
- **Expected** KEEP
- **Sound** narrowband
- **Why** A Kuwaiti acknowledgement, shorter and voiced differently from Najdi «إيه». The same function: "I'm
  following".

#### DV-D02 · KEEP · 2 Medium · gulf-emirati · car
Car service reminder (مركز صيانة الوادي) · agent-f1 · male, 35–44
- A0 «مساء الخير، معك نوف من مركز صيانة الوادي. معي الأخ حمد؟»
- U0 «هيه، مساء النور.»
- A1 «أهلين. حبيت أذكرك إن موعد تغيير الزيت لسيارتك بكرة الساعة تسع الصبح، والفحص بيشمل الفرامل والإطارات، والمدة تقريباً ساعة.»
- U1 «هيه… هيه.» (Emirati "yes", soft, two tokens 600 ms apart)
- **Timing** onset = end(«الصبح»)+200
- **Intent** no
- **Expected** KEEP
- **Sound** in-car noise at SNR 12 dB
- **Why** «هيه» means "yes" in Emirati speech. Here it is an acknowledgement.

#### DV-D03 · STOP · 2 Medium · gulf-kuwaiti · quiet
Unwanted order (متجر نوّار) · agent-m1 · male, 45–54
- A0 «هلا، معك خالد من متجر نوّار. معي الأخ بدر؟»
- U0 «إي نعم.»
- A1 «يا هلا. حبيت أأكد معك طلب الاشتراك الشهري بخدمة التوصيل المجاني، والاشتراك بيتجدد تلقائياً كل شهر بتسعة وعشرين ريال، وتقدر تلغيه أي وقت من التطبيق.»
- U1 «اسمع اسمع، أنا ما طلبت هالشي أصلاً.» (firm)
- **Timing** onset = end(«المجاني»)+200
- **Intent** yes
- **Expected** STOP · evident = end(«اسمع اسمع») · deadline = evident+1500
- **Sound** quiet
- **Why** «اسمع» opens a turn in Gulf speech: the caller is taking the floor to object.

---

## 8. Hidden evaluation set: composition rules

The hidden and checkpoint scripts are held by the organisers, outside this repository. They must
satisfy these rules, which are public. Teams may read this section.

| Rule | Value |
|---|---|
| Scenarios | 64 scripted + 16 naturalistic, all scored; + 8 reserve (used only to replace a broken recording) |
| Per category | scripted: 8 in each of GI, BC, HT, BG, NV, AM, OV, DV. Naturalistic: categories assigned per event after labelling |
| Naturalistic slice | 16 scenarios cut from unscripted sessions (E §5). **Every** caller vocalisation in a cut is labelled, none selected; a cut is chosen only by a random draw over the session timeline, before labelling |
| Expected-decision mix (definite events) | about 45 % STOP, 45 % KEEP, 10 % EITHER; balanced so that "always stop" and "never stop" both score near zero (D §3) |
| End-of-turn scenarios | 8 (inside HT, AM and DV) |
| Difficulty (scripted part) | at least 10 % easy and 20 % medium; adversarial at most 45 % (minimal-pair members, echo and same-dialect side speech count as adversarial under the behavioural definition in §5) |
| Dialects | at most 40 % Najdi; at least 15 % Hijazi; at least 25 % Gulf (non-Saudi); Southern, Eastern and Northern Saudi together at least 15 % |
| Speakers | at least 16 different callers, balanced by gender; ages 18–65; **no dev-set speaker** |
| Acoustic | at least 40 % `phone-nb`; every condition in section 5 appears at least 4 times; **no noise recording reused from the dev set** |
| Minimal pairs | at least 6 pairs: the same agent utterance, near-identical words, different intent (for example «طيب↘» against «طيب↗ + بس») |
| Real-time stream | the 80 scenarios are concatenated in a fixed random order, with 1.5 s of room tone between them, into a single run of about 28–32 minutes |
| Independence | written, labelled and reviewed by people who do not work on Sarj's turn-taking system; frozen and hashed before any system runs on it (§3a, D §1) |

---

## 9. Out of scope (deliberately)

- **Full-duplex dialogue.** The agent audio is fixed. A `STOP` does not change what the caller says
  next (an open-loop benchmark, D §1). The live showcase renders what the call would have sounded
  like, for the judges.
- **What the agent says after stopping.** Turn-taking only, not response generation.
- **English-only calls.** Short code-switches («أوكي», «سوري», «yes yes») appear inside Arabic speech in
  `DV` and `AM`. That is normal Saudi speech, not a separate language track.
