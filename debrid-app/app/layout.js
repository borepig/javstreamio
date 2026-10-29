import "./globals.css";

export const metadata = {
  title: "JAV StreamIO — Debrid Resolver",
  description: "Resolve JAV magnet/infoHash to direct debrid URLs (Torbox, Real-Debrid, AllDebrid, Torrentio, Pretorx)",
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
