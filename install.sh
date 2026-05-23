#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"

mkdir -p "$TARGET/books/data"
mkdir -p "$TARGET/.agents/skills/kindle"
mkdir -p "$TARGET/skills/kindle"

cp "$SCRIPT_DIR/books/kindle_capture.py" "$TARGET/books/kindle_capture.py"
cp "$SCRIPT_DIR/books/kindle_login.py" "$TARGET/books/kindle_login.py"
cp "$SCRIPT_DIR/books/reference.md" "$TARGET/books/reference.md"
cp "$SCRIPT_DIR/books/.gitignore" "$TARGET/books/.gitignore"
touch "$TARGET/books/data/.gitkeep"

cp "$SCRIPT_DIR/.agents/skills/kindle/SKILL.md" "$TARGET/.agents/skills/kindle/SKILL.md"
cp "$SCRIPT_DIR/skills/kindle/SKILL.md" "$TARGET/skills/kindle/SKILL.md"
cp "$SCRIPT_DIR/requirements.txt" "$TARGET/requirements-kindle.txt"

if [ ! -e "$TARGET/.claude/skills" ]; then
  mkdir -p "$TARGET/.claude"
  ln -s ../.agents/skills "$TARGET/.claude/skills"
fi

echo "Installed Kindle autosave files into: $TARGET"
echo "Next:"
echo "  python3 -m pip install --user -r \"$TARGET/requirements-kindle.txt\""
echo "  python3 -m playwright install chromium"
echo "  gcloud auth application-default login"
echo "  gcloud config set project <your-gcp-project-id>"
echo "  export KINDLE_CAPTURE_GCP_PROJECT=\"<your-gcp-project-id>\""
