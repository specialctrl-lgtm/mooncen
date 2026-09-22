#!/bin/bash
# MoonCen Mac 크롤러 관리도구 (통합 컨트롤러)
PLIST="$HOME/Library/LaunchAgents/com.mooncen.crawler.worker.plist"
LOG_FILE="/tmp/mooncen_crawler_worker.stdout.log"
WORK_DIR="$HOME/mooncen/mooncen"

cd "$WORK_DIR" 2>/dev/null || true

clear
while true; do
    echo "=================================================="
    echo "       🌙 MoonCen Mac 크롤러 워커 관리도구        "
    echo "=================================================="
    
    PID=$(launchctl list 2>/dev/null | grep com.mooncen.crawler.worker | awk '{print $1}')
    if [ -n "$PID" ] && [ "$PID" != "-" ]; then
        echo "  🟢 현재 상태: [실행 중] (PID: $PID)"
    else
        echo "  🔴 현재 상태: [중지됨]"
    fi
    echo "=================================================="
    echo "  [1] 크롤러 시작 (Start, 자동 업데이트 포함)"
    echo "  [2] 크롤러 종료 (Stop)"
    echo "  [3] 크롤러 재시작 (Restart, 최신 버전 재적용)"
    echo "  [4] 실시간 로그 보기 (Logs)"
    echo "  [5] 큐 수집 현황 확인 (Queue Status)"
    echo "  [6] 최신 코드 수동 업데이트 (Git Pull)"
    echo "  [0] 나가기 (Exit)"
    echo "=================================================="
    read -rp "원하는 작업 번호를 입력하세요 (0-6): " choice

    case "$choice" in
        1)
            echo ""
            echo "▶ 크롤러 워커를 시작합니다..."
            launchctl load -w "$PLIST" 2>/dev/null || true
            sleep 1
            launchctl list | grep mooncen
            echo "✓ 완료되었습니다."
            echo ""
            read -rp "계속하려면 Enter를 누르세요..."
            ;;
        2)
            echo ""
            echo "▶ 크롤러 워커를 중지합니다..."
            launchctl unload "$PLIST" 2>/dev/null || true
            sleep 1
            echo "✓ 안전하게 중지되었습니다."
            echo ""
            read -rp "계속하려면 Enter를 누르세요..."
            ;;
        3)
            echo ""
            echo "▶ 크롤러 워커를 재시작합니다..."
            launchctl unload "$PLIST" 2>/dev/null || true
            sleep 1
            launchctl load -w "$PLIST" 2>/dev/null || true
            sleep 1
            launchctl list | grep mooncen
            echo "✓ 재시작되었습니다."
            echo ""
            read -rp "계속하려면 Enter를 누르세요..."
            ;;
        4)
            echo ""
            echo "▶ 실시간 로그를 표시합니다. (종료하려면 Ctrl + C)"
            echo "--------------------------------------------------"
            tail -f "$LOG_FILE"
            echo ""
            ;;
        5)
            echo ""
            echo "▶ DB 큐 수집 현황을 조회합니다..."
            echo "--------------------------------------------------"
            if [ -f "$HOME/mooncen/.venv/bin/python" ]; then
                "$HOME/mooncen/.venv/bin/python" tools/crawler_queue_manager.py status
            elif [ -f ".venv/bin/python" ]; then
                .venv/bin/python tools/crawler_queue_manager.py status
            else
                python3 tools/crawler_queue_manager.py status
            fi
            echo "--------------------------------------------------"
            echo ""
            read -rp "계속하려면 Enter를 누르세요..."
            ;;
        6)
            echo ""
            echo "▶ 최신 소스코드를 원격 저장소에서 업데이트합니다..."
            echo "--------------------------------------------------"
            if [ -d ".git" ]; then
                git fetch origin main --prune
                git pull --ff-only origin main || git pull origin main
                echo "✓ 소스코드 업데이트가 완료되었습니다."
            else
                echo "⚠ .git 디렉토리를 찾을 수 없습니다."
            fi
            echo "--------------------------------------------------"
            echo ""
            read -rp "계속하려면 Enter를 누르세요..."
            ;;
        0)
            echo "프로그램을 종료합니다."
            exit 0
            ;;
        *)
            echo "잘못된 입력입니다."
            sleep 1
            ;;
    esac
    clear
done
