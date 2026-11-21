#!/usr/bin/env bash
# Moves docs/ and specs/ into .claude/ and rewrites path references. Run once from the repo root.
set -euo pipefail
mkdir -p .claude
mv docs .claude/docs
mv specs .claude/specs
grep -rlE '(^|[^.a-z/])(docs|specs)/' --include='*.md' --include='*.sh' . | grep -v '^./.git/' | while read -r f; do
  perl -pi -e 's#(?<![\w./])docs/#.claude/docs/#g; s#(?<![\w./])specs/#.claude/specs/#g' "$f"
done
echo "Moved to .claude/ and updated references."
