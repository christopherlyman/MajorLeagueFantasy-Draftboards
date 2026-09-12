import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Commissioner Tools | Major League Fantasy",
  description: "Fantasy league commissioner tools for redraft, keeper, dynasty, and contract leagues",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
