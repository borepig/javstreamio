import "./globals.css";

export const metadata = {
  title: "Stremio JAV Database Addon",
  description: "Stremio JAV Database Addon. Buy me a coffee to support the hosting server.",
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
