#!/bin/bash
# Mac 바탕화면에 크롤러 관리용 실행 아이콘 생성 스크립트
DESKTOP_DIR="$HOME/Desktop"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$DESKTOP_DIR"

# 원본 실행 권한 부여
chmod +x "$SRC_DIR"/*.command 2>/dev/null || true

# 바탕화면에 한국어 이름으로 복사
cp "$SRC_DIR/crawler_manager.command" "$DESKTOP_DIR/크롤러_통합관리.command"
cp "$SRC_DIR/crawler_start.command" "$DESKTOP_DIR/크롤러_시작.command"
cp "$SRC_DIR/crawler_stop.command" "$DESKTOP_DIR/크롤러_종료.command"
cp "$SRC_DIR/crawler_logs.command" "$DESKTOP_DIR/크롤러_실시간로그.command"

# 바탕화면 파일 실행 권한 부여
chmod +x "$DESKTOP_DIR"/크롤러_*.command

echo "============================================================"
echo "✓ Mac 바탕화면(Desktop)에 크롤러 실행파일 생성이 완료되었습니다!"
echo "============================================================"
echo "  1) [크롤러_통합관리.command] : 메뉴에서 시작/종료/재시작/현황 선택"
echo "  2) [크롤러_시작.command]     : 더블클릭 시 즉시 백그라운드 실행"
echo "  3) [크롤러_종료.command]     : 더블클릭 시 즉시 안전 종료"
echo "  4) [크롤러_실시간로그.command] : 더블클릭 시 크롤링 로그 실시간 모니터링"
echo "============================================================"
