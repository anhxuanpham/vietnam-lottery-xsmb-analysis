import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const TITLE = "Lôtô Lab — Vietnam Lottery Analytics";
const DESCRIPTION = "Dashboard mô tả dữ liệu XSMB/XSMN/XSMT với heatmap, model lab và backtest minh bạch.";
const FALLBACK_ORIGIN = "https://loto-lab-vietnam.nmt17092006.chatgpt.site";

function requestOrigin(requestHeaders: Headers): URL {
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",", 1)[0].trim();
  const host = forwardedHost || requestHeaders.get("host")?.trim();
  if (!host || !/^[A-Za-z0-9.-]+(?::\d{1,5})?$/.test(host)) return new URL(FALLBACK_ORIGIN);
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",", 1)[0].trim().toLowerCase();
  const protocol = forwardedProtocol === "http" || forwardedProtocol === "https"
    ? forwardedProtocol
    : /^(localhost|127\.0\.0\.1)(?::|$)/.test(host)
      ? "http"
      : "https";
  try {
    return new URL(`${protocol}://${host}`);
  } catch {
    return new URL(FALLBACK_ORIGIN);
  }
}

export async function generateMetadata(): Promise<Metadata> {
  const origin = requestOrigin(await headers());
  const socialImage = new URL("/og.png", origin).toString();
  return {
    metadataBase: origin,
    title: TITLE,
    description: DESCRIPTION,
    openGraph: {
      title: TITLE,
      description: "Khám phá dữ liệu XSMB, XSMN và XSMT bằng lịch sử có phiên bản và backtest không nhìn trước.",
      type: "website",
      locale: "vi_VN",
      url: origin,
      images: [{ url: socialImage, width: 1735, height: 907, alt: "Lôtô Lab analytics data grid" }],
    },
    twitter: {
      card: "summary_large_image",
      title: TITLE,
      description: "Dashboard mô tả dữ liệu xổ số ba miền với Explorer và backtest minh bạch.",
      images: [socialImage],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body>
    </html>
  );
}
