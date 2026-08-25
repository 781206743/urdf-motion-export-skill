#!/bin/sh
# Cross-platform installer for urdf-motion-export-skill.
set -eu

SKILL_NAME="urdf-motion-export-skill"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="universal"
DESTINATION=""

usage() {
  echo "Usage: ./install.sh [--platform universal|codex|claude-code|cursor|copilot] [--path DIRECTORY]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --platform) PLATFORM="$2"; shift 2 ;;
    --path) DESTINATION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [ ! -f "$SCRIPT_DIR/SKILL.md" ]; then
  echo "SKILL.md is missing." >&2
  exit 1
fi

if [ -z "$DESTINATION" ]; then
  case "$PLATFORM" in
    codex) DESTINATION="$HOME/.codex/skills" ;;
    universal) DESTINATION="$HOME/.agents/skills" ;;
    claude-code) DESTINATION="$HOME/.claude/skills" ;;
    cursor) DESTINATION=".cursor/rules" ;;
    copilot) DESTINATION=".github/skills" ;;
    *) echo "Unsupported platform: $PLATFORM" >&2; exit 1 ;;
  esac
fi

mkdir -p "$DESTINATION"
TARGET="$DESTINATION/$SKILL_NAME"
if [ -e "$TARGET" ]; then
  echo "Refusing to overwrite existing skill: $TARGET" >&2
  echo "Remove or rename it first, then run the installer again." >&2
  exit 1
fi
cp -R "$SCRIPT_DIR" "$TARGET"
echo "Installed: $TARGET"
echo "Open a new agent session and invoke /$SKILL_NAME"
