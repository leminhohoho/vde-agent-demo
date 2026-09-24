import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ARTIFACT_EXACT, ARTIFACT_HREF_PREFIX, ARTIFACT_SPLIT } from "../ui/artifacts";
import { ArtifactLink } from "./ArtifactLink";

/** Minimal mdast node shape used by the artifact-link transform. */
interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

/** Rewrite `ds_/ch_/rp_` ids in text (and inline code that is exactly an id) into links. */
function linkArtifacts(node: MdNode): void {
  if (!node.children || node.type === "link" || node.type === "linkReference") return;
  const out: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && child.value) {
      child.value.split(ARTIFACT_SPLIT).forEach((part, i) => {
        if (i % 2 === 1) {
          const text: MdNode = { type: "text", value: part };
          out.push({ type: "link", url: ARTIFACT_HREF_PREFIX + part, children: [text] });
        } else if (part) {
          out.push({ type: "text", value: part });
        }
      });
    } else if (child.type === "inlineCode" && child.value && ARTIFACT_EXACT.test(child.value)) {
      out.push({ type: "link", url: ARTIFACT_HREF_PREFIX + child.value, children: [child] });
    } else {
      linkArtifacts(child);
      out.push(child);
    }
  }
  node.children = out;
}

function remarkArtifactLinks() {
  return (tree: MdNode) => linkArtifacts(tree);
}

const components: Components = {
  a: ({ href, children }) => {
    if (href?.startsWith(ARTIFACT_HREF_PREFIX)) {
      return <ArtifactLink id={href.slice(ARTIFACT_HREF_PREFIX.length)}>{children}</ArtifactLink>;
    }
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
  table: ({ children }) => (
    <div className="md-table-wrap">
      <table>{children}</table>
    </div>
  ),
};

const remarkPlugins = [remarkGfm, remarkArtifactLinks];

/** GitHub-flavoured markdown with artifact ids rendered as Inspector links. */
export function MarkdownText({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className ? `markdown ${className}` : "markdown"}>
      <Markdown remarkPlugins={remarkPlugins} components={components}>
        {text}
      </Markdown>
    </div>
  );
}
