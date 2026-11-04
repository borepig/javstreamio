import "./globals.css";

export const metadata = {
  title: "Stremio JAV Database Addon",
  description:
    "Stremio JAV Database Addon. Free tier: newest 15 titles per category + Torbox/Real-Debrid. Paid key unlocks the full 41k library and all debrid services.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </head>
      <body>{children}</body>
    </html>
  );
}
