import "./globals.css";
import { Inter, Space_Grotesk } from "next/font/google";

const inter = Inter({ subsets: ["latin"], weight: ["400", "500", "600", "700"], display: "swap" });
const grotesk = Space_Grotesk({ subsets: ["latin"], weight: ["500", "700"], display: "swap" });

export const metadata = {
  title: "Stremio JAV Database Addon",
  description:
    "Stremio JAV Database Addon. Free tier: newest 15 titles per category, all debrid services. Paid key: full 41k library, meta refreshed daily.",
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      style={{
        "--font-body": inter.style.fontFamily,
        "--font-display": grotesk.style.fontFamily,
      }}
    >
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>{children}</body>
    </html>
  );
}
