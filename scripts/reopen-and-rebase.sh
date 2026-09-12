#!/usr/bin/env bash
# reopen-and-rebase.sh — bring a held PR branch up to date with current main
# and push, so a stale Sourcery review (e.g. "helper doesn't exist on main")
# becomes non-stale and the bot re-reviews the real code.
#
# Usage: sh scripts/reopen-and-rebase.sh <branch>
#   sh scripts/reopen-and-rebase.sh docs/linear-graphql-skill
set -eu

branch="${1:-}"
if [ -z "$branch" ]; then echo "usage: $0 <branch>"; exit 1; fi
need() { command -v "$1" >/dev/null 2>&1 || { echo "$1 is required"; exit 1; }; }

need git
need gh

branch_remote=$(git remote show origin 2>/dev/null | sed -n "/$branch\$/p" | awk '{print $2}')
if [ -z "$branch_remote" ]; then
  echo "branch '$branch' has no tracked remote — pushing explicit tracking below"
  branch_remote="origin/$branch"
fi

current=$(git branch --show-current)
if [ "$current" != "$branch" ]; then
  echo "checking out $branch"
  git checkout "$branch"
fi

echo "fetching origin/main"
git fetch origin main

echo "rebasing $branch onto origin/main"
git rebase origin/main

echo "pushing $branch"
git push --force-with-lease origin "$branch"

head=$(gh pr view "$branch" --json headRefName --jq .headRefName 2>/dev/null || echo "$branch")
# try to find an open PR for this head
pr=$(gh pr list --head "$branch" --json number --jq '.[0].number' 2>/dev/null || echo "")
if [ -z "$pr" ]; then
  # fallback: search by base
  pr=$(gh pr list --base main --head "$branch" --json number --jq '.[0].number' 2>/dev/null || echo "")
fi

if [ -n "$pr" ]; then
  echo "PR #$pr updated — force-push re-wrote the branch tip"
else
  echo "no open PR found for $branch (head=$head)"
fi
