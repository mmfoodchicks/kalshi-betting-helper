# DraftKings usernames in the public repository: what was done, and a cleanup plan for approval (2026-11-26)

## What was exposed

Three DraftKings showdown standings exports were committed on 2026-09-19 as S7 inputs. That was before the
rule that standings exports never enter git. Each row carries an entrant's DraftKings username, an EntryId and
the lineup.

| file | added in | contest |
|---|---|---|
| `research/data/dk_standings/contest-standings-195677825.csv.gz` | `479caa2` (2026-09-19 11:53 UTC) | DET @ BUF showdown, 88,235 rows |
| `research/data/dk_standings/contest-standings-193391013.csv.gz` | `672b83b` (2026-09-19 12:12 UTC) | NE @ SEA showdown, 126,020 rows |
| `research/data/dk_standings/contest-standings-195526287.csv.gz` | `672b83b` | DEN @ KC showdown, 88,235 rows |

**No other tracked file carries entrant data.** Every tracked file was checked by name and by DraftKings'
header. The other matches were synthetic test fixtures with made-up handles. The repository is public, has
no forks and has no pull requests. Of the remote branches, only `claude/kalshi-crypto-predictor-ckutwm`
contains these commits. `sim-history` and `claude/kalshi-crypto-predictor-explore-5x0lvp` do not.

## Done today (a normal commit, no history rewrite)

- **The three files are removed from the tree.** The manifest keeps every hash and row count, so a private copy
  can always be verified. Each entry says why and when it was removed.
- **Private copies.** The research scripts read them from the same paths, which are now gitignored. This session
  holds copies.
  - **On the PC:** the next automatic pull deletes the tracked copies from the checkout, as git does for any file
    removed upstream. To restore them as private, ignored files (one line, in a second Command Prompt in the
    checkout):

        git restore --source=27eacbd -- research/data/dk_standings/contest-standings-193391013.csv.gz research/data/dk_standings/contest-standings-195526287.csv.gz research/data/dk_standings/contest-standings-195677825.csv.gz

  - This works until the history is rewritten (below), and the owner's original zips are pinned in the manifest
    by sha256.
- **Aggregates stay.** `research/data/s7_field.json`, the S7 reports and every `dk_fields` summary hold no
  entrant data and are unchanged.
- **Safeguards.**
  - The ignore rules cover every standings path (`**/contest-standings-*` and the `dk_standings` folder's
    csv/gz/zip).
  - `scripts/secret-scan.sh`, run before every commit, now refuses a staged export. It checks by name, and by
    DraftKings' header in the file's first 4 KB, gzipped or not, whatever the file is called. It was tested on
    a renamed plain-text export and a renamed gzipped one; both were refused.
  - A suite guard fails the build if any tracked file is an export, by name or by header.

**What this does NOT do.** Deleting a file in a commit leaves its bytes in every earlier commit. Anyone can
still fetch `479caa2` or `672b83b` from GitHub and read the usernames.

## Before any cleanup: what each option breaks

There are two ways to take the old commits out of public view. Both are your decision. Neither has been done.

### Option A: make the repository private (recommended if it fits)

- **What it fixes.** The history stops being public at once. No commit changes, so every hash cited anywhere
  stays valid.
- **The PC worker.** It pulls anonymously over HTTPS (`origin https://github.com/mmfoodchicks/...`, and pc_loop
  runs `git fetch` then `git pull --ff-only`). Once the repo is private, that fetch fails until the PC has
  credentials.
  - pc_loop does not stop: it reports the pull error on the light and keeps building on the code it has.
  - **The fix, before flipping:** sign in once on the PC, either with Git Credential Manager (Git for Windows
    prompts on the first fetch) or with a fine-grained token limited to this repository, read-only.
- **Render.** It deploys through its GitHub app. Confirm in Render's settings that the app has access to private
  repositories. Otherwise the next deploy fails, while the running service is unaffected.
- **GitHub Actions.** A private repository on the Free plan has 2,000 included minutes a month. This repo ran
  about 32 workflow runs in the last 7 days; the guard suite is about 8-10 minutes a push.
  - Check the Actions usage page before flipping.
  - Workflows that push to `sim-history` use the built-in token and keep working.
- **What it cannot undo.** Copies already cloned or indexed by anyone before the flip.

#### Option A readiness, checked 2026-11-26 evening (the owner chose A; nothing has been changed on GitHub)

**What was verified from here:**

| item | finding | what to do |
|---|---|---|
| GitHub Actions minutes | 147 workflow runs in the last 30 days, about **240 billed minutes** (guards ~91, error-log ~88, nightly ~56, weekly ~4; each run rounded up to the minute). The Free plan includes 2,000 a month for private repositories. | Nothing. There is room for about 8x today's usage. Check Settings > Billing after the first month. |
| Workflows that write to `sim-history` | They use the built-in `GITHUB_TOKEN`, which works the same in a private repository. | Nothing. |
| The app reading its own repository | **One dependency:** on every restart, `deep_history.restore_from_github` pulls the MLB deep-sim history back from `sim-history` over anonymous raw.githubusercontent URLs. In a private repository that request is refused. The restore is best-effort, so the app starts normally and the failure is ledgered (`APP-restore_history`). MLB simulation is paused for the off-season (`MLB_SIMS_PAUSED`), so nothing is lost now. | Before MLB resumes, either give the app a read-only, single-repository token (a small code change: the app holds no GitHub credentials today, so that is your call), or accept that the MLB history calendar starts empty after a restart. |
| The PC worker | pc_loop fetches anonymously over HTTPS (`git fetch`, then `git pull --ff-only`). Hardened in this commit: its git now runs with prompts off and a 300-second timeout. A missing credential therefore shows as a pull error on the light while the PC keeps building on its current code, instead of hanging the loop on a sign-in window. | The sign-in steps below, BEFORE flipping. |
| Third-party raw.githubusercontent reads | dynastyprocess, Sackmann and TML are other people's public repositories, unaffected. | Nothing. |

**What only the owner can check: Render.**

- Render Dashboard > the Vigil service > Settings > Build & Deploy: the repository should show as connected
  through GitHub.
- On GitHub: Settings > Applications > Installed GitHub Apps > Render > Configure > Repository access should
  read "All repositories" or list `kalshi-betting-helper`. If it does, a private repository deploys as before.
- If the service was created from a public URL rather than the app, reconnect it through the app first.
  Otherwise the next deploy after the flip fails. The running service keeps running either way.

**The PC worker's sign-in, step by step** (before flipping, in a second Command Prompt; the worker can keep
running):

1. `cd /d "D:\Games\Kalshi Betting\kalshi-betting-helper"`, then `git log -1 --oneline`. It must show the
   commit that hardened pc_loop's git, or a later one. The PC pulls on its own between builds. Do not flip
   before this, or an unattended fetch could still wait on a prompt.
2. `git config --global credential.helper` should print `manager`. Git for Windows ships Git Credential
   Manager. If it prints nothing: `git config --global credential.helper manager`.
3. `git credential-manager github login`. A browser opens: sign in as the repository's owner and approve.
4. `git credential-manager github list` should show that account.
5. Flip the repository to private: GitHub > the repository > Settings > General > Danger Zone > Change
   visibility > Private.
6. In the same Command Prompt: `git fetch`. It must finish with no prompt and no error. That confirms the
   stored credential works.
7. Within a minute or two, check the PC light shows no pull error. pc_loop fetches every 60 seconds.

Fallback if step 3 is unavailable (an older Git Credential Manager): create a fine-grained token (GitHub >
Settings > Developer settings > Fine-grained tokens) with repository access "Only select repositories ->
kalshi-betting-helper", permission "Contents: Read-only", and an expiry of your choosing. After the flip, run
`git fetch` once in the Command Prompt; when asked, enter your GitHub username, then paste the token at the
second prompt; Git Credential Manager stores it.

**After the flip.**

- Links to files in the repository need a signed-in browser, including any shared with ChatGPT.
- If Vigil should ever be public again, publish a fresh, sanitized repository rather than this history.
- **What A cannot do:** anything already cloned or cached while the repository was public stays out there.

### Option B: rewrite history to drop the three files

- **Every commit hash from `479caa2` on changes:** 121 commits, almost two months of history.
  - **63 committed research artifacts record commit hashes as provenance.** `research/prov_verify.py` walks
    history to verify them, and reports cite runs like "clean run at ac09ae4".
  - After a rewrite those hashes point at commits that no longer exist in the repository. The mitigation is to
    commit the rewrite's old-to-new commit map and teach `prov_verify` to read it. It is still a permanent seam
    in the record.
- **The PC worker.** `git pull --ff-only` cannot follow a rewritten branch, so the PC stays on the old code and
  the light shows the pull error until someone resets it by hand. Untracked and ignored files (`vigil-pc.cfg`,
  `pc-simcache/`, `pc-deepcache/`, `pc-standings/`) are not touched by a reset.
- **Render.** The force push deploys the rewritten head, which has the same code, so it is one restart.
  Render's dashboard rollback to a pre-rewrite deploy may need the old commit. Check that rollback still works
  before relying on it.
- **GitHub.** The old commits stay reachable by hash until GitHub garbage-collects them. Cached views can only
  be purged by GitHub Support.
- **The session working branch** (`claude/kalshi-crypto-predictor-explore-5x0lvp`) predates the files and is
  unaffected.

**Procedure, if you approve B.** Each step waits for the one before.

1. **Pick a window** with no build the PC owes (not Saturday 6 pm to midnight MT, and not within 2 hours of a
   lock). Stop `vigil-pc.bat` on the PC.
2. **Back up.** On the PC, run the `git restore` line above so the private copies exist. In a fresh clone,
   `git bundle create vigil-pre-rewrite.bundle --all`: the complete pre-rewrite history, kept privately offline
   as the rollback point.
3. **Rewrite.** In the fresh clone:

       git filter-repo --invert-paths --path research/data/dk_standings/contest-standings-193391013.csv.gz --path research/data/dk_standings/contest-standings-195526287.csv.gz --path research/data/dk_standings/contest-standings-195677825.csv.gz

   Then check that the guard suite passes on the result. Commit filter-repo's `commit-map` (old to new hashes,
   no entrant data) as `research/data/history_rewrite_commit_map.txt`, and update `prov_verify` to read it.
4. **Force-push the one affected branch:**

       git push --force-with-lease origin claude/kalshi-crypto-predictor-ckutwm

   Render deploys it, which is one restart.
5. **Reset the PC:**

       cd /d "D:\Games\Kalshi Betting\kalshi-betting-helper"
       git fetch origin
       git reset --hard origin/claude/kalshi-crypto-predictor-ckutwm

   Then start `vigil-pc.bat` and check that the light shows no pull error.
6. **Ask GitHub Support** to remove cached views of `479caa2` and `672b83b` (and their descendants' old hashes).
7. **Roll back if needed.** The bundle from step 2 restores the old history exactly: clone from the bundle and
   force-push.

**Recommendation (the owner has since chosen A).** Take A if the Actions minutes fit, because it fixes the exposure without breaking two months
of provenance. If the repository must stay public, take B, in a quiet window, with the bundle kept offline. This
session does neither without your go-ahead.
