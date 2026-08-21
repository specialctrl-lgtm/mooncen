package com.mooncen.monitor;

import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;

import javax.net.ssl.SSLException;

final class ApiErrorMessage {
    private ApiErrorMessage() {
    }

    static String from(Exception exception) {
        if (exception instanceof ApiClient.ApiException) {
            return fromHttpStatus(((ApiClient.ApiException) exception).getStatusCode());
        }
        if (exception instanceof SSLException) {
            return "보안 연결을 확인할 수 없습니다. 서버 인증서와 기기 날짜·시간을 확인하세요.";
        }
        if (exception instanceof UnknownHostException) {
            return "서버 주소를 찾을 수 없습니다. 인터넷 연결과 API 주소를 확인하세요.";
        }
        if (exception instanceof SocketTimeoutException) {
            return "서버 응답 시간이 초과되었습니다. 잠시 후 다시 시도하세요.";
        }
        if (exception instanceof ConnectException || exception instanceof NoRouteToHostException) {
            return "서버에 연결할 수 없습니다. 인터넷 연결 또는 서버 상태를 확인하세요.";
        }
        String message = exception.getMessage();
        return message == null || message.trim().isEmpty()
                ? "알 수 없는 연결 오류가 발생했습니다."
                : message.trim();
    }

    private static String fromHttpStatus(int statusCode) {
        if (statusCode == 401) {
            return "인증에 실패했습니다. 설정에서 API 토큰을 확인하세요.";
        }
        if (statusCode == 403) {
            return "서버가 접근을 거부했습니다. 공개 상태 API 권한을 확인하세요.";
        }
        if (statusCode == 404) {
            return "상태 API 경로를 찾을 수 없습니다. API 주소를 확인하세요.";
        }
        if (statusCode >= 300 && statusCode < 400) {
            return "리디렉션 주소는 허용하지 않습니다. 최종 HTTPS API 주소를 입력하세요.";
        }
        if (statusCode >= 500) {
            return "모니터링 서버에 오류가 발생했습니다. 잠시 후 다시 시도하세요. (HTTP "
                    + statusCode + ")";
        }
        return "API 요청이 실패했습니다. (HTTP " + statusCode + ")";
    }
}
