import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";

import type { BranchDto } from "../api/mooncenApi";
import { theme } from "../constants/theme";
import type { KakaoMapViewProps } from "./KakaoMapView.types";

type KakaoMarker = {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  count: number;
};

function markerPayload(branches: BranchDto[]): KakaoMarker[] {
  return branches.flatMap((branch) => {
    if (branch.lat === null || branch.lon === null) return [];
    return [{
      id: branch.id,
      name: branch.name,
      latitude: branch.lat,
      longitude: branch.lon,
      count: branch.open_course_count,
    }];
  }).slice(0, 250);
}

function escapeInlineJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

function buildMapHtml(
  apiKey: string,
  markers: KakaoMarker[],
  center: KakaoMapViewProps["center"],
  selectedBranchId: string | null | undefined,
): string {
  const safeKey = encodeURIComponent(apiKey);
  const markerJson = escapeInlineJson(markers);
  const selectedJson = escapeInlineJson(selectedBranchId ?? "");
  return `<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no" />
<style>
html,body,#map{width:100%;height:100%;margin:0;padding:0;background:#edf5f2;overflow:hidden}
.pin{min-width:34px;height:34px;padding:0 8px;border:2px solid #fff;border-radius:18px;background:#14b8a6;color:#fff;box-shadow:0 3px 10px rgba(15,23,42,.22);font:800 12px/30px -apple-system,BlinkMacSystemFont,'Noto Sans KR',sans-serif;text-align:center;box-sizing:border-box}
.pin.selected{background:#ff6b6b;transform:scale(1.12)}
</style>
<script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey=${safeKey}&autoload=false"></script>
</head><body><div id="map" aria-label="카카오 지도"></div><script>
(function(){
  var markers=${markerJson};
  var selected=${selectedJson};
  function send(type,payload){window.ReactNativeWebView&&window.ReactNativeWebView.postMessage(JSON.stringify({type:type,payload:payload}));}
  if(!window.kakao||!window.kakao.maps){send('error','카카오 지도 SDK를 불러오지 못했습니다.');return;}
  kakao.maps.load(function(){
    try {
      var center=new kakao.maps.LatLng(${center.latitude},${center.longitude});
      var map=new kakao.maps.Map(document.getElementById('map'),{center:center,level:7});
      var bounds=new kakao.maps.LatLngBounds();
      markers.forEach(function(item){
        var position=new kakao.maps.LatLng(item.latitude,item.longitude);
        var node=document.createElement('button');
        node.type='button';
        node.className='pin'+(item.id===selected?' selected':'');
        node.textContent=item.count>99?'99+':String(Math.max(0,item.count));
        node.setAttribute('aria-label',item.name+' 접수중 '+item.count+'개');
        node.onclick=function(){send('select',item.id)};
        new kakao.maps.CustomOverlay({map:map,position:position,content:node,yAnchor:1.05});
        bounds.extend(position);
      });
      if(markers.length>1){map.setBounds(bounds,38,38,38,38)}
      send('ready',markers.length);
    } catch(error){send('error',error&&error.message?error.message:'지도를 표시하지 못했습니다.');}
  });
})();
</script></body></html>`;
}

export function KakaoMapView({
  branches,
  center,
  height = 300,
  selectedBranchId,
  onSelectBranch,
  onOpenExternal,
}: KakaoMapViewProps) {
  const apiKey = process.env.EXPO_PUBLIC_KAKAO_MAPS_JAVASCRIPT_KEY?.trim();
  const markers = markerPayload(branches);
  const [mapError, setMapError] = useState<string | null>(null);

  if (!apiKey || mapError) {
    return (
      <View style={[styles.fallback, { height }]}> 
        <Text accessibilityRole="alert" style={styles.fallbackTitle}>
          {mapError || "카카오 지도 설정이 필요해요."}
        </Text>
        <Text style={styles.fallbackDescription}>
          기관 목록은 계속 이용할 수 있고, 외부 카카오맵에서도 위치를 확인할 수 있어요.
        </Text>
        <Pressable
          accessibilityLabel="카카오맵에서 현재 위치 열기"
          accessibilityRole="link"
          onPress={onOpenExternal}
          style={({ pressed }) => [styles.fallbackButton, pressed && styles.pressed]}
        >
          <Text style={styles.fallbackText}>카카오맵에서 열기</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={[styles.container, { height }]}>
      <WebView
        accessibilityLabel={`카카오 지도, 주변 기관 ${markers.length}곳`}
        allowsLinkPreview={false}
        domStorageEnabled
        javaScriptEnabled
        mixedContentMode="never"
        onError={() => setMapError("카카오 지도를 불러오지 못했어요.")}
        onMessage={(event) => {
          try {
            const message = JSON.parse(event.nativeEvent.data) as { type?: string; payload?: unknown };
            if (message.type === "select" && typeof message.payload === "string") {
              onSelectBranch(message.payload);
            } else if (message.type === "error") {
              setMapError(
                typeof message.payload === "string" && message.payload.trim()
                  ? message.payload.trim().slice(0, 120)
                  : "카카오 지도를 불러오지 못했어요.",
              );
            }
          } catch {
            // Ignore malformed messages from the embedded document.
          }
        }}
        originWhitelist={["https://*", "about:blank"]}
        setSupportMultipleWindows={false}
        source={{
          html: buildMapHtml(apiKey, markers, center, selectedBranchId),
          baseUrl: "https://mooncen.kr",
        }}
        style={styles.webView}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    overflow: "hidden",
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.map,
  },
  webView: { flex: 1, backgroundColor: theme.colors.map },
  fallback: {
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.map,
    padding: theme.spacing.xl,
  },
  fallbackTitle: { color: theme.colors.text, fontSize: 14, fontWeight: "900" },
  fallbackDescription: {
    maxWidth: 280,
    color: theme.colors.textMuted,
    fontSize: 11,
    lineHeight: 17,
    textAlign: "center",
  },
  fallbackButton: {
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.lg,
  },
  fallbackText: { color: theme.colors.primaryStrong, fontSize: 12, fontWeight: "800" },
  pressed: { opacity: 0.65 },
});
