import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Contract Keeper Commissioner",
  description: "Commissioner software for contract keeper leagues",
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
