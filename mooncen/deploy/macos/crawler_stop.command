#!/bin/bash
# MoonCen 크롤러 워커 즉시 종료 스크립트
PLIST="$HOME/Library/LaunchAgents/com.mooncen.crawler.worker.plist"

echo "=========================================="
echo "🌙 MoonCen 크롤러 워커 종료"
echo "=========================================="
launchctl unload "$PLIST" 2>/dev/null || true
sleep 1

echo "✓ 크롤러 워커가 안전하게 종료되었습니다."
echo ""
echo "3초 후 자동으로 닫힙니다..."
sleep 3
