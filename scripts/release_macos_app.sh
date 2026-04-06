#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_BUNDLE="$PROJECT_DIR/dist/AstrBot Desktop Assistant.app"
DIST_DIR="$PROJECT_DIR/dist"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/release_macos_app.sh <version> [release_title]

Example:
  ./scripts/release_macos_app.sh v1.2.3
  ./scripts/release_macos_app.sh v1.2.3 "AstrBot Desktop Assistant v1.2.3"

What it does:
  1. Build the macOS app bundle
  2. Create a zip artifact in dist/
  3. Create and push a git tag
  4. Create a GitHub release with gh (if installed and authenticated)
EOF
}

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

VERSION="$1"
RELEASE_TITLE="${2:-AstrBot Desktop Assistant $VERSION}"
ZIP_NAME="AstrBot-Desktop-Assistant-${VERSION}-macos.zip"
ZIP_PATH="$DIST_DIR/$ZIP_NAME"

if [[ ! "$VERSION" =~ ^v[0-9]+(\.[0-9]+)*([.-][A-Za-z0-9]+)?$ ]]; then
    echo "[release] version must look like v1.2.3" >&2
    exit 1
fi

cd "$PROJECT_DIR"

./scripts/build_macos_app.sh

rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_BUNDLE" "$ZIP_PATH"

if git rev-parse "$VERSION" >/dev/null 2>&1; then
    echo "[release] git tag already exists: $VERSION" >&2
    exit 1
fi

git tag -a "$VERSION" -m "$RELEASE_TITLE"
git push origin "$VERSION"

if command -v gh >/dev/null 2>&1; then
    gh release create "$VERSION" "$ZIP_PATH" --title "$RELEASE_TITLE" --generate-notes
    echo "[release] github release created: $VERSION"
else
    echo "[release] gh is not installed; artifact ready at $ZIP_PATH"
    echo "[release] tag has been pushed. Create the release manually on GitHub."
fi
