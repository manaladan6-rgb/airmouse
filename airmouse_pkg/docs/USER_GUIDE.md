# AirMouse v15 User Guide — DO IT WITH ME, onboarding, badges, privacy

Chapters §16 (DO IT WITH ME), §17/§18 (onboarding + accessibility) and
§33 (HUD badges) of the v15 spec. Companion: `docs/ACCESSIBILITY_GUIDE.md`,
`docs/PRIVACY.md`. Status: **SIMULATION-VERIFIED** — the interaction
model is fully tested in simulation; webcam/microphone/gaze/RF hardware
remain NOT PHYSICALLY VERIFIED (unchanged since v11.5).

---

## 1. DO IT WITH ME (§16) — the flagship experience

You give a **goal**; AirMouse proposes a plan; you stay in control at
every step. Nothing runs before you approve it.

### The walkthrough

1. **You state a goal** — e.g. *"prepare the quarterly report"*.
2. **AirMouse understands** — a deterministic parser classifies it
   (command / intent / task / goal) and, if it's unsure, it asks rather
   than guesses.
3. **AirMouse inspects context** — the current world model state (app,
   window, files) becomes the proposal's CURRENT STATE and SOURCES.
4. **You receive a structured proposal**:

   ```
   OBJECTIVE        prepare the quarterly report
   PLAN             1. gather context …  2. perform …  3. verify outcome …
   SOURCES          world model snapshot, deterministic parser
   CURRENT STATE    (what is on screen right now, from the world model)
   RISKS            requires permissions: …  /  destructive operation
                    present — explicit confirmation required
   REQUIRED ACTIONS the exact steps it wants to run
   APPROVAL STATE   pending
   ```

5. **You choose:**

   | Verb | What it does |
   |---|---|
   | **START** | run the approved plan (destructive steps still ask per-run) |
   | **EDIT PLAN** | adjust the steps — allowed any time before approval |
   | **PAUSE** | freeze execution; resume later with START |
   | **STOP** | cancel immediately — the human stop always wins |
   | **CHANGE DIRECTION** | stop the current plan and propose a fresh one from your new goal |

6. **AirMouse executes, observes and verifies** every step, and reports
   progress (0..1) as it goes.
7. **You correct it in words** — corrections are recorded and (if you
   enabled the personal twin) learned from, so the next proposal fits
   better.

### The guarantees

* **PREDICTION ≠ PERMISSION ≠ EXECUTION** — a proposal is only a
  proposal; nothing executes before approval, and approval never lifts
  destructive-step confirmations.
* Every state change is explainable (§24): you can always ask why a
  step was proposed, why a target was chosen, why a confirmation was
  requested, why something failed, why recovery worked the way it did,
  and which learned preference influenced the plan.

## 2. Zero-learning-curve onboarding (§17)

First run asks for **one choice** — not dozens of settings:

| Entry choice | Starts with | Confirmation style |
|---|---|---|
| **voice** | voice modality | spoken |
| **hands** | hand/gesture modality | dwell |
| **eyes** | gaze modality (large UI on) | dwell |
| **keyboard** | keyboard only | explicit key |
| **automatic** | keyboard + voice | explicit key |
| **all** | hand + gaze + voice + keyboard | explicit key |

The moment you pick, AirMouse is **usable** (INSTALL → LAUNCH → USE).
From there it learns your preferences progressively and quietly — every
choice stays changeable later.

## 3. Accessibility modes (§18) — architecture, not a theme

Eight modes, each guaranteed at least one modality and a configurable
confirmation path:

`voice-only` · `gesture-only` · `gaze-only` · `keyboard-only` ·
`switch-access` · `hybrid` · `hands-free` · `low-mobility`

Large-UI, high-contrast and reduced-motion are architectural flags for
the modes that need them (gaze-only, low-mobility, switch-access) —
not a cosmetic overlay. Details: `docs/ACCESSIBILITY_GUIDE.md`.

## 4. What the HUD badges mean (§33)

The on-screen HUD shows badges so you always know who is doing what:

| Badge | Meaning |
|---|---|
| `AGENT:` | **an AI agent is controlling the computer right now** (the agent's id) — the user must ALWAYS know |
| `TASK:` | a structured task is active, with its progress label |
| `CONFIRM?` | an action is waiting for your confirmation — nothing happens until you answer |
| `RECOVER:` | the recovery engine is actively retrying/re-targeting after a failure |

They appear alongside the v11.5 badges (`AI:` model, `MODE:`,
`SUG:` suggestion, transcript caption) and the v9/v5 badges (`KALMAN`,
`CAL`, `DIRECT`, precision, e-stop, …).

**If you ever see `AGENT:` and did not start an agent:** press the
e-stop (hotkey `x` / ESC path) or use human override — agents are
suspended instantly and their resource leases are released. The
hierarchy guarantees you win: E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
PERMISSION > AGENT > PREDICTION.

## 5. Privacy controls (§21 continuity)

* **Telemetry is structurally OFF.** There is no cloud code path;
  nothing phones home. (A v15 hardening pass found and fixed a config
  defect where a legacy v9 performance-report flag could collide with
  the privacy telemetry setting — telemetry now stays authoritatively
  OFF; see CHANGELOG.)
* **The Personal Interaction Twin (§2)** is optional and local. You
  control it fully:
  * **inspect** — list the patterns it knows (categorical patterns,
    never your content);
  * **forget** — delete any single learned fact;
  * **export** — take your learned data with you (bounded JSON);
  * **import** — bring data in (validated, secrets refused);
  * **reset** — wipe everything with one action.
* **Skills are yours** — inspectable, editable, revocable. Nothing is
  ever learned into a skill silently: repetition → notification →
  preview → your approval.
* **Licensing is transparent** (§19) — the FREE tier is the complete
  local core; higher tiers only add; the license state is inspectable
  locally and never phones home.

## 6. Where things live

| You want to… | Use |
|---|---|
| see who/what is active | HUD badges; `airmouse status` |
| give a goal | DO IT WITH ME propose → approve → run |
| pause/stop everything | PAUSE / STOP (human stop always wins); e-stop for agents |
| check learned patterns | `airmouse twin` |
| see your skills | `airmouse skills` |
| see which agents exist | `airmouse agents` |
| check permission decisions | `airmouse permissions` |
| inspect the protocol | `airmouse protocol` |
