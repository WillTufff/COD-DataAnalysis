import type { Metadata } from "next";
import { MethodologyToc } from "./MethodologyToc";

export const metadata: Metadata = { title: "Methodology" };

// A fixed docs shell, not a scrolling page: the masthead is pinned under the
// site header, and the tier nav and content pane each scroll on their own.
export default function MethodologyLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[1600px] px-6">
      <MethodologyToc>{children}</MethodologyToc>
    </div>
  );
}
