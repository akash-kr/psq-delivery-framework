#!/usr/bin/env bash
# annotation-watcher.sh — watches reviews/inbox for submitted HTML-doc
# annotations and launches a headless agent run per submission.
# (Not the escalation watcher — that is tools/escalation-watcher.py.)
#
# Usage:
#   ./annotation-watcher.sh [ROOT]       # ROOT defaults to current dir
#
# Env:
#   AGENT_CMD   command that accepts a prompt as its last arg.
#               Default: claude -p
#               Examples: AGENT_CMD="claude -p --permission-mode acceptEdits"
#
# Lifecycle per submission:
#   reviews/inbox/X.{json,md} → reviews/processing/ → agent run →
#   success: reviews/processed/   failure: stays in processing/ for inspection
set -u
ROOT="${1:-.}"
INBOX="$ROOT/reviews/inbox"
RUN="$ROOT/reviews/processing"
DONE="$ROOT/reviews/processed"
AGENT_CMD="${AGENT_CMD:-claude -p}"

mkdir -p "$INBOX" "$RUN" "$DONE"
echo "[watcher] watching $INBOX (agent: $AGENT_CMD)"

while true; do
  for f in "$INBOX"/*.json; do
    [ -e "$f" ] || continue
    base=$(basename "$f" .json)
    mv "$f" "$RUN/" 2>/dev/null || continue   # another watcher may have claimed it
    [ -e "$INBOX/$base.md" ] && mv "$INBOX/$base.md" "$RUN/"

    src=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('file',''))" "$RUN/$base.json" 2>/dev/null)
    echo "[watcher] $(date +%H:%M:%S) processing $base (source: ${src:-unknown})"

    if $AGENT_CMD "You are applying human review feedback to a document in this repo.

Review notes: reviews/processing/$base.md
Source document: ${src:-see the notes file header}

For each note: locate the quoted span in the document's source and apply the
requested change to that exact section. Apply the 'Overall page note' as a
document-wide revision. If the document is annotated HTML generated from a
source file, update the source and regenerate the HTML so highlights re-anchor.
Do not change anything the notes do not ask for. When finished, summarize what
changed per note."; then
      mv "$RUN/$base".* "$DONE/"
      echo "[watcher] $base done → processed/"
    else
      echo "[watcher] agent run FAILED for $base — left in processing/ for inspection"
    fi
  done
  sleep 5
done
