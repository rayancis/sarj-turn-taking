# Public demo site: deployment and updates

The public site is the same demo as `demo/server.py` (same page, same detector code path), started with
`--public`. It runs on **Render** (free web service, Docker, Frankfurt), built from branch `feat/demo-ui` of the
private GitHub repository. The repository stays private: Render reads it through its GitHub app; visitors only
reach the HTTP routes listed below.

## What visitors get

A normal HTTPS link (`https://<service-name>.onrender.com`) that opens in Chrome or Safari, desktop or phone, with no
install, account or sign-in. They pick one of 6 original demo calls and either detector (current `my_system.py` =
E01b, or Baseline `bd2a3f0`), press Play, and watch the real detector's decisions.

## Data rights decision

The competition dev set (`data/dev`) has **no public redistribution licence** in the kit (CHALLENGE.md section 10,
DATA.md, README.md, SUBMISSION.md); the owner's approval covers this private repository only. So the public site
never serves it:

- `demo/public_data/` holds 6 **original** scenarios made by `demo/public_data/make_public_data.py`: scripts written for
  the demo, eSpeak NG's rule-based Arabic voice (no recorded speech samples; the GPL covers the program, not its
  audio output), procedural noise, and our own script-derived labels. Licence: CC BY 4.0 (change in the manifest if
  the team prefers another).
- Public mode refuses to start with any folder under `data/`, or with a folder whose `manifest.json` does not
  declare `"distribution": "public"` and a `"license"`.
- The Docker image copies an explicit whitelist (`demo/Dockerfile`): `data/`, `experiments/`, `baseline/`, tests,
  documents and `.git` are not in the image at all.

## Public routes (everything else is 404)

| Route | Content |
|---|---|
| `/`, `/app.js`, `/style.css` | the page |
| `/api/config` | scenario list, detector names and revisions (no file paths, hashes, errors or dev-set scores) |
| `/api/scenario/<id>`, `/api/audio/<id>.wav` | public demo scenarios only |
| `POST /api/session`, `/api/session/<id>/advance`, `/close` | detector sessions |
| `/healthz`, `/robots.txt` | health check; robots disallowed, `X-Robots-Tag: noindex` |

## Limits (public mode)

| Limit | Value | Why |
|---|---|---|
| Simultaneous plays | 12 (env `SARJ_DEMO_MAX_SESSIONS`) | memory and the free tier's small CPU; a 13th visitor sees "at capacity, try in a minute" |
| Idle playback kept | 10 min; at capacity an idle (>60 s) one is replaced | frees memory |
| New plays | 40 per minute, all visitors together | stops scripted floods |
| Playhead posts | 20 per second per play (the page sends 10) | |
| Detector speed | at most 1.5x real time + 1.8 s per play | a client cannot burn CPU by running whole calls instantly |
| Request body | 2 KB | |
| Connections | 48 at once; idle keep-alive closed after 30 s | |
| Headers | strict CSP, no framing, no referrer, `nosniff` | |

Network delay: the hosted detector is fed up to **0.8 s ahead** of what the visitor hears, and the page applies each
decision when playback reaches the frame time where the detector issued it. Each decision still uses only audio up
to its own frame (the detector is causal), and the decisions are identical to the kit's replay (tests).
Measured with 150 ms of emulated latency: STOP applied 0.5 ms after its frame time.

## One-time setup (needs the repository owner's accounts; about 5 minutes, no card)

1. Go to https://render.com and choose **Sign up with GitHub** (free "Hobby" workspace).
2. When GitHub asks which repositories Render may access, choose **Only select repositories** and pick
   `rayancis/sarj-turn-taking`. The repository stays private.
3. In the Render dashboard: **New +** then **Web Service**, then select `rayancis/sarj-turn-taking`, and set:
   - Name: `sarj-demo` (becomes `https://sarj-demo.onrender.com`; Render adds a suffix if the name is taken)
   - Language: **Docker**; Branch: **`feat/demo-ui`**; Region: **Frankfurt (EU Central)**
   - Root directory: leave empty. Dockerfile path: **`./demo/Dockerfile`**; Docker build context: **`.`**
   - Instance type: **Free**
   - Advanced: Health check path **`/healthz`**; Auto-deploy **On commit**; optional env var `SARJ_DEMO_MAX_SESSIONS=12`
4. **Create Web Service**. The first build takes 2-4 minutes. When the log shows
   `SARJ demo (PUBLIC): 6 calls ... Listening on 0.0.0.0:10000`, open the `.onrender.com` link at the top.

(Equivalent: **New + > Blueprint** with the same repository and branch `feat/demo-ui`; it reads `render.yaml`.)

## Verify before sharing

```bash
curl -s https://sarj-demo.onrender.com/healthz
```
```bash
node demo/browser_check.js https://sarj-demo.onrender.com/
```

The browser check (Node + Playwright) plays a real interruption and a real KEEP call, pauses, resets, switches
scenario and detector, and checks the phone layout: all must PASS.

## Costs and limitations of the free plan

Render documents and reviews (Nov 2026) describe the free web service as: $0 with no card, **spins down after 15 minutes
without traffic and takes about a minute to wake** on the next visit, 512 MB RAM and a small shared CPU, and 750 free
instance hours per workspace per month (enough for one always-on service). Check the current figures in the Render
dashboard. Practical consequences:

- Open the link yourself a minute or two before a presentation, so the first teammate does not wait.
- If 750 hours run out (other free services in the same workspace), Render pauses free services until the next month.
- For no sleeping and more CPU, switch the instance to a paid plan in the service settings (Starter was reported at
  about $7/month).
- Audio is synthetic and robotic on purpose; scores here are not benchmark results.

## Updating the site

Every push to `feat/demo-ui` redeploys automatically (2-4 minutes). Typical updates:

- **Show a newer detector**: bring the new `my_system.py` onto the demo branch, then push.
  ```bash
  git checkout feat/demo-ui
  ```
  ```bash
  git checkout <experiment-branch> -- my_system.py
  ```
  ```bash
  git commit -m "demo: show <experiment>" && git push
  ```
  The "current" entry follows `my_system.py`. To display a short name instead of its hash, add the file's SHA-256 to
  `KNOWN_SOURCES` in `demo/server.py`. Run `python -m pytest -q` first: the tests check the demo against the kit's replay.
- **Add a pinned comparison version**: copy that version's file to `demo/models/`, add it to `DEFAULT_MODELS`.
- **Change scenarios**: edit `SCENARIOS` in `demo/public_data/make_public_data.py`, run it (needs `espeak-ng` and
  `ffmpeg`), run the tests, commit the regenerated `audio/`, `labels/` and `manifest.json`.
- **Roll back**: Render dashboard > the service > Events/Deploys > Rollback.
- **Take the site down**: Render dashboard > the service > Settings > Suspend (or Delete).
