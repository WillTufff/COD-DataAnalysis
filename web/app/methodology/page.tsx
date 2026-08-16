import { redirect } from "next/navigation";
import { TOC } from "./toc";

// /methodology has no content of its own — it lands on the first section of
// the first tier so the URL always resolves to a real page.
export default function MethodologyIndexPage() {
  redirect(`/methodology/${TOC[0].sections[0].id}`);
}
