"use client";

export default function DashboardError({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="loading-shell error-shell">
      <div className="loading-mark">!</div>
      <p>Dashboard gặp lỗi không mong muốn khi hiển thị dữ liệu.</p>
      <button type="button" onClick={reset}>Thử lại</button>
    </main>
  );
}
