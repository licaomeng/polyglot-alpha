#!/usr/bin/env bash
# =============================================================================
# scripts/deploy_to_hf.sh — one-command deploy to Hugging Face Spaces.
# =============================================================================
# Strategy: snapshot the current main commit into a fresh orphan branch, swap
# README.md for the HF-flavored version (with the YAML frontmatter Spaces
# needs), force-push that single-commit branch as HF's `main`, then clean up.
#
# Why an orphan branch (not a regular merge / push of main)?
#   1. HF Spaces requires YAML frontmatter at the top of README.md so it
#      knows to launch a Docker container. GitHub's full README has none —
#      we only want the swap to live on the deploy branch, not on main.
#   2. main's git history is ~1000+ commits. HF pushes carry the full
#      reachable history, and any historical commit that ever held a
#      binary >10 MB would fail HF's pre-receive hook. Orphan = 1 commit,
#      no history to litigate.
#
# Prerequisites:
#   * `hf` remote already configured (run once:
#       git remote add hf https://huggingface.co/spaces/messili/polyglot-alpha)
#   * `hf auth login` already done (token cached by huggingface_hub CLI).
#   * Working tree is clean on main, or untracked-only.
# -----------------------------------------------------------------------------
set -euo pipefail

# ---- color/log helpers ------------------------------------------------------
if [[ -t 1 ]]; then
    BLUE=$'\033[34m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi
log()  { printf '%s[deploy]%s %s\n' "$BLUE" "$RESET" "$*"; }
ok()   { printf '%s[deploy]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[deploy]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%s[deploy]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ---- preflight checks -------------------------------------------------------
log "preflight checks"
git remote get-url hf > /dev/null 2>&1 || die "hf remote not configured. Run: git remote add hf https://huggingface.co/spaces/messili/polyglot-alpha"
[[ -f deploy/hf-readme.md ]] || die "deploy/hf-readme.md missing — HF needs the YAML frontmatter version"
[[ -f Dockerfile ]]          || die "Dockerfile missing — HF Spaces is a Docker SDK Space"
[[ -f deploy/nginx.conf ]]   || die "deploy/nginx.conf missing — required by Dockerfile"
[[ -f deploy/entrypoint.sh ]] || die "deploy/entrypoint.sh missing — required by Dockerfile"

# Working tree state: tracked changes block deploy, untracked are fine.
if ! git diff-index --quiet HEAD --; then
    die "working tree has uncommitted tracked changes; commit or stash first"
fi

ORIGINAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$ORIGINAL_BRANCH" != "main" ]]; then
    warn "currently on '$ORIGINAL_BRANCH' (not main) — will checkout main"
    git checkout main
fi

MAIN_SHA_SHORT="$(git rev-parse --short HEAD)"
TMP_BRANCH="hf-deploy-$(date +%Y%m%d-%H%M%S)"
log "deploying main @ $MAIN_SHA_SHORT  →  hf:main  (via temp orphan '$TMP_BRANCH')"

# ---- cleanup trap -----------------------------------------------------------
# If anything below fails, restore the user to their starting branch and
# delete the temp branch so they don't end up stuck on an orphan.
cleanup() {
    local exit_code=$?
    if [[ -n "${TMP_BRANCH:-}" ]] && git show-ref --verify --quiet "refs/heads/$TMP_BRANCH"; then
        local current
        current="$(git rev-parse --abbrev-ref HEAD)"
        if [[ "$current" == "$TMP_BRANCH" ]]; then
            git checkout "$ORIGINAL_BRANCH" 2>/dev/null || git checkout main
        fi
        git branch -D "$TMP_BRANCH" > /dev/null 2>&1 || true
    fi
    if [[ $exit_code -ne 0 ]]; then
        warn "deploy aborted (exit $exit_code)"
    fi
    exit $exit_code
}
trap cleanup EXIT

# ---- build the orphan snapshot ---------------------------------------------
log "creating orphan branch '$TMP_BRANCH'"
git checkout --orphan "$TMP_BRANCH"
# `git checkout --orphan` keeps the working tree but stages everything. We
# want to start from a clean stage, then re-add everything filtered by
# .gitignore — so a stray binary in working tree doesn't sneak through.
git rm -rf --cached . > /dev/null 2>&1 || true
git add -A

log "swapping README for HF-flavored version (deploy/hf-readme.md → README.md)"
cp deploy/hf-readme.md README.md
git add README.md

# Single commit — HF only ever sees this one.
COMMIT_MSG="deploy: main@${MAIN_SHA_SHORT} → HF Spaces ($(date -u +%Y-%m-%dT%H:%MZ))"
git commit --quiet -m "$COMMIT_MSG"
ok "snapshot committed: $(git rev-parse --short HEAD)"

# ---- push to HF -------------------------------------------------------------
log "force-pushing to hf:main (HF Space will auto-rebuild on receipt)"
if ! git push hf "$TMP_BRANCH:main" --force 2>&1; then
    die "push to HF failed — see error above. Common causes: stale auth ('hf auth login'), Space settings (sdk: docker required), or large files."
fi
ok "pushed → https://huggingface.co/spaces/messili/polyglot-alpha"

# ---- restore main, swap README back -----------------------------------------
log "restoring '$ORIGINAL_BRANCH' (your README.md never moved on main)"
git checkout --force "$ORIGINAL_BRANCH"

# Cleanup trap deletes the temp branch on exit.
ok "deploy complete"
echo
echo "  HF Space: https://messili-polyglot-alpha.hf.space/"
echo "  Build progress: https://huggingface.co/spaces/messili/polyglot-alpha"
echo "  Cold rebuild ~3-5 min; warm rebuild ~30-60 s."
