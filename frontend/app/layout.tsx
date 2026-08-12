import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Cold Email Agent Dashboard',
  description: 'Autonomous cold email outreach pipeline & review dashboard',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
