import { Component, type ErrorInfo, type ReactNode } from 'react';

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  failed: boolean;
};

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Ops Console render failure', error, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="startup access-denied" role="alert">
          <span className="eyebrow">OPS ADMIN</span>
          <h1>Ops Console 화면 오류</h1>
          <p>화면을 표시하는 중 오류가 발생했습니다. 새로고침 후에도 반복되면 API 로그를 확인하세요.</p>
          <button className="button primary" type="button" onClick={() => window.location.reload()}>
            새로고침
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
