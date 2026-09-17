#!/bin/bash
# MoonCen 크롤러 실시간 로그 보기 스크립트
LOG_FILE="/tmp/mooncen_crawler_worker.stdout.log"

echo "=================================================="
echo "🌙 MoonCen 크롤러 실시간 로그"
echo "(이 창을 닫거나 Ctrl + C 를 누르면 종료됩니다)"
echo "=================================================="
if [ ! -f "$LOG_FILE" ]; then
    touch "$LOG_FILE"
fi

tail -f "$LOG_FILE"
