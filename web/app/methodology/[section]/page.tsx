import { notFound } from "next/navigation";
import { getMethodologySections } from "../data";
import { TOC } from "../toc";

// The archive is frozen and the models only change on a rerun, so this route
// is prerendered and revalidated on a timer rather than queried per request.
export const revalidate = 3600;

export async function generateStaticParams() {
  return TOC.flatMap((t) => t.sections.map((s) => ({ section: s.id })));
}

export default async function MethodologySectionPage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  const sections = await getMethodologySections();
  const node = sections[section];
  if (!node) notFound();
  return node;
}
