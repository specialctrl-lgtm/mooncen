#!/bin/bash
# MoonCen 크롤러 워커 즉시 시작 스크립트
PLIST="$HOME/Library/LaunchAgents/com.mooncen.crawler.worker.plist"

echo "=========================================="
echo "🌙 MoonCen 크롤러 워커 시작"
echo "=========================================="
launchctl load -w "$PLIST" 2>/dev/null || true
sleep 1

PID=$(launchctl list 2>/dev/null | grep com.mooncen.crawler.worker | awk '{print $1}')
if [ -n "$PID" ] && [ "$PID" != "-" ]; then
    echo "✓ 실행 완료 (PID: $PID)"
    echo "✓ 크롤러가 백그라운드에서 동작 중입니다."
else
    echo "⚠️ 상태 확인:"
    launchctl list 2>/dev/null | grep mooncen || echo "실행 등록 완료"
fi

echo ""
echo "3초 후 자동으로 닫힙니다 (창을 바로 닫으셔도 계속 실행됩니다)..."
sleep 3
