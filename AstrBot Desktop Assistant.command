#!/bin/bash
set -e

PROJECT_DIR="/Users/hacchiroku/bot-stack/Astrbot-desktop-assistant"
APP_BUNDLE="$PROJECT_DIR/dist/AstrBot Desktop Assistant.app"

cd "$PROJECT_DIR"
open -n "$APP_BUNDLE"
